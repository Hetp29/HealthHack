"""The single ingestion pipeline shared by the fixture and official adapters.

    source files --(mapping config)--> canonical field dicts
                 --(validation)------> accepted / accepted-with-warnings / rejected
                 --(dedupe + FK)-----> one row per natural key, known participants only
                 --(upsert)----------> SQLite (idempotent)  +  quarantine  +  run report

Fixture data and official data go through exactly this code, so both arrive at
identical canonical records.
"""

from __future__ import annotations

import json
import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine

from data.adapters.mapping import MODALITIES, MappingConfig, MappingError, ModalityMap
from data.adapters.readers import RawRow, SourceReadError, iter_rows, resolve_files, sha256_inputs
from data.quality import quality_summary
from data.schema import SCHEMA_VERSION
from data.storage import tables
from data.storage.db import REPO_ROOT, init_db
from data.storage.upsert import UpsertCounts, upsert_rows
from data.timeutil import to_rfc3339, to_storage
from data.validation import (
    DEFAULT_THRESHOLDS,
    NATURAL_KEYS,
    PRIMARY_KEYS,
    VALIDATORS,
    Thresholds,
    ValidationContext,
)

DEFAULT_REPORT_DIR = REPO_ROOT / "data" / "reports" / "runs"
EVENT_MODALITIES = ("cgm", "cgm_secondary", "heart_rate", "meals", "medications", "activity")
REQUIRED_MODALITIES = ("cgm",)
# A participant-level median below this is far below any physiologic mg/dL level and
# typical of mmol/L values mislabeled as mg/dL.
MMOL_SUSPICION_MEDIAN = 30.0


class IngestionError(RuntimeError):
    pass


@dataclass
class _Quarantined:
    modality: str
    source_file: str | None
    source_row: int | None
    participant_id: str | None
    reasons: list[str]
    raw: dict


@dataclass
class PreparedSource:
    """Canonical-keyed rows produced by a source-specific adapter (CGMacros, MetaboNet).

    The adapter does the format-specific work (file layout, header variants, sensor
    de-interpolation); validation, dedupe, FK checks, upsert, quarantine and reporting
    still happen here, exactly as for mapped files.
    """

    name: str  # adapter name, recorded as the run's mapping_name
    label: str  # source label for provenance (relative path or dataset/subject)
    sha256: str  # checksum of the inputs this batch was built from
    data_source: str
    rows: dict  # modality -> list[RawRow] whose .values use canonical field names
    assume_timezone: str | None = None  # declared policy for offset-naive timestamps
    notes: list = field(default_factory=list)  # adapter findings, copied to batch_warnings


@dataclass
class _Candidate:
    record: dict
    raw: RawRow
    has_warnings: bool


@dataclass
class _ModalityStats:
    files: list[str] = field(default_factory=list)
    rows_read: int = 0
    skipped_empty: int = 0
    accepted: int = 0
    accepted_with_warnings: int = 0
    rejected: int = 0
    duplicates_dropped: int = 0
    out_of_order_rows: int = 0
    reasons: Counter = field(default_factory=Counter)
    warnings: Counter = field(default_factory=Counter)
    upsert: UpsertCounts = field(default_factory=UpsertCounts)
    unit_conversions: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "files": self.files,
            "rows_read": self.rows_read,
            "skipped_empty": self.skipped_empty,
            "accepted": self.accepted,
            "accepted_with_warnings": self.accepted_with_warnings,
            "rejected": self.rejected,
            "duplicates_dropped": self.duplicates_dropped,
            "out_of_order_rows_in_source": self.out_of_order_rows,
            "inserted": self.upsert.inserted,
            "updated": self.upsert.updated,
            "unchanged": self.upsert.unchanged,
            "rejection_reasons": dict(sorted(self.reasons.items())),
            "warnings": dict(sorted(self.warnings.items())),
            "unit_conversions": self.unit_conversions,
        }


@dataclass
class IngestionResult:
    run_id: str
    status: str
    report: dict
    report_path: Path | None

    @property
    def accepted(self) -> int:
        return self.report["totals"]["accepted"]

    @property
    def rejected(self) -> int:
        return self.report["totals"]["rejected"]


def _map_row(raw: RawRow, mmap: ModalityMap) -> dict:
    mapped = {}
    for canonical, column in mmap.columns.items():
        mapped[canonical] = raw.values.get(column)
    for canonical, constant in mmap.constants.items():
        mapped[canonical] = constant
    return mapped


def _all_null(mapped: dict, names: tuple[str, ...], ctx: ValidationContext) -> bool:
    for name in names:
        value = mapped.get(name)
        if value is not None and not (isinstance(value, str) and value.strip() in ctx.null_tokens):
            return False
    return True


def _comparable(record: dict) -> tuple:
    return tuple(sorted(record.items(), key=lambda kv: kv[0]))


class _Run:
    def __init__(
        self,
        source_root: Path,
        mapping: MappingConfig,
        engine: Engine | None,
        thresholds: Thresholds,
        prepared: "PreparedSource | None" = None,
    ):
        self.prepared = prepared
        self.root = source_root
        self.mapping = mapping
        self.engine = engine
        self.thresholds = thresholds
        self.run_id = f"run_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}"
        self.created_at = datetime.now(timezone.utc)
        self.stats: dict[str, _ModalityStats] = {m: _ModalityStats() for m in MODALITIES}
        self.quarantine: list[_Quarantined] = []
        self.accepted: dict[str, list[dict]] = {m: [] for m in MODALITIES}
        self.batch_warnings: list[dict] = []
        self.files: dict[str, list[Path]] = {}
        self.source_label = _display_source(source_root)
        self.report_rel_path: str | None = None

    # ------------------------------------------------------------ source discovery

    def discover(self) -> list[Path]:
        if not self.root.is_dir():
            raise IngestionError(f"source directory not found: {self.root}")
        all_files: list[Path] = []
        for modality, mmap in self.mapping.modalities.items():
            files = resolve_files(self.root, mmap.files)
            if not files:
                if modality in REQUIRED_MODALITIES or modality == "participants":
                    raise IngestionError(f"{modality}: no files match {list(mmap.files)} under {self.root}")
                self.batch_warnings.append({"code": "modality_files_not_found", "modality": modality})
            self.files[modality] = files
            self.stats[modality].files = [p.relative_to(self.root.resolve()).as_posix() for p in files]
            all_files.extend(files)
        self._warn_unmapped()
        return all_files

    def _warn_unmapped(self) -> None:
        for modality in MODALITIES:
            if modality not in self.mapping.modalities and modality not in ("participants", "cgm_secondary"):
                self.batch_warnings.append({"code": "modality_not_mapped", "modality": modality})

    # ------------------------------------------------------------ validation

    def _context(self, mmap: ModalityMap, participant_tz: dict[str, str | None]) -> ValidationContext:
        return ValidationContext(
            data_source=self.mapping.data_source,
            null_tokens=self.mapping.null_tokens,
            timestamp_format=mmap.timestamp_format or self.mapping.timestamp_format,
            assume_timezone=self.mapping.assume_timezone,
            participant_timezones=participant_tz,
            default_participant_timezone=self.mapping.derived_timezone,
            unit_factors=mmap.unit_factors(),
            participant_id_prefix=self.mapping.participant_id_prefix,
            thresholds=self.thresholds,
        )

    def _validate_modality(self, modality: str, participant_tz: dict[str, str | None]) -> list[_Candidate]:
        mmap = self.mapping.modalities[modality]
        ctx = self._context(mmap, participant_tz)
        stats = self.stats[modality]
        stats.unit_conversions = {k: f"{v} -> canonical" for k, v in mmap.units.items() if mmap.unit_factors()[k] != 1.0}
        validator = VALIDATORS[modality]
        accepted: list[_Candidate] = []
        for raw, mapped in self._source_rows(modality, mmap):
            stats.rows_read += 1
            if mmap.skip_if_all_null and _all_null(mapped, mmap.skip_if_all_null, ctx):
                stats.skipped_empty += 1
                continue
            if modality == "meals" and mmap.generate_meal_id:
                mapped["meal_id"] = "__generated__"
            result = validator(mapped, ctx)
            stats.warnings.update(result.warnings)
            if result.rejected:
                self._reject(modality, raw, mapped, result.reasons, ctx.participant_id_prefix)
                continue
            record = result.record
            if modality == "meals" and mmap.generate_meal_id:
                record["meal_id"] = f"{record['participant_id']}_meal_{to_storage(record['timestamp'])[:19].replace('-', '').replace(':', '')}"
            accepted.append(_Candidate(record, raw, bool(result.warnings)))
        return accepted

    def _source_rows(self, modality: str, mmap: ModalityMap):
        """(provenance row, canonical-keyed dict) pairs from files or from an adapter."""
        if self.prepared is not None:
            for raw in self.prepared.rows.get(modality, []):
                yield raw, dict(raw.values)
            return
        for path in self.files.get(modality, []):
            for raw in iter_rows(path, self.root):
                yield raw, _map_row(raw, mmap)

    def _reject(self, modality: str, raw: RawRow | None, mapped: dict, reasons: list[str], prefix: str = "") -> None:
        stats = self.stats[modality]
        stats.rejected += 1
        stats.reasons.update(reasons)
        pid = mapped.get("participant_id")
        self.quarantine.append(
            _Quarantined(
                modality=modality,
                source_file=raw.source_file if raw else None,
                source_row=raw.source_row if raw else None,
                participant_id=(prefix + str(pid)) if pid not in (None, "") else None,
                reasons=reasons,
                raw={k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v)) for k, v in mapped.items()},
            )
        )

    def _dedupe(self, modality: str, candidates: list[_Candidate]) -> list[dict]:
        """Identical repeats collapse to one row; conflicting repeats are all quarantined."""
        stats = self.stats[modality]
        groups: dict[tuple, list[_Candidate]] = defaultdict(list)
        for cand in candidates:
            groups[tuple(cand.record[k] for k in NATURAL_KEYS[modality])].append(cand)
        kept: list[dict] = []
        for members in groups.values():
            if len({_comparable(c.record) for c in members}) == 1:
                kept.append(members[0].record)
                stats.duplicates_dropped += len(members) - 1
                if any(c.has_warnings for c in members):
                    stats.accepted_with_warnings += 1
            else:
                for cand in members:
                    self._reject(modality, cand.raw, _jsonable_record(cand.record), ["conflicting_duplicate_in_source"])
        return kept

    # ------------------------------------------------------------ main flow

    def execute(self, *, dry_run: bool) -> dict:
        if self.prepared is not None:
            sha = self.prepared.sha256
            for modality, rows in self.prepared.rows.items():
                self.stats[modality].files = sorted({r.source_file for r in rows})
            self.batch_warnings.extend(self.prepared.notes)
            self._warn_unmapped()
        else:
            input_files = self.discover()
            sha = sha256_inputs(self.root, input_files)

        existing_tz: dict[str, str | None] = {}
        if self.engine is not None and _has_tables(self.engine):
            with self.engine.connect() as conn:
                for pid, tz in conn.execute(select(tables.participants.c.participant_id, tables.participants.c.timezone)):
                    existing_tz[pid] = tz

        # Participants first: their timezones may be needed to localize naive event times.
        participant_records: list[dict] = []
        if "participants" in self.mapping.modalities:
            cands = self._validate_modality("participants", {})
            participant_records = self._dedupe("participants", cands)
        participant_tz = {**existing_tz, **{p["participant_id"]: p["timezone"] for p in participant_records}}

        event_candidates: dict[str, list[_Candidate]] = {}
        for modality in EVENT_MODALITIES:
            if modality in self.mapping.modalities:
                event_candidates[modality] = self._validate_modality(modality, participant_tz)

        # Derive participants from event IDs when the source has no roster.
        known = set(existing_tz) | {p["participant_id"] for p in participant_records}
        if self.mapping.derive_participants:
            seen = sorted({c.record["participant_id"] for cands in event_candidates.values() for c in cands})
            new_ids = [pid for pid in seen if pid not in known]
            for index, pid in enumerate(new_ids, start=1):
                participant_records.append(
                    {
                        "participant_id": pid,
                        "display_name": f"Participant {index:03d}",
                        "timezone": self.mapping.derived_timezone,
                        "data_source": self.mapping.data_source,
                    }
                )
            if new_ids:
                self.batch_warnings.append({"code": "participants_derived_from_event_ids", "count": len(new_ids)})
            known |= set(new_ids)
        self.accepted["participants"] = participant_records

        for modality, cands in event_candidates.items():
            stats = self.stats[modality]
            valid: list[_Candidate] = []
            for cand in cands:
                if cand.record["participant_id"] not in known:
                    self._reject(modality, cand.raw, _jsonable_record(cand.record), ["unknown_participant"])
                else:
                    valid.append(cand)
            stats.out_of_order_rows = _count_out_of_order(valid)
            self.accepted[modality] = self._dedupe(modality, valid)

        for modality in MODALITIES:
            self.stats[modality].accepted = len(self.accepted[modality])

        self._batch_checks()

        status = "dry_run" if dry_run else "completed"
        if not dry_run:
            status = self._persist(sha)
        return self._report(sha, status, dry_run)

    def _batch_checks(self) -> None:
        cgm_by: dict[str, list[float]] = defaultdict(list)
        for r in self.accepted["cgm"]:
            cgm_by[r["participant_id"]].append(r["glucose_mg_dl"])
        for pid, values in sorted(cgm_by.items()):
            median = statistics.median(values)
            if median < MMOL_SUSPICION_MEDIAN:
                self.batch_warnings.append(
                    {"code": "glucose_units_suspect_mmol_per_l", "participant_id": pid, "median": round(median, 2)}
                )
        units: dict[tuple[str, str], set] = defaultdict(set)
        for m in self.accepted["medications"]:
            if m["dose_unit"]:
                units[(m["participant_id"], m["medication_class"])].add(m["dose_unit"])
        for (pid, cls), found in sorted(units.items()):
            if len(found) > 1:
                self.batch_warnings.append(
                    {"code": "inconsistent_dose_units", "participant_id": pid, "medication_class": cls, "units": sorted(found)}
                )
        act_units = {a["activity_unit"] for a in self.accepted["activity"] if a["activity_unit"]}
        if len(act_units) > 1:
            self.batch_warnings.append({"code": "multiple_activity_units", "units": sorted(act_units)})
        seen: Counter = Counter((m["participant_id"], m["timestamp"]) for m in self.accepted["meals"])
        same_time = sum(1 for n in seen.values() if n > 1)
        if same_time:
            self.batch_warnings.append({"code": "multiple_meals_same_participant_same_time", "count": same_time})

    def _persist(self, sha: str) -> str:
        totals_accepted = sum(len(v) for v in self.accepted.values())
        totals_rejected = len(self.quarantine)
        status = "completed_with_rejections" if totals_rejected else "completed"
        run_row = {
            "run_id": self.run_id,
            "source": self.source_label,
            "data_source": self.mapping.data_source,
            "schema_version": SCHEMA_VERSION,
            "created_at": self.created_at,
            "records": totals_accepted,
            "rejected": totals_rejected,
            "warnings": sum(sum(s.warnings.values()) for s in self.stats.values()) + len(self.batch_warnings),
            "error_summary": self._error_summary(),
            "sha256": sha,
            "status": status,
            "mapping_name": self.mapping.name,
            "report_path": self.report_rel_path,
        }
        try:
            with self.engine.begin() as conn:
                conn.execute(tables.ingestion_runs.insert().values(**run_row))
                for modality in MODALITIES:
                    table = tables.participants if modality == "participants" else tables.EVENT_TABLES[modality]
                    self.stats[modality].upsert = upsert_rows(
                        conn, table, PRIMARY_KEYS[modality], self.accepted[modality], self.run_id
                    )
                if self.quarantine:
                    conn.execute(
                        tables.quarantine.insert(),
                        [
                            {
                                "run_id": self.run_id,
                                "modality": q.modality,
                                "source_file": q.source_file,
                                "source_row": q.source_row,
                                "participant_id": q.participant_id,
                                "reasons": ",".join(q.reasons),
                                "raw_record": json.dumps(q.raw, sort_keys=True, default=str),
                            }
                            for q in self.quarantine
                        ],
                    )
        except Exception as exc:
            # Nothing from this run was stored (transaction rolled back). Log the failure.
            with self.engine.begin() as conn:
                conn.execute(
                    tables.ingestion_runs.insert().values(
                        **{**run_row, "records": 0, "status": "failed", "error_summary": f"{type(exc).__name__}: {exc}"[:2000]}
                    )
                )
            raise IngestionError(f"ingestion run {self.run_id} failed and was rolled back: {exc}") from exc
        return status

    def _error_summary(self) -> str | None:
        parts = []
        for modality in MODALITIES:
            reasons = self.stats[modality].reasons
            if reasons:
                parts.append(f"{modality}: " + ", ".join(f"{k}={v}" for k, v in sorted(reasons.items())))
        return "; ".join(parts) or None

    def _report(self, sha: str, status: str, dry_run: bool) -> dict:
        totals = Counter()
        for stats in self.stats.values():
            d = stats.as_dict()
            for key in ("rows_read", "skipped_empty", "accepted", "accepted_with_warnings", "rejected",
                        "duplicates_dropped", "inserted", "updated", "unchanged"):
                totals[key] += d[key]
        quarantine_sample = [
            {"modality": q.modality, "source_file": q.source_file, "source_row": q.source_row, "reasons": q.reasons}
            for q in self.quarantine[:200]
        ]
        return {
            "run_id": self.run_id,
            "status": status,
            "dry_run": dry_run,
            "source": self.source_label,
            "data_source": self.mapping.data_source,
            "schema_version": SCHEMA_VERSION,
            "created_at": to_rfc3339(self.created_at),
            "input_sha256": sha,
            "mapping": {"name": self.mapping.name, "path": self.mapping.path, "sha256": self.mapping.sha256},
            "totals": dict(totals),
            "modalities": {m: self.stats[m].as_dict() for m in MODALITIES},
            "batch_warnings": self.batch_warnings,
            "quarantine": {"count": len(self.quarantine), "first_200": quarantine_sample},
            "quality": quality_summary(
                self.accepted["participants"],
                self.accepted["cgm"],
                self.accepted["heart_rate"],
                self.accepted["meals"],
                self.accepted["medications"],
                self.accepted["activity"],
            ),
        }


def _jsonable_record(record: dict) -> dict:
    return {k: to_rfc3339(v) if isinstance(v, datetime) else v for k, v in record.items()}


def _has_tables(engine: Engine) -> bool:
    from data.storage.db import is_initialized

    return is_initialized(engine)


def _count_out_of_order(rows: list[_Candidate]) -> int:
    last: dict[str, datetime] = {}
    count = 0
    for cand in rows:
        pid, ts = cand.record["participant_id"], cand.record["timestamp"]
        if pid in last and ts < last[pid]:
            count += 1
        else:
            last[pid] = ts
    return count


def _display_source(root: Path) -> str:
    try:
        return root.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return root.resolve().name  # do not record absolute local paths


def run_prepared_ingestion(
    prepared: PreparedSource,
    engine: Engine | None,
    *,
    dry_run: bool = False,
    report_dir: str | Path | None = DEFAULT_REPORT_DIR,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> IngestionResult:
    """Ingest rows from a source-specific adapter through the common pipeline."""
    from data.adapters.mapping import CANONICAL_FIELDS, parse_mapping

    mapping = parse_mapping(
        {
            "name": prepared.name,
            "data_source": prepared.data_source,
            "assume_timezone": prepared.assume_timezone or "",
            "derive_participants": "participants" not in prepared.rows,
            "modalities": {
                m: {"files": ["<adapter>"], "columns": {f: f for f in CANONICAL_FIELDS[m]}}
                for m in prepared.rows
            },
        }
    )
    return _execute(Path("."), mapping, engine, dry_run, report_dir, thresholds, prepared)


def _execute(source_root, mapping, engine, dry_run, report_dir, thresholds, prepared=None) -> IngestionResult:
    if engine is None and not dry_run:
        raise IngestionError("an engine is required unless dry_run=True")
    if engine is not None and not dry_run:
        init_db(engine)
    run = _Run(Path(source_root), mapping, engine, thresholds, prepared)
    if prepared is not None:
        run.source_label = prepared.label
    report_path = None
    if report_dir is not None:
        report_path = Path(report_dir) / f"{run.run_id}.json"
        try:
            run.report_rel_path = report_path.resolve().relative_to(REPO_ROOT).as_posix()
        except ValueError:
            run.report_rel_path = report_path.name
    try:
        report = run.execute(dry_run=dry_run)
    except (MappingError, SourceReadError) as exc:
        raise IngestionError(str(exc)) from exc
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    return IngestionResult(run.run_id, report["status"], report, report_path)


def run_ingestion(
    source_root: str | Path,
    mapping: MappingConfig,
    engine: Engine | None,
    *,
    dry_run: bool = False,
    report_dir: str | Path | None = DEFAULT_REPORT_DIR,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
) -> IngestionResult:
    """Ingest every file the mapping names under ``source_root``.

    ``dry_run=True`` validates and reports without writing to the database (``engine``
    may then be ``None``). The JSON report is written to ``report_dir`` unless it is
    ``None``.
    """
    return _execute(source_root, mapping, engine, dry_run, report_dir, thresholds)
