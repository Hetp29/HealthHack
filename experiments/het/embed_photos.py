"""Embed every CGMacros meal photo with a frozen, pretrained CLIP image encoder.

No image model is trained here: 1.7k meals is far too few to fine-tune a vision
network. CLIP turns each photo into a 512-d vector describing what is on the plate,
plus zero-shot similarity scores against plain-English food prompts (interpretable).

Output (gitignored): data/local/het_experiments/photo_embeddings.npz
    keys:  "CGMacros-014/photos/00000005-PHOTO-....jpg"  (matches meals.image_ref)
    emb:   float32 [n, 512], L2-normalised
    zs:    float32 [n, len(FOOD_PROMPTS)] softmax-free cosine similarities

Usage:  python -m experiments.het.embed_photos
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import open_clip
import torch
from PIL import Image, ImageOps

ROOT = Path("data/official_raw/cgmacros_extracted/CGMacros")
OUT = Path("data/local/het_experiments/photo_embeddings.npz")
MODEL, PRETRAINED = "ViT-B-32", "laion2b_s34b_b79k"

# Food categories that plausibly change the glycemic response at equal macros.
FOOD_PROMPTS = (
    "white rice", "brown rice", "bread or toast", "pasta or noodles", "pizza",
    "a sandwich or burger", "tortilla, tacos or burrito", "potatoes or fries",
    "breakfast cereal or oatmeal", "pancakes, waffles or pastry", "fresh fruit",
    "a salad or vegetables", "beans or lentils", "eggs", "meat, chicken or fish",
    "cheese or dairy", "yogurt", "a sugary drink or juice", "dessert, cake or sweets",
    "nuts", "a protein shake or smoothie", "an empty plate",
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model, _, preprocess = open_clip.create_model_and_transforms(MODEL, pretrained=PRETRAINED, device=device)
    tokenizer = open_clip.get_tokenizer(MODEL)
    model.eval()

    paths = sorted(ROOT.glob("CGMacros-*/photos/*"))
    paths = [p for p in paths if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    print(f"{len(paths)} photos on {device}")

    with torch.no_grad():
        text = tokenizer([f"a photo of {p}" for p in FOOD_PROMPTS]).to(device)
        text_emb = model.encode_text(text)
        text_emb = text_emb / text_emb.norm(dim=-1, keepdim=True)

    keys, embs, bad = [], [], []
    t0 = time.time()
    for i in range(0, len(paths), args.batch):
        batch, batch_keys = [], []
        for p in paths[i:i + args.batch]:
            try:
                img = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
            except Exception as exc:  # corrupt / unreadable photo: record, skip
                bad.append(f"{p}: {exc}")
                continue
            batch.append(preprocess(img))
            batch_keys.append(str(p.relative_to(ROOT)))
        if not batch:
            continue
        with torch.no_grad():
            e = model.encode_image(torch.stack(batch).to(device))
            e = e / e.norm(dim=-1, keepdim=True)
        embs.append(e.float().cpu().numpy())
        keys.extend(batch_keys)
        if (i // args.batch) % 20 == 0:
            print(f"  {len(keys)}/{len(paths)}  {time.time() - t0:.0f}s", flush=True)

    emb = np.concatenate(embs).astype("float32")
    zs = (emb @ text_emb.float().cpu().numpy().T).astype("float32")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT, keys=np.array(keys), emb=emb, zs=zs, prompts=np.array(FOOD_PROMPTS),
                        model=np.array(f"{MODEL}/{PRETRAINED}"))
    print(f"wrote {OUT}: {emb.shape}, unreadable={len(bad)}, {time.time() - t0:.0f}s")
    for b in bad[:10]:
        print("  unreadable:", b)


if __name__ == "__main__":
    main()
