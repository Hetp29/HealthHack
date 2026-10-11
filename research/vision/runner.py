"""Meal-image and food-composition experiments (Phases One, Two, Three, Five).

    python -m research.vision.runner --db-url sqlite:///data/local/cgmacros_v2.db

TWO EVALUATION SCOPES, BOTH REPORTED
------------------------------------
Image coverage is 1126 of the 1171 development episodes (96.2%), so the
image-bearing subset is nearly the whole pool -- but "nearly" is not "exactly",
and a candidate must not be credited for an easier subset. Hence:

  FULL    the seven established folds, unchanged, all 1171 episodes. Episodes
          without a photograph receive NaN image features, which is the
          image-missing fallback path, and fs-1.3 is scored on the same episodes.
          This is the primary, prospective-style comparison.
  COVERED folds rebuilt on the 1126 image-eligible episodes only, with fs-1.3
          re-scored on exactly those meals. This answers the narrower question
          "given a photograph, does the photograph help?" without dilution.

Only photographs VERIFIED as taken at the meal timestamp are used. The 1548
end-of-meal follow-up photographs (median 16 minutes AFTER the meal) are excluded
from both scopes: they postdate the prediction, and they encode how much was left
on the plate.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import glob
import json
import pathlib
import sqlite3
import time

import numpy as np
import pandas as pd

from ml.config import DEFAULT_CONFIG, HORIZONS
from ml.devvalidation import Candidate, participant_holdout_folds, rolling_folds
from research.biometa.common import fit_blend, row
from research.vision.encoders import BACKBONES, embed_paths, food_logit_features
from research.vision.features import (
    FOOD_PROMPTS,
    Pca,
    composition_features,
    image_retrieval_features,
    meal_type_features,
    prior_response_index,
)
from research.vision.inventory import ROOT, normalize_meal_type

RESULTS = pathlib.Path("research/vision/results")
SHUFFLE_SEED = 20261011


# --------------------------------------------------------------------------- #
# Static per-episode blocks (no fold fitting required)
# --------------------------------------------------------------------------- #
def load_meal_types() -> dict[tuple[str, dt.datetime], str]:
    out = {}
    for path in sorted(glob.glob(str(ROOT / "CGMacros-*" / "CGMacros-*.csv"))):
        pid = f"cgmacros_{int(pathlib.Path(path).parent.name.split('-')[-1]):03d}"
        for r in csv.DictReader(open(path)):
            mt = normalize_meal_type(r.get("Meal Type", ""))
            if not mt:
                continue
            try:
                stamp = dt.datetime.strptime(r["Timestamp"].strip(), "%Y-%m-%d %H:%M:%S")
            except (ValueError, KeyError):
                continue
            out[(pid, stamp)] = mt
    return out


def load_macros(db_url: str):
    con = sqlite3.connect(db_url.replace("sqlite:///", ""))
    rows = list(con.execute(
        "SELECT participant_id, meal_id, timestamp, carbs_g, protein_g, fat_g, "
        "fiber_g, calories_kcal FROM meals ORDER BY participant_id, timestamp"))
    by_key, by_participant = {}, {}
    for pid, mid, ts, c, p, f, fb, kcal in rows:
        stamp = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=dt.timezone.utc)
        by_key[(pid, mid)] = {"carbs": c, "protein": p, "fat": f, "fiber": fb,
                              "calories": kcal, "timestamp": stamp}
        by_participant.setdefault(pid, []).append({"timestamp": stamp, "meal_id": mid})
    return by_key, by_participant


class Block:
    """A per-episode feature block. `fit` sees only the fold's training episodes."""

    needs_fit = False

    def fit(self, keys: list[tuple[str, str]]) -> "Block":
        return self

    def columns(self, keys: list[tuple[str, str]]) -> pd.DataFrame:
        raise NotImplementedError


class Static(Block):
    """A precomputed frame indexed by (participant_id, meal_id)."""

    def __init__(self, frame: pd.DataFrame, name: str):
        self.frame, self.name = frame, name

    def columns(self, keys):
        out = self.frame.reindex(pd.MultiIndex.from_tuples(keys))
        out.index = range(len(out))
        return out


class Embedding(Block):
    """Frozen embedding reduced by TRAINING-FOLD-ONLY PCA.

    `permute` implements the two image-shuffling controls:
      "meals"        -- permute the embedding->meal assignment among the fold's
                        TRAINING meals, across participants. Destroys which
                        photograph belongs to which meal. Validation keeps its true
                        photographs, so a candidate that still wins was not using
                        genuine image-meal correspondence.
      "participants" -- permute WITHIN each participant's own training meals.
                        Destroys which of that person's meals a photograph shows
                        while preserving whose meal it is, isolating meal-specific
                        image content from participant-identity content.
    """

    needs_fit = True

    def __init__(self, table: dict, k: int, name: str, permute: str | None = None):
        self.table, self.k, self.name, self.permute = table, k, name, permute
        self.pca: Pca | None = None
        self._remap: dict = {}
        self._dim = len(next(iter(table.values()))) if table else 0

    def fit(self, keys):
        present = [k for k in keys if k in self.table]
        if not present:
            self.pca = None
            return self
        X = np.stack([self.table[k] for k in present]).astype("float64")
        self.pca = Pca(min(self.k, X.shape[0] - 1, X.shape[1])).fit(X)
        self._remap = {}
        if self.permute:
            rng = np.random.default_rng(SHUFFLE_SEED)
            if self.permute == "meals":
                shuffled = list(present)
                rng.shuffle(shuffled)
                self._remap = dict(zip(present, shuffled))
            else:
                by_p: dict[str, list] = {}
                for k in present:
                    by_p.setdefault(k[0], []).append(k)
                for pid, group in by_p.items():
                    shuffled = list(group)
                    rng.shuffle(shuffled)
                    self._remap.update(dict(zip(group, shuffled)))
        return self

    def columns(self, keys):
        cols = [f"{self.name}_{i}" for i in range(self.pca.k if self.pca else self.k)]
        out = np.full((len(keys), len(cols)), np.nan)
        if self.pca is None:
            return pd.DataFrame(out, columns=cols)
        for i, k in enumerate(keys):
            src = self._remap.get(k, k)
            v = self.table.get(src)
            if v is not None:
                out[i] = self.pca.transform(np.asarray(v, dtype="float64")[None, :])[0]
        return pd.DataFrame(out, columns=cols)


def candidate(name: str, blocks: list[Block], notes: str = "") -> Candidate:
    def run(train, validate, config):
        tr_keys = list(zip(train.participant_ids, train.meal_ids))
        va_keys = list(zip(validate.participant_ids, validate.meal_ids))
        X_tr = [train.features.reset_index(drop=True)]
        X_va = [validate.features.reset_index(drop=True)]
        for b in blocks:
            if b.needs_fit:
                b.fit(tr_keys)
            X_tr.append(b.columns(tr_keys))
            X_va.append(b.columns(va_keys))
        X_tr, X_va = pd.concat(X_tr, axis=1), pd.concat(X_va, axis=1)
        blend, pers = fit_blend(X_tr, train, config)
        return pers.apply(validate.participant_ids, blend.predict_deltas(X_va))

    return Candidate(name, run, notes)


def macros_only() -> Candidate:
    """A6: macronutrients ONLY, no glucose history at all.

    An interpretive control, not a competitor: it measures how much of the
    response the logged macros explain on their own, which is the yardstick any
    food-level feature has to beat.
    """
    cols = ["carbs_g", "protein_g", "fat_g", "fiber_g", "calories_kcal"]

    def run(train, validate, config):
        blend, pers = fit_blend(train.features[cols], train, config)
        return pers.apply(validate.participant_ids,
                          blend.predict_deltas(validate.features[cols]))

    return Candidate("A6 CONTROL macronutrients only (no CGM)", run,
                     "interpretive yardstick")


def build(dev, db_url, backbones: dict, prompts_sim, retrieval, logits_frame,
          meal_types, macros_by_key) -> list[Candidate]:
    keys = [(e.participant_id, e.meal_id) for e in dev]
    idx = pd.MultiIndex.from_tuples(keys)

    avail = pd.DataFrame(
        {"img_available": [1.0 if k in backbones["resnet18"] else 0.0 for k in keys]},
        index=idx)
    comp = composition_features(
        *[[macros_by_key.get(k, {}).get(f) for k in keys]
          for f in ("carbs", "protein", "fat", "fiber", "calories")])
    comp.index = idx
    mtypes = meal_type_features(
        [meal_types.get((e.participant_id, e.meal_timestamp.replace(tzinfo=None)), "unknown")
         for e in dev])
    mtypes.index = idx
    logits_frame.index = idx
    retrieval.index = idx

    c: list[Candidate] = [
        candidate("A0 fs-1.3 baseline", [], "reference"),
        candidate("A1 + image-availability indicator", [Static(avail, "avail")],
                  "one binary column; tests whether HAVING a photo is informative"),
        candidate("A2 CONTROL random-weight backbone (PCA16)",
                  [Embedding(backbones["random_resnet18"], 16, "rnd")],
                  "same architecture and preprocessing, untrained weights"),
        candidate("A3 + ResNet-18 embeddings (PCA16)",
                  [Embedding(backbones["resnet18"], 16, "r18")], "ImageNet-1k, frozen"),
        candidate("A4 CONTROL embeddings permuted among training meals",
                  [Embedding(backbones["resnet18"], 16, "r18", permute="meals")],
                  "destroys image<->meal correspondence"),
        candidate("A5 CONTROL embeddings permuted within participants",
                  [Embedding(backbones["resnet18"], 16, "r18", permute="participants")],
                  "destroys which meal, preserves which person"),
        macros_only(),
        candidate("A7 + source Meal Type one-hot", [Static(mtypes, "mt")],
                  "the ONLY source-observed food categorisation that exists"),
        candidate("A8 + enriched composition (source macros only)", [Static(comp, "fc")],
                  "fractions/ratios/energy density; NO external table"),
    ]
    for b, k in (("efficientnet_b0", 16), ("clip_vit_b32", 16)):
        if b in backbones:
            c.append(candidate(f"C + {BACKBONES[b]['architecture']} (PCA16)",
                               [Embedding(backbones[b], k, b[:6])],
                               BACKBONES[b]["weights"]))
    for k in (8, 32):
        c.append(candidate(f"C4 ResNet-18 PCA{k}",
                           [Embedding(backbones["resnet18"], k, "r18")],
                           "PCA dimensionality sweep"))
    if prompts_sim is not None:
        prompts_sim.index = idx
        c.append(candidate("C5/E3 CLIP food-text similarity", [Static(prompts_sim, "clip")],
                           "zero-shot similarity to coarse appearance prompts"))
    c.append(candidate("E2 ImageNet food-class posteriors", [Static(logits_frame, "lg")],
                       "pretrained classifier's own food-class outputs"))
    c.append(candidate("C6/E5 image-similar own-meal retrieval", [Static(retrieval, "iq")],
                       "own past meals ranked by photo similarity"))
    c.append(candidate("E6 ResNet-18 + Meal Type",
                       [Embedding(backbones["resnet18"], 16, "r18"), Static(mtypes, "mt")],
                       "vision + source category"))
    c.append(candidate("E7 ResNet-18 + enriched composition",
                       [Embedding(backbones["resnet18"], 16, "r18"), Static(comp, "fc")],
                       "vision + source-macro composition"))
    c.append(candidate("E7b composition + Meal Type + food posteriors",
                       [Static(comp, "fc"), Static(mtypes, "mt"), Static(logits_frame, "lg")],
                       "all non-embedding food information at once"))
    return c


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--scope", default="both", choices=("full", "covered", "both"))
    args = ap.parse_args()

    from ml.experiments import load_dev_pool
    from research.batch_b.sequences import load_series

    dev, _ = load_dev_pool(args.db_url, DEFAULT_CONFIG)
    elig = json.loads((RESULTS / "eligible_images.json").read_text())
    path_of = {(p, m): f for p, m, f in elig["eligible"]}
    print(f"dev episodes {len(dev)}, image-eligible {len(path_of)} "
          f"({len(path_of)/len(dev):.1%})")

    ordered = [(k, path_of[k]) for k in
               [(e.participant_id, e.meal_id) for e in dev] if k in path_of]
    keys_with_image = [k for k, _ in ordered]
    paths = [p for _, p in ordered]

    backbones: dict[str, dict] = {}
    enc_stats = []
    for b in ("resnet18", "efficientnet_b0", "clip_vit_b32", "random_resnet18"):
        try:
            emb, logits, stats = embed_paths(b, paths, verbose=False)
        except Exception as exc:                                       # noqa: BLE001
            print(f"  {b}: UNAVAILABLE -- {type(exc).__name__}: {exc}")
            continue
        backbones[b] = dict(zip(keys_with_image, emb))
        enc_stats.append(stats)
        print(f"  {b}: {emb.shape} ({stats['cpu_ms_per_image'] or 'cached'} ms/img)")
        if b == "resnet18" and logits is not None:
            lg, lg_names = food_logit_features(logits)
            logits_frame = pd.DataFrame(np.nan, index=range(len(dev)), columns=lg_names)
            pos = {k: i for i, k in enumerate(keys_with_image)}
            for i, e in enumerate(dev):
                j = pos.get((e.participant_id, e.meal_id))
                if j is not None:
                    logits_frame.iloc[i] = lg[j]

    # CLIP zero-shot appearance similarity
    prompts_sim = None
    if "clip_vit_b32" in backbones:
        from research.vision.encoders import FrozenEncoder

        clip = FrozenEncoder("clip_vit_b32")
        names = sorted(FOOD_PROMPTS)
        text = np.stack([clip.embed_text(FOOD_PROMPTS[n]).mean(axis=0) for n in names])
        text = text / np.linalg.norm(text, axis=1, keepdims=True)
        sim = np.full((len(dev), len(names)), np.nan)
        pos = {k: i for i, k in enumerate(keys_with_image)}
        emb = np.stack([backbones["clip_vit_b32"][k] for k in keys_with_image])
        S = emb @ text.T
        for i, e in enumerate(dev):
            j = pos.get((e.participant_id, e.meal_id))
            if j is not None:
                sim[i] = S[j]
        prompts_sim = pd.DataFrame(sim, columns=[f"clipsim_{n}" for n in names])

    stamps, values = load_series(args.db_url)
    macros_by_key, meals_by_participant = load_macros(args.db_url)
    pindex = prior_response_index(stamps, values, meals_by_participant)
    retrieval = image_retrieval_features(dev, backbones.get("resnet18", {}), pindex)
    print(f"  image-similar retrieval: {retrieval['iq_n'].notna().mean():.1%} of episodes "
          f"have >=1 admissible photographed prior meal")

    meal_types = load_meal_types()
    cands = build(dev, args.db_url, backbones, prompts_sim, retrieval,
                  logits_frame, meal_types, macros_by_key)
    print(f"{len(cands)} configurations")

    all_rows: list[dict] = []
    scopes = []
    if args.scope in ("full", "both"):
        scopes.append(("full", dev))
    if args.scope in ("covered", "both"):
        covered = [e for e in dev if (e.participant_id, e.meal_id) in path_of]
        scopes.append(("covered", covered))

    from ml.devvalidation import score_candidate

    for scope_name, pool in scopes:
        for regime, folds in (("within_person", rolling_folds(pool, DEFAULT_CONFIG, 3)),
                              ("cold_start", participant_holdout_folds(pool, DEFAULT_CONFIG, 4))):
            print(f"\n--- {scope_name} / {regime} ({len(folds)} folds, "
                  f"{len(pool)} episodes) ---", flush=True)
            scored = {}
            for c in cands:
                t0 = time.perf_counter()
                scored[c.name] = score_candidate(folds, c, DEFAULT_CONFIG)
                print(f"    {c.name:50s} MAE {scored[c.name].mae:7.3f} "
                      f"({time.perf_counter()-t0:5.1f}s)", flush=True)
            ref = scored["A0 fs-1.3 baseline"]
            for n, r in scored.items():
                d = row(r, None if n == "A0 fs-1.3 baseline" else ref)
                d["regime"], d["scope"] = regime, scope_name
                all_rows.append(d)

    df = pd.DataFrame(all_rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    df.to_csv(RESULTS / "vision_leaderboard.csv", index=False)
    (RESULTS / "encoder_provenance.json").write_text(json.dumps(enc_stats, indent=2) + "\n")

    print("\n=== VISION / FOOD LEADERBOARD (delta>0 = better than fs-1.3) ===")
    for scope_name in df.scope.unique():
        for regime in ("within_person", "cold_start"):
            sub = df[(df.scope == scope_name) & (df.regime == regime)].sort_values(
                "delta", ascending=False, na_position="first")
            if sub.empty:
                continue
            print(f"\n{scope_name} / {regime}:")
            print(f"  {'candidate':50s}{'MAE':>8s}{'delta':>8s}{'95% CI':>20s}"
                  f"{'excl0':>7s}{'worst':>8s}")
            for _, r in sub.iterrows():
                dd = "   ref" if pd.isna(r["delta"]) else f"{r['delta']:+.3f}"
                ci = "" if pd.isna(r["ci_low"]) else f"[{r['ci_low']:+.3f},{r['ci_high']:+.3f}]"
                ex = "" if pd.isna(r["delta"]) else ("YES" if r["excludes_zero"] else "no")
                w = ("" if pd.isna(r.get("worst_participant_delta"))
                     else f"{r['worst_participant_delta']:+.2f}")
                print(f"  {r['model']:50s}{r['mae']:8.3f}{dd:>8s}{ci:>20s}{ex:>7s}{w:>8s}")
    print(f"\nwrote {RESULTS}/vision_leaderboard.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
