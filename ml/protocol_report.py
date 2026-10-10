"""Emit the evaluation-protocol report BEFORE any model performance is inspected.

    python -m ml.protocol_report --source sqlite --db-url sqlite:///data/local/cgmacros_v2.db

Ruling D1 requires the held-out protocol to be fixed before results are seen, so that
it cannot be adjusted to flatter a score. This CLI writes `docs/evaluation_protocol.md`
describing the full filtering cascade and the exact split, and it deliberately reports
NO error metrics -- it cannot, because it never fits a model.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib

from ml.config import DEFAULT_CONFIG, Config
from ml.evaluate import split_cohort_holdout, split_day8_holdout, split_within_person
from ml.provenance import describe_time_basis, resolve_data_source, resolve_time_basis
from ml.targets import Episode, build_episodes


def _by_participant(groups: dict[str, list[Episode]]) -> list[str]:
    ids = sorted({e.participant_id for group in groups.values() for e in group})
    lines = [
        "| participant | train | validation | test | day-8 eligible | overlap-tagged |",
        "|---|---|---|---|---|---|",
    ]
    for pid in ids:
        counts = {name: sum(1 for e in g if e.participant_id == pid) for name, g in groups.items()}
        eligible = sum(
            1
            for g in groups.values()
            for e in g
            if e.participant_id == pid and e.context and e.context.day8_eligible
        )
        overlap = sum(
            1
            for g in groups.values()
            for e in g
            if e.participant_id == pid and e.target.has_overlapping_meal
        )
        lines.append(
            f"| `{pid}` | {counts['train']} | {counts['validation']} | {counts['test']} "
            f"| {eligible} | {overlap} |"
        )
    return lines


def build(repository, config: Config) -> str:
    data_source = resolve_data_source(repository)
    summaries = repository.list_participants()
    time_basis, tz = resolve_time_basis(
        repository, summaries[0].participant_id, summaries[0].timezone
    )

    episodes, exclusions = build_episodes(repository, config)
    train, validation, test = split_day8_holdout(episodes, config)
    wp_train, wp_test = split_within_person(episodes, config)
    co_train, co_test = split_cohort_holdout(episodes, config)

    report = exclusions.as_dict()
    day8 = config.day8
    unused = len(episodes) - len(train) - len(validation) - len(test)
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")

    out: list[str] = [
        "# Evaluation Protocol (fixed before results were inspected)",
        "",
        f"**Owner:** Ruhaan Shah · **Generated:** `{now}` · **Contract:** v1.0",
        f"**Dataset provenance:** `{data_source}` · **Participants discovered:** "
        f"{len(summaries)}",
        "",
        "Regenerate with:",
        "",
        "```bash",
        "python -m ml.protocol_report --source sqlite --db-url sqlite:///data/local/cgmacros_v2.db",
        "```",
        "",
        "> This document contains **no error metrics**, by design. Ruling D1 requires the "
        "held-out protocol to be fixed before any performance is seen, so it cannot be "
        "adjusted to improve a score. The command that produces it never fits a model.",
        "",
        "## 1 · Filtering cascade",
        "",
        "| Stage | Count |",
        "|---|---|",
        f"| Candidate logged meals in the database | {report['candidate_meals']} |",
        f"| Became usable episodes | {report['accepted']} |",
        f"| Excluded | {report['excluded']} |",
    ]
    for reason, count in report["reasons"].items():
        out.append(f"| &nbsp;&nbsp;↳ `{reason}` | {count} |")
    out += [
        f"| Tagged: another meal inside the 120-min window | {report['overlapping_tagged']} |",
        f"| **Day-8 context-eligible** | **{report['day8_eligible']}** |",
        "",
        "Nothing is dropped silently: every excluded meal carries a recorded reason.",
        "",
        "## 2 · Inclusion requirements",
        "",
        "**Every usable episode** must have:",
        "",
        f"- a measured CGM baseline within {config.features.baseline_tolerance_minutes:g} "
        "minutes **before** the meal (minute 0 of the curve is a measurement, never "
        "imputed; without one the episode is refused);",
        f"- an observed reading within ±{config.targets.tolerance_minutes:g} minutes of "
        f"all {config.targets.min_horizons_required} horizons (t+30/60/90/120);",
        "- all five macronutrients present (the forecast contract requires them).",
        "",
        "**A Day-8 test episode** must additionally have:",
        "",
        f"- at least **{day8.history_days} days** of prior CGM history;",
        f"- at least **{day8.min_history_coverage:.0%}** of the nominal "
        f"{day8.nominal_cadence_minutes:g}-minute readings present in that window "
        "(CGMacros has real sensor gaps; demanding 100% would discard good episodes);",
        "- **no other logged meal inside its 120-minute response window** "
        f"(enforced: `exclude_overlap_from_test={day8.exclude_overlap_from_test}`). An "
        "overlapping episode's observed response blends two meals, so an error measured "
        "against it is not attributable to the meal being forecast. Such episodes stay "
        "in training, tagged.",
        "",
        "## 3 · Split methodology — `day8_holdout` (PRIMARY)",
        "",
        "Per participant:",
        "",
        "1. **test candidates** = Day-8-eligible episodes, minus overlapping ones;",
        "2. **cutoff** = the earliest test-candidate meal time;",
        f"3. **pool** = episodes at or before `cutoff − {day8.embargo_minutes:g} min` "
        "(the **embargo/purge**);",
        f"4. **validation** = the latest {day8.validation_fraction:.0%} of that pool;",
        "5. **train** = the remainder of the pool.",
        "",
        "A participant with no test candidate contributes its whole pool to training: it "
        "still informs the cohort model but cannot be scored on a Day-8 meal.",
        "",
        "**Why the embargo.** A training episode at `t'` owns the response window "
        "`(t', t'+120]`. If `t' + 120 > cutoff`, that window shares CGM observations "
        "with a held-out window, so the training target would partially contain the "
        "held-out answer. Purging the last 120 minutes before the cutoff makes "
        "`t' + 120 ≤ cutoff` for every training episode.",
        "",
        "Train, validation and test are therefore **disjoint by construction** — the "
        "protocol never trains on all episodes and scores an overlapping subset.",
        "",
        "## 4 · Fold sizes",
        "",
        "| Fold | Episodes | Participants |",
        "|---|---|---|",
        f"| train | {len(train)} | {len({e.participant_id for e in train})} |",
        f"| validation | {len(validation)} | {len({e.participant_id for e in validation})} |",
        f"| **test (scored)** | **{len(test)}** | **{len({e.participant_id for e in test})}** |",
        f"| purged by embargo / ineligible-and-after-cutoff | {unused} | — |",
        f"| total usable | {len(episodes)} | {len({e.participant_id for e in episodes})} |",
        "",
        "Contract note: `sample_counts.train_episodes` in `GET /api/model/metrics` "
        f"reports {len(train) + len(validation)} — train **plus** validation — because "
        "contract v1.0 freezes that object's members and both folds are fitted or tuned "
        "on rather than scored. The breakdown lives here and in the evaluation detail "
        "file. No public field was added.",
        "",
        "## 5 · Secondary protocols (reported separately, never the primary result)",
        "",
        f"- `within_person_chronological`: {len(wp_train)} train / {len(wp_test)} test. "
        "Latest 25% of each participant's meals, **without** the Day-8 context "
        "requirement. Broader but weaker evidence.",
        f"- `cohort_holdout`: {len(co_train)} train / {len(co_test)} test across "
        f"{len({e.participant_id for e in co_test})} entirely held-out participants. "
        "Cold start; personalization is unavailable by construction.",
        "",
        "## 6 · Leakage safeguards",
        "",
        "| Guarantee | Mechanism |",
        "|---|---|",
        "| No feature reads the future | `build_features` starts with "
        "`history.truncated_at(meal_timestamp)`, so post-meal rows are *absent*, not "
        "merely unused |",
        "| Interpolated CGM never used | the data layer keeps only native sensor "
        "readings; the source's 1-minute rows are forward-interpolated and would encode "
        "future glucose |",
        "| Splits by whole meal episode | 5-minute rows inside one response window are "
        "not independent |",
        "| Response windows never overlap folds | the 120-minute embargo, asserted at "
        "run time by `_assert_day8_separation` |",
        "| Folds disjoint, test eligibility enforced | same assertion; the run fails "
        "rather than reporting a contaminated number |",
        "| Transforms fitted per fold | imputation medians, scaler statistics and the "
        "Ridge penalty are pipeline steps fitted on training rows only |",
        "| Calibration from training meals only | per-participant offsets come from the "
        "training pool, all strictly before the cutoff |",
        "",
        "## 7 · Regularization selection",
        "",
        "The Ridge penalty is chosen by **MAE on the explicit validation fold** — the "
        "latest pre-cutoff slice of each participant's own pool. It is participant-aware "
        "and chronological within person.",
        "",
        "A globally sorted `TimeSeriesSplit` is **not** used on real data: the organizer "
        "timestamps are date-shifted per participant (observed recording years here span "
        "2019–2023), so a global sort is not a chronology at all and would silently "
        "degenerate into participant blocks.",
        "",
        "**The test fold is never used for tuning, model selection, calibration or "
        "preprocessing.**",
        "",
        "## 8 · Timestamp semantics",
        "",
        f"`time_basis = {time_basis!r}`, `timezone = {tz!r}`.",
        "",
        (describe_time_basis(time_basis, tz) or "Timestamps are real UTC instants.").replace("\n", " "),
        "",
        "Time-of-day features are computed through the data layer's "
        "`local_minutes_of_day`, which reads the source wall clock directly for this "
        "basis and returns *missing* rather than guessing when the local clock is "
        "unknowable.",
        "",
        "## 9 · Per-participant counts",
        "",
    ]
    out += _by_participant({"train": train, "validation": validation, "test": test})
    out += [
        "",
        "## 10 · What is NOT claimed",
        "",
        "- Every technically eligible meal is **not** an independent sample: meals from "
        "one participant share physiology, and consecutive meals share CGM context.",
        "- No uncertainty interval is reported, because none has measured coverage.",
        "- No causal physiological effect is claimed from any feature attribution.",
        "",
    ]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    from ml.train import add_source_arguments, build_repository

    parser = argparse.ArgumentParser(description=__doc__)
    add_source_arguments(parser)
    parser.add_argument("--out", default="docs/evaluation_protocol.md")
    args = parser.parse_args(argv)

    repository = build_repository(args.source, args.days, args.seed, args.db_url)
    text = build(repository, DEFAULT_CONFIG)
    path = pathlib.Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(text)
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    from ml.protocol_report import main as _main

    raise SystemExit(_main())
