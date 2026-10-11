# GLIMPSE release blockers

What stands between the audited build and a public, judge-usable deployment. Nothing
here is a known application defect; each is a check that has not been run, or work that
has not been done.

| # | Blocker | Why it blocks | Owner | How to clear it |
|---|---|---|---|---|
| 1 | **No public deployment exists.** | Judges cannot open the app without one. The local start-up needs the model artifact and demonstration bundles, which are deliberately not in git. | Het | Deploy per `RUNBOOK.md` ("Deploying"), then run the smoke scripts and `npm run test:e2e` against the public URLs. |
| 2 | **PostgreSQL not verified on this build.** | The audit machine has no PostgreSQL server. The release adds queries (hypothetical-meal filters: `LIKE`, `IN (subquery)`) that are standard SQL but unproven there. Last recorded PostgreSQL pass is on an earlier commit. | Het | `infra/local/verify_postgres.sh` on the release commit. |
| 3 | **Container build and container smoke not run on this build.** | No Docker on the audit machine. | Het | `infra/local/container_smoke.sh` on the release commit. |
| 4 | **No rate limiting on sign-in or uploads** (BUG_REGISTER OPEN-1). | 25 wrong passwords in a row were all simply refused; the demonstration password is shared. Team decision B-005 puts the limit at the edge. **Not implemented anywhere yet.** | Het | Exact rules and the test that proves them: `HET_DEPLOYMENT_HANDOFF.md` §4. |
| 5 | **`main` and the release have diverged.** | `main` (`2f167f4`) still carries the pre-redesign frontend, patched; the redesign, the hypothetical-meal fix, the audit fixes and the freeze restoration are on `final-audit`. Deploying `main` would ship the old frontend. | Ruhaan / team | Review and merge `final-audit` into `main` with a merge commit (no squash, no force-push). The old frontend files are deleted on purpose. |
| 6 | **Real OpenAI analysis not re-run on the final commit.** | By choice during the audit. It passed from the browser twice on earlier commits of this branch; the code path is unchanged since. | Ruhaan | `E2E_OPENAI=1 E2E_MEAL_PHOTO=<a team-owned photo> npm run test:e2e -- photo-analysis` (one paid call). |

## Resolved on this branch, still wrong on `main`

* **Frozen files and Contract v1.0** were edited and re-recorded by `4a2d59f`. Restored
  here (BUG-012, D-020). `main` keeps the edited versions until this branch is merged;
  do not deploy `main` as it stands.

## Local configuration note

* `.env` on the developer machine has its `…_DEMO_MODE` / `…_DEMO_PASSWORD` lines
  commented out; the server ignores commented lines.

## Not blockers, but not done

Demo recording; hackathon submission upload; Safari/Firefox and real-phone passes;
assistive-technology session; real provider connections (unavailable anywhere).
