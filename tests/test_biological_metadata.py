"""Guards on the Phase 1 biological-metadata findings.

These are not tests of model quality -- they are tests of the FACTS the Phase 2-5
experiments were built on. Each one failed once as a hypothesis before it was
checked, and each would silently invalidate a downstream conclusion if the data
or the loader changed. The two that matter most:

  * the microbiome table is binary presence/absence, which is what rules out
    Shannon/Simpson diversity and compositional log-ratios;
  * `A1c PDL (Lab)` is in NGSP %, contradicting the data dictionary's own stated
    unit of mmol/mol.

They are skipped, not failed, when the CGMacros archive is not present, so the
suite still runs on a machine without the raw data.
"""

from __future__ import annotations

import numpy as np
import pytest

metadata = pytest.importorskip("research.biometa.metadata")

pytestmark = pytest.mark.skipif(
    not (metadata.ROOT / "bio.csv").exists(),
    reason="CGMacros archive not extracted on this machine",
)


def test_microbiome_is_binary_presence_absence():
    """The whole Phase 3 design rests on this. Counts or abundances would change
    which analyses are legitimate."""
    inc = metadata.microbiome_incidence()
    values = set(np.unique(inc.to_numpy("float64")[np.isfinite(inc.to_numpy("float64"))]))
    assert values == {0.0, 1.0}, f"expected binary incidence, found {sorted(values)[:8]}"


def test_a1c_values_are_ngsp_percent_not_mmol_per_mol():
    """The data dictionary says mmol/mol. The values cannot be.

    4.6-8.5 mmol/mol would put every participant far below the non-diabetic
    reference floor (~20 mmol/mol) in a cohort whose fasting lab glucose reaches
    218 mg/dL. Read as NGSP % they are coherent and track fasting glucose.
    """
    table = metadata.metabolic_table()
    a1c = table["m_a1c"].dropna()
    assert 4.0 < a1c.min() and a1c.max() < 15.0, "outside any plausible NGSP % range"
    assert a1c.max() < 20.0, "a value this low cannot be mmol/mol"
    rho = table["m_a1c"].corr(table["m_fasting_glucose"], method="spearman")
    assert rho > 0.6, f"A1c should track fasting glucose; got rho={rho:.3f}"


def test_participant_linkage_is_exact_for_bio_and_microbes():
    bio = set(metadata.metabolic_table().index)
    mic = set(metadata.microbiome_incidence().index)
    assert len(bio) == 45
    assert bio == mic, "bio.csv and microbes.csv must cover the same participants"


def test_gut_health_is_a_superset_and_extras_are_dropped():
    """gut_health_test.csv ships 47 subjects; 24 and 25 have no CGM data at all."""
    gh = metadata.gut_health()
    bio = set(metadata.metabolic_table().index)
    assert set(gh.index) <= bio
    assert metadata.participant_id(24) not in gh.index
    assert metadata.participant_id(25) not in gh.index


def test_gut_health_scores_are_ordinal_one_to_three():
    gh = metadata.gut_health().to_numpy("float64")
    values = set(np.unique(gh[np.isfinite(gh)]))
    assert values <= {1.0, 2.0, 3.0}, f"expected ordinal 1/2/3, found {sorted(values)}"


def test_missing_microbiome_sample_stays_missing():
    """Subject 48 has no sample. "No sample" and "no bacteria" are different
    claims, so richness must be NaN rather than 0."""
    inc = metadata.microbiome_incidence()
    pid = metadata.participant_id(48)
    assert inc.loc[pid].isna().all()
    assert np.isnan(metadata.richness(inc)[pid])


def test_prevalence_is_a_fraction_and_groups_resolve():
    """Every prespecified functional group must actually match taxa present in the
    table -- a group that silently matches nothing would be reported as a null
    result when it was really a naming mismatch."""
    inc = metadata.microbiome_incidence()
    prev = metadata.prevalence_fractions(inc, metadata.FUNCTIONAL_GROUPS)
    for group in metadata.FUNCTIONAL_GROUPS:
        assert f"prev_{group}" in prev.columns, f"{group} matched no taxon"
        column = prev[f"prev_{group}"].dropna()
        assert ((column >= 0.0) & (column <= 1.0)).all()
        assert prev[f"n_{group}"].dropna().gt(0).any()


def test_jaccard_is_a_similarity_with_unit_diagonal():
    sim = metadata.jaccard_matrix(metadata.microbiome_incidence())
    arr = sim.to_numpy("float64")
    assert np.allclose(np.diag(arr), 1.0)
    assert np.allclose(arr, arr.T)
    assert arr.min() >= 0.0 and arr.max() <= 1.0
    # the excluded all-NaN participant must not appear
    assert metadata.participant_id(48) not in sim.index


def test_homa_ir_matches_the_standard_formula():
    table = metadata.metabolic_table()
    expected = table["m_fasting_glucose"] * table["m_fasting_insulin"] / 405.0
    assert np.allclose(table["m_homa_ir"].to_numpy(), expected.to_numpy(), equal_nan=True)


def test_ada_cohort_thresholds():
    table = metadata.metabolic_table()
    for a1c, cohort in zip(table["m_a1c"], table["m_cohort_ord"]):
        assert cohort == (0.0 if a1c < 5.7 else (1.0 if a1c < 6.5 else 2.0))
