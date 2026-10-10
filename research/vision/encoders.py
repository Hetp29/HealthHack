"""Frozen pretrained vision encoders, with content-hash caching.

    python -m research.vision.encoders --backbone resnet18

PROVENANCE IS PART OF THE RESULT
--------------------------------
Every encoder records its name, architecture, weight enum, version and licence,
because "a frozen vision embedding" is not a reproducible object without them.
Weights are the published ImageNet-1k / LAION checkpoints downloaded from their
official hosts; none are trained here and none are random. If weights cannot be
obtained the experiment is reported as blocked -- a random-initialised backbone is
never labelled pretrained. (A random backbone IS run, deliberately, as control A2.)

Preprocessing is whatever the chosen checkpoint specifies, taken from the weight
enum's own transform rather than reimplemented, so a mismatch between training and
inference normalisation cannot creep in.

CACHING is keyed on the SHA-256 of the image bytes, so a duplicate photograph is
embedded once, and re-runs cost nothing. The cache lives in a gitignored local
workspace.

WHAT THESE EMBEDDINGS DO AND DO NOT ESTABLISH
---------------------------------------------
An ImageNet or LAION embedding is a generic visual descriptor. It is NOT evidence
that a depicted food has a particular nutrient composition, and no experiment here
treats it as such. The food-class logit features are the pretrained model's own
reported class posteriors over ImageNet's genuine food categories -- a model
capability, not a manual annotation and not a nutrient measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import time

import numpy as np
import torch

CACHE = pathlib.Path("data/local/vision/embeddings")

#: ImageNet-1k class indices that denote a food or drink. Fixed from the published
#: class list BEFORE any validation scoring; used for the food-category-logit
#: features (C5/E2) so that no manual food labelling of this dataset occurs.
IMAGENET_FOOD_CLASSES = {
    "fruit_veg": [936, 937, 938, 939, 940, 941, 943, 944, 945, 946, 947, 948, 949,
                  950, 951, 952, 953, 954, 955, 956, 957, 958, 959],
    "baked_grain": [930, 931, 932, 933, 934, 935],
    "prepared_dish": [923, 924, 925, 926, 927, 928, 929, 960, 961, 962, 963, 964, 965],
    "drink_vessel": [967, 968, 969, 647, 504, 725],
    "tableware": [809, 923, 532, 659],
}

BACKBONES = {
    "resnet18": {
        "architecture": "ResNet-18",
        "weights": "torchvision IMAGENET1K_V1",
        "licence": "BSD-3-Clause (torchvision); weights trained on ImageNet-1k",
        "source": "https://download.pytorch.org/models/resnet18-f37072fd.pth",
        "dim": 512,
    },
    "efficientnet_b0": {
        "architecture": "EfficientNet-B0",
        "weights": "torchvision IMAGENET1K_V1",
        "licence": "BSD-3-Clause (torchvision); weights trained on ImageNet-1k",
        "source": "https://download.pytorch.org/models/efficientnet_b0_rwightman-7f5810bc.pth",
        "dim": 1280,
    },
    "clip_vit_b32": {
        "architecture": "CLIP ViT-B/32",
        "weights": "open_clip laion2b_s34b_b79k",
        "licence": "MIT (open_clip); weights trained on LAION-2B",
        "source": "https://huggingface.co/laion/CLIP-ViT-B-32-laion2B-s34B-b79K",
        "dim": 512,
    },
    "random_resnet18": {
        "architecture": "ResNet-18",
        "weights": "NONE -- randomly initialised. CONTROL A2 ONLY.",
        "licence": "n/a",
        "source": "n/a",
        "dim": 512,
    },
}


def content_hash(path: str | pathlib.Path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


class FrozenEncoder:
    """A frozen backbone plus its own published preprocessing."""

    def __init__(self, name: str):
        if name not in BACKBONES:
            raise ValueError(f"unknown backbone {name!r}")
        self.name = name
        self.meta = dict(BACKBONES[name])
        self.kind = "clip" if name.startswith("clip") else "torchvision"
        self._build()

    def _build(self):
        if self.kind == "clip":
            import open_clip

            model, _, preprocess = open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="laion2b_s34b_b79k")
            model.eval()
            self.model, self.preprocess = model, preprocess
            self.tokenizer = open_clip.get_tokenizer("ViT-B-32")
            self.classifier = None
            return

        import torchvision.models as tvm

        if self.name == "resnet18":
            weights = tvm.ResNet18_Weights.IMAGENET1K_V1
            net = tvm.resnet18(weights=weights)
            self.preprocess = weights.transforms()
        elif self.name == "random_resnet18":
            net = tvm.resnet18(weights=None)
            # Use the SAME preprocessing as the pretrained control's counterpart so
            # the only difference is the weights.
            self.preprocess = tvm.ResNet18_Weights.IMAGENET1K_V1.transforms()
        else:
            weights = tvm.EfficientNet_B0_Weights.IMAGENET1K_V1
            net = tvm.efficientnet_b0(weights=weights)
            self.preprocess = weights.transforms()
        net.eval()
        # keep the classifier head so food-class logits are available
        self.classifier = net
        self.model = net

    @torch.no_grad()
    def embed_batch(self, images) -> tuple[np.ndarray, np.ndarray | None]:
        """(pooled embedding [n, dim], ImageNet logits [n, 1000] or None)."""
        batch = torch.stack([self.preprocess(im) for im in images])
        if self.kind == "clip":
            feats = self.model.encode_image(batch)
            feats = feats / feats.norm(dim=-1, keepdim=True)
            return feats.numpy().astype("float32"), None

        import torchvision.models as tvm

        net = self.model
        if isinstance(net, tvm.ResNet):
            x = net.conv1(batch); x = net.bn1(x); x = net.relu(x); x = net.maxpool(x)
            x = net.layer1(x); x = net.layer2(x); x = net.layer3(x); x = net.layer4(x)
            pooled = torch.flatten(net.avgpool(x), 1)
            logits = net.fc(pooled)
        else:
            x = net.features(batch)
            pooled = torch.flatten(net.avgpool(x), 1)
            logits = net.classifier(pooled)
        return pooled.numpy().astype("float32"), logits.numpy().astype("float32")

    @torch.no_grad()
    def embed_text(self, prompts: list[str]) -> np.ndarray:
        """CLIP only: normalised text embeddings for E4's food-description similarity."""
        if self.kind != "clip":
            raise RuntimeError("text embeddings require a CLIP backbone")
        tokens = self.tokenizer(prompts)
        feats = self.model.encode_text(tokens)
        return (feats / feats.norm(dim=-1, keepdim=True)).numpy().astype("float32")


def embed_paths(backbone: str, paths: list[str], batch_size: int = 16,
                verbose: bool = True) -> tuple[np.ndarray, np.ndarray | None, dict]:
    """Embed `paths`, reusing any cached vectors keyed by image content hash."""
    from PIL import Image

    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / f"{backbone}.npz"
    cached: dict[str, np.ndarray] = {}
    cached_logits: dict[str, np.ndarray] = {}
    if cache_file.exists():
        blob = np.load(cache_file, allow_pickle=False)
        keys = list(blob["keys"])
        cached = {k: blob["emb"][i] for i, k in enumerate(keys)}
        if "logits" in blob:
            cached_logits = {k: blob["logits"][i] for i, k in enumerate(keys)}

    hashes = [content_hash(p) for p in paths]
    todo = [i for i, h in enumerate(hashes) if h not in cached]
    encoder = None
    t0 = time.perf_counter()
    if todo:
        encoder = FrozenEncoder(backbone)
        for start in range(0, len(todo), batch_size):
            chunk = todo[start : start + batch_size]
            images = [Image.open(paths[i]).convert("RGB") for i in chunk]
            emb, logits = encoder.embed_batch(images)
            for j, i in enumerate(chunk):
                cached[hashes[i]] = emb[j]
                if logits is not None:
                    cached_logits[hashes[i]] = logits[j]
            if verbose and (start // batch_size) % 10 == 0:
                print(f"    {backbone}: {start + len(chunk)}/{len(todo)} new images",
                      flush=True)
        keys = sorted(cached)
        np.savez_compressed(
            cache_file, keys=np.array(keys),
            emb=np.stack([cached[k] for k in keys]),
            **({"logits": np.stack([cached_logits[k] for k in keys])}
               if cached_logits and len(cached_logits) == len(keys) else {}))
    elapsed = time.perf_counter() - t0

    emb = np.stack([cached[h] for h in hashes])
    logits = (np.stack([cached_logits[h] for h in hashes])
              if cached_logits and all(h in cached_logits for h in hashes) else None)
    stats = {
        "backbone": backbone, **BACKBONES[backbone],
        "n_requested": len(paths), "n_unique_by_content_hash": len(set(hashes)),
        "n_newly_embedded": len(todo), "cpu_seconds_new": round(elapsed, 1),
        "cpu_ms_per_image": round(1000 * elapsed / len(todo), 1) if todo else None,
        "cache": str(cache_file),
    }
    return emb, logits, stats


def food_logit_features(logits: np.ndarray) -> tuple[np.ndarray, list[str]]:
    """Aggregate ImageNet posteriors over the prespecified food class groups.

    These are the pretrained classifier's own outputs over ImageNet's genuine food
    categories. They are a model capability, not a manual annotation of this
    dataset, and they are not a nutrient measurement.
    """
    probs = torch.softmax(torch.from_numpy(logits), dim=-1).numpy()
    cols, names = [], []
    for group, idx in IMAGENET_FOOD_CLASSES.items():
        cols.append(probs[:, idx].sum(axis=1))
        names.append(f"img_p_{group}")
    food_all = sorted({i for v in IMAGENET_FOOD_CLASSES.values() for i in v})
    cols.append(probs[:, food_all].sum(axis=1))
    names.append("img_p_food_total")
    cols.append(probs.max(axis=1))
    names.append("img_top1_confidence")
    cols.append(-(probs * np.log(probs + 1e-12)).sum(axis=1))
    names.append("img_class_entropy")
    return np.column_stack(cols).astype("float64"), names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backbone", default="resnet18", choices=sorted(BACKBONES))
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    elig = json.loads(pathlib.Path("research/vision/results/eligible_images.json").read_text())
    paths = [p for _, _, p in elig["eligible"]]
    if args.limit:
        paths = paths[: args.limit]
    emb, logits, stats = embed_paths(args.backbone, paths)
    print(json.dumps(stats, indent=2))
    print(f"embeddings {emb.shape}, logits {None if logits is None else logits.shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
