# infra/: deployment and operations (owner: Het)

Infrastructure for GlucoTwin. Only infrastructure lives here. Backend, model and frontend code
are owned by Ruhaan and Neil and are not changed by anything in this folder. The design is in
`docs/project/HET_PHASE0_INFRA_AUDIT.md`.

## Local verification (H2)

| Script | Checks | Needs |
|---|---|---|
| `infra/local/verify_postgres.sh` | v2 Alembic migrations (upgrade → check → downgrade → upgrade) and the v2 test suite against a throwaway **PostgreSQL 16** container | Docker, repo `.venv` |
| `infra/local/container_smoke.sh` | artifact checksums → clean build → start-time gate refuses a wrong checksum → Postgres + one-off migration → API probes → `scripts/smoke_v2.py` → demo mode (if bundles present) | Docker, the 5 frozen files in `ml/artifacts_cgmacros_fs13/` |

## Results log

| Date (UTC) | Commit | Check | Result |
|---|---|---|---|
| 2026-10-11 | `fe5d532` | Build with **no** artifact | **Fails as required** at `verify_artifact`: "model artifact not found … never trained here". All Python dependencies install in `python:3.12-slim` (linux/arm64). |
| 2026-10-11 | `fe5d532` | Build with a **wrong** artifact | **Fails as required**: "checksum mismatch: expected 12ed0602…, found 2ca054eb…". No image is produced. |
| 2026-10-11 | `fe5d532` | `verify_postgres.sh` (PostgreSQL 16.15) | Migrations up/check/down/up **OK** (head `0002`). v2 suite: **289 passed, 13 skipped**, 2 deselected (see below). |
| 2026-10-11 | `a68792a` | `verify_postgres.sh` after Ruhaan's `4f3f2f4` (adds migration `0003`) | Migrations up/check/down/up **OK** (head `0003`). v2 suite: **303 passed, 13 skipped**, 2 deselected (same harness limitation). |
| 2026-10-11 | `fea1359` | Frozen files received from Ruhaan | All 5 artifact SHA-256 match `HANDOFF_HET.md` §4. `research.freeze.verify_artifacts`: **PHASE 1 PASS**. Both demo bundles match their manifest canonical hashes. Stored gitignored, never committed. |
| 2026-10-11 | `fea1359` | `container_smoke.sh` | **ALL CHECKS PASSED** in 1 min 53 s: image `glucotwin-backend:fea13595c30a` (232 MB, arm64, runs as `glucotwin`); start-time gate refused a wrong checksum; Postgres 16 one-off migration; `/health`, `/api/v2/ready` (database/model/integrity all true), fs-1.3 `data_source=official`, `/api/v2/me` 401; `smoke_v2.py` 21/21 PASS (vision 503 `vision_not_configured` as expected with no OpenAI key); demo: `demo_verify` ALL CHECKS PASSED (both scenarios), `demo_seed`, `smoke_demo.py` ALL STEPS PASSED. The container ran `--read-only`. |

**The 2 deselected tests** (`test_resetting_one_mode_never_touches_the_other_or_an_ordinary_account`, both
parameters): the test builds a second harness partway through. When `GLUCOTWIN_TEST_APP_DB_URL` points at one shared
database, that second harness drops and recreates the schema, wiping the first harness's data. On SQLite each
harness has its own file, so the test passes there. This is a test-harness limitation, not an app defect, and is
reported to Ruhaan.

**Platform NOTE (not a failure):** `demo_verify` reports the end-state fingerprint differs from the one recorded on Ruhaan's Mac. This is expected across platforms (floating point, linux/arm64 vs macOS), as `DEMO_HANDOFF_HET.md` §11 says. Reproducibility, chronology and count checks all pass.

macOS note: Docker Desktop's credential helper lives in `/Applications/Docker.app/Contents/Resources/bin`. Both
scripts add it to `PATH`.
