"""Source adapters: fixture and official data both enter through one pipeline.

    from data.adapters import import_fixture, import_official
    result = import_fixture()                      # data/fixtures/synthetic_dev -> DB
    result = import_official("/path/to/official", "data/mapping/official.toml")
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import Engine

from data.adapters.mapping import MappingConfig, MappingError, load_mapping
from data.adapters.pipeline import DEFAULT_REPORT_DIR, IngestionError, IngestionResult, run_ingestion
from data.storage.db import REPO_ROOT, make_engine

FIXTURE_DIR = REPO_ROOT / "data" / "fixtures" / "synthetic_dev"
FIXTURE_MAPPING = REPO_ROOT / "data" / "mapping" / "synthetic_dev.toml"


def _engine(engine: Engine | None, db_url: str | None, dry_run: bool) -> Engine | None:
    if engine is not None:
        return engine
    if dry_run and db_url is None:
        return None
    return make_engine(db_url)


def import_fixture(
    source_dir: str | Path = FIXTURE_DIR,
    *,
    engine: Engine | None = None,
    db_url: str | None = None,
    dry_run: bool = False,
    report_dir: str | Path | None = DEFAULT_REPORT_DIR,
) -> IngestionResult:
    """Import canonical-format fixture files (default: the committed synthetic_dev set)."""
    return run_ingestion(
        source_dir,
        load_mapping(FIXTURE_MAPPING),
        _engine(engine, db_url, dry_run),
        dry_run=dry_run,
        report_dir=report_dir,
    )


def import_official(
    path: str | Path,
    mapping_config: str | Path | MappingConfig,
    *,
    engine: Engine | None = None,
    db_url: str | None = None,
    dry_run: bool = False,
    report_dir: str | Path | None = DEFAULT_REPORT_DIR,
) -> IngestionResult:
    """Import an organizer dataset directory through a config-driven column mapping."""
    mapping = mapping_config if isinstance(mapping_config, MappingConfig) else load_mapping(mapping_config)
    return run_ingestion(path, mapping, _engine(engine, db_url, dry_run), dry_run=dry_run, report_dir=report_dir)


__all__ = [
    "FIXTURE_DIR",
    "IngestionError",
    "IngestionResult",
    "MappingError",
    "import_fixture",
    "import_official",
    "load_mapping",
]
