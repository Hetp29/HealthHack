"""Verified loader for the three CGMacros biological metadata tables.

Every fact asserted here was checked against `DataDictionary.pdf` and against the
files themselves in Phase 1. Field meanings are NOT inferred from column names.

THE TWO FINDINGS THAT CONSTRAIN EVERY DOWNSTREAM EXPERIMENT
-----------------------------------------------------------
1. `microbes.csv` is **binary presence/absence**, not counts and not abundances.
   The dictionary is explicit and unambiguous for every taxon column: "Binary:
   0 = No, 1 = Yes -- Indicator variable of presence of bacteria in microbiome
   sample". Verified: the set of distinct values over all 45 x 1979 cells is
   exactly {0, 1}. Consequences, which are not negotiable:
     * richness (count of taxa present) IS computable;
     * Shannon / Simpson / evenness are NOT computable -- they need abundances;
     * centred log-ratio and other compositional transforms are NOT appropriate --
       there is no composition, only an incidence vector;
     * "relative abundance of a microbial group" is NOT available. The honest
       analogue is a within-group PREVALENCE FRACTION, which is what we build, and
       it is named `prev_` and never `abundance_`;
     * Jaccard is the natural similarity for incidence vectors, so that is the
       kernel used rather than Bray-Curtis or Aitchison.

2. `A1c PDL (Lab)` is in **% (NGSP/DCCT)**, and the data dictionary's stated unit
   is an error. The dictionary says "Range of hbA1c reading taken at start of
   study in mmol/mol" while giving the range as 4.6-8.5 in the same sentence.
   4.6-8.5 mmol/mol is biologically impossible -- the non-diabetic reference range
   is ~20-42 mmol/mol, so every participant would fall far below the healthy
   floor, in a cohort whose fasting lab glucose reaches 218 mg/dL. Read as NGSP %
   the values are coherent: Spearman rho = 0.875 against fasting lab glucose, and
   the ADA thresholds split the cohort 15 normal / 16 prediabetes / 14 diabetes,
   which matches the recruitment design CGMacros describes. Treated as %.

PARTICIPANT LINKAGE (verified, Phase 1)
---------------------------------------
45 CGM participant directories. `bio.csv` and `microbes.csv` both contain exactly
that set of 45 subject ids -- note the ids are NOT 1..45: 24, 25, 37 and 40 are
absent and the ids run to 49. `gut_health_test.csv` is a SUPERSET with 47 rows: it
adds subjects 24 and 25, which have no CGM data at all and are dropped here.

MISSINGNESS (verified, Phase 1)
-------------------------------
bio.csv         : complete, 45/45 on every field used.
microbes.csv    : subject 48 is entirely missing (all 1979 cells NaN) -> that
                  participant gets NaN microbiome features, never an imputed zero,
                  because "no sample" and "no bacteria" are different claims.
gut_health_test : subjects 26, 28 and 48 are entirely missing (all 22 cells NaN).

Metadata are participant-level and were collected at study start, i.e. strictly
BEFORE every prediction episode, so they introduce no temporal leakage. They do
introduce participant-identity leakage in the within-person regime, which is why
cold-start is the regime that decides.
"""

from __future__ import annotations

import functools
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(
    "data/cgmacros-a-scientific-dataset-for-personalized-nutrition-and-diet-monitoring-1.0.0/CGMacros"
)

#: Participant ids in the warehouse are zero-padded; bio.csv keys on a bare int.
def participant_id(subject: int) -> str:
    return f"cgmacros_{int(subject):03d}"


# --- Phase 2: metabolic fields, with the verified unit for each ------------- #
#: name -> (column in bio.csv, unit, evidence level)
BIO_FIELDS: dict[str, tuple[str, str, str]] = {
    "m_age": ("Age", "years", "A"),
    "m_bmi": ("BMI", "kg/m^2", "A"),
    "m_a1c": ("A1c PDL (Lab)", "% (NGSP) -- see module docstring", "A"),
    "m_fasting_glucose": ("Fasting GLU - PDL (Lab)", "mg/dL", "A"),
    "m_fasting_insulin": ("Insulin", "mcU/mL", "A"),
    "m_triglycerides": ("Triglycerides", "mg/dL", "A"),
    "m_cholesterol": ("Cholesterol", "mg/dL", "A"),
    "m_hdl": ("HDL", "mg/dL", "A"),
    "m_ldl": ("LDL (Cal)", "mg/dL -- CALCULATED, not directly measured", "A"),
    "m_chol_hdl_ratio": ("Cho/HDL Ratio", "ratio", "A"),
}

#: Prespecified metabolic groups (Phase 2C). Fixed BEFORE any scoring.
METABOLIC_GROUPS: dict[str, tuple[str, ...]] = {
    "glycemic": ("m_a1c", "m_fasting_glucose"),
    "insulin_axis": ("m_fasting_insulin", "m_homa_ir"),
    "anthropometric": ("m_bmi", "m_age"),
    "lipid": ("m_triglycerides", "m_hdl", "m_chol_hdl_ratio"),
    "core4": ("m_a1c", "m_bmi", "m_homa_ir", "m_age"),
}


@functools.lru_cache(maxsize=1)
def metabolic_table() -> pd.DataFrame:
    """Participant-level metabolic metadata, indexed by warehouse participant id."""
    raw = pd.read_csv(ROOT / "bio.csv")
    raw.columns = [c.strip() for c in raw.columns]
    out = pd.DataFrame(index=[participant_id(s) for s in raw["subject"]])
    for name, (col, _unit, _lvl) in BIO_FIELDS.items():
        out[name] = pd.to_numeric(raw[col], errors="coerce").to_numpy()
    # HOMA-IR: the standard surrogate index. A SURROGATE computed from two fasting
    # lab values -- NOT a clamp-measured insulin sensitivity.
    out["m_homa_ir"] = out["m_fasting_glucose"] * out["m_fasting_insulin"] / 405.0
    # ADA cohort from the measured lab A1c (not inferred from glucose traces).
    out["m_cohort_ord"] = np.where(
        out["m_a1c"] < 5.7, 0.0, np.where(out["m_a1c"] < 6.5, 1.0, 2.0)
    )
    out["m_sex_female"] = (raw["Gender"].str.strip().str.upper() == "F").astype(float).to_numpy()
    return out


# --- Phase 3: microbiome incidence ----------------------------------------- #
#: Genera prespecified from the published glycaemia literature, fixed BEFORE
#: scoring. Matched on the leading genus token of each taxon name.
FUNCTIONAL_GROUPS: dict[str, tuple[str, ...]] = {
    # short-chain fatty acid / butyrate producers, repeatedly associated with
    # improved postprandial glycaemia
    "scfa_butyrate": ("Faecalibacterium", "Roseburia", "Eubacterium", "Butyrivibrio",
                      "Coprococcus", "Anaerostipes", "Butyricicoccus"),
    # propionate / succinate route
    "propionate": ("Bacteroides", "Prevotella", "Akkermansia", "Dialister", "Veillonella"),
    # lactate producers
    "lactate": ("Lactobacillus", "Bifidobacterium", "Streptococcus", "Enterococcus"),
    # bile-salt hydrolase / secondary bile acids
    "bile_acid": ("Clostridium", "Blautia", "Ruminococcus", "Dorea"),
    # facultative anaerobes enriched in dysbiosis / inflammation
    "proteobacteria_like": ("Escherichia", "Klebsiella", "Enterobacter", "Citrobacter",
                            "Proteus", "Shigella", "Salmonella"),
    # mucin degraders
    "mucin": ("Akkermansia", "Ruminococcus", "Bacteroides"),
}


@functools.lru_cache(maxsize=1)
def microbiome_incidence() -> pd.DataFrame:
    """Binary taxon incidence, 45 x 1979, indexed by participant id.

    Subject 48's row is all-NaN in the source and is preserved as NaN.
    """
    raw = pd.read_csv(ROOT / "microbes.csv")
    raw.columns = [c.strip().lstrip("﻿") for c in raw.columns]
    taxa = [c for c in raw.columns if c != "subject"]
    out = raw[taxa].copy()
    out.index = [participant_id(s) for s in raw["subject"]]
    out.columns = [c.strip() for c in taxa]
    return out


@functools.lru_cache(maxsize=1)
def gut_health() -> pd.DataFrame:
    """22 vendor-scored ordinal gut-health pathway variables.

    The dictionary defines these as `Factor: 1 = Not Optimal, 2 = Average,
    3 = Good` -- percentile-derived ORDINAL scores, not measurements in any
    physical unit. Verified: distinct values over all cells are exactly {1, 2, 3}.
    Subjects 24 and 25 are dropped (no CGM data); 26, 28 and 48 are all-NaN.
    """
    raw = pd.read_csv(ROOT / "gut_health_test.csv")
    raw.columns = [c.strip().lstrip("﻿") for c in raw.columns]
    keep = [c for c in raw.columns if c != "subject"]
    out = raw[keep].copy()
    out.index = [participant_id(s) for s in raw["subject"]]
    out.columns = [f"h_{c.strip().lower().replace(' ', '_').replace(',', '')}" for c in keep]
    valid = set(metabolic_table().index)
    return out.loc[[i for i in out.index if i in valid]]


def genus_of(taxon: str) -> str:
    return taxon.strip().split()[0] if taxon.strip() else ""


def richness(incidence: pd.DataFrame) -> pd.Series:
    """Observed taxon richness. The ONLY diversity index the binary scale supports.

    Shannon, Simpson and evenness all require abundances and are therefore not
    computed anywhere in this project; see the module docstring.
    """
    present = incidence.sum(axis=1, skipna=False)
    return present


def prevalence_fractions(incidence: pd.DataFrame,
                         groups: dict[str, tuple[str, ...]]) -> pd.DataFrame:
    """Fraction of a group's taxa that are PRESENT in each participant.

    Named `prev_`, never `abundance_`: with incidence data this is a prevalence
    fraction and carries no information about how much of each taxon is there.
    """
    genera = np.array([genus_of(c) for c in incidence.columns])
    out = {}
    for name, members in groups.items():
        mask = np.isin(genera, members)
        if not mask.any():
            continue
        block = incidence.loc[:, mask]
        out[f"prev_{name}"] = block.mean(axis=1, skipna=False)
        out[f"n_{name}"] = block.sum(axis=1, skipna=False)
    return pd.DataFrame(out, index=incidence.index)


def jaccard_matrix(incidence: pd.DataFrame) -> pd.DataFrame:
    """Pairwise Jaccard similarity -- the correct measure for incidence vectors."""
    x = incidence.to_numpy("float64")
    ok = np.isfinite(x).all(axis=1)
    inter = x[ok] @ x[ok].T
    size = x[ok].sum(axis=1)
    union = size[:, None] + size[None, :] - inter
    with np.errstate(invalid="ignore", divide="ignore"):
        sim = np.where(union > 0, inter / union, 0.0)
    return pd.DataFrame(sim, index=incidence.index[ok], columns=incidence.index[ok])


if __name__ == "__main__":
    mt, mi, gh = metabolic_table(), microbiome_incidence(), gut_health()
    print("=== verified biological metadata dictionary ===")
    print(f"metabolic   : {mt.shape[0]} participants x {mt.shape[1]} fields, "
          f"missing cells {int(mt.isna().sum().sum())}")
    print(f"microbiome  : {mi.shape[0]} x {mi.shape[1]} binary taxa, "
          f"all-NaN participants {[i for i in mi.index if mi.loc[i].isna().all()]}")
    print(f"gut health  : {gh.shape[0]} x {gh.shape[1]} ordinal scores, "
          f"all-NaN participants {[i for i in gh.index if gh.loc[i].isna().all()]}")
    r = richness(mi)
    print(f"richness    : median {r.median():.0f}, range {r.min():.0f}-{r.max():.0f} "
          f"(NaN for {int(r.isna().sum())})")
    pf = prevalence_fractions(mi, FUNCTIONAL_GROUPS)
    print(f"prevalence  : {list(pf.columns)}")
    print(pf.describe().loc[["mean", "min", "max"]].round(3).to_string())
    j = jaccard_matrix(mi)
    off = j.to_numpy()[~np.eye(len(j), dtype=bool)]
    print(f"jaccard     : {j.shape}, off-diagonal mean {off.mean():.3f} "
          f"range {off.min():.3f}-{off.max():.3f}")
