# GLIMPSE release blockers

What stands between the audited build and a public, judge-usable deployment. Nothing
here is a known application defect; each is a check that has not been run, or work that
has not been done.

| # | Blocker | Why it blocks | Owner | How to clear it |
|---|---|---|---|---|
| 1 | **No public deployment exists.** | Judges cannot open the app without one. The local start-up needs the model artifact and demonstration bundles, which are deliberately not in git. | Het | Deploy per `RUNBOOK.md` ("Deploying"), then run the smoke scripts and `npm run test:e2e` against the public URLs. |
| 2 | **PostgreSQL not verified on this build.** | The audit machine has no PostgreSQL server. The release adds queries (hypothetical-meal filters: `LIKE`, `IN (subquery)`) that are standard SQL but unproven there. Last recorded PostgreSQL pass is on an earlier commit. | Het | `infra/local/verify_postgres.sh` on the release commit. |
| 3 | **Container build and container smoke not run on this build.** | No Docker on the audit machine. | Het | `infra/local/container_smoke.sh` on the release commit. |
| 4 | **No rate limiting on sign-in or uploads** (BUG_REGISTER OPEN-1). | 25 wrong passwords in a row were all simply refused; the demonstration password is shared. Team decision B-005 puts the limit at the edge. | Het | Rate-limit `/api/v2/auth/*` and `/api/v2/meals/images` at the load balancer / gateway before going public. |
| 5 | **`main` and the release have diverged.** | `main` (`2f167f4`) still carries the pre-redesign frontend, patched; the redesign, the hypothetical-meal fix and the audit fixes are on `final-audit`. Deploying `main` would ship the old frontend. | Ruhaan / team | Review and merge `final-audit` into `main` with a merge commit (no squash, no force-push). The old frontend files are deleted on purpose. |
| 6 | **Real OpenAI analysis not re-run on the final commit.** | By choice during the audit. It passed from the browser twice on earlier commits of this branch; the code path is unchanged since. | Ruhaan | `E2E_OPENAI=1 E2E_MEAL_PHOTO=<a team-owned photo> npm run test:e2e -- photo-analysis` (one paid call). |

## Things to confirm rather than fix

* **Contract v1.0's checksum changed on `main`.** Commit `4a2d59f` renamed settings
  `GLUCOTWIN_*` → `GLIMPSE_*` and edited one description line in `contracts/openapi.yaml`
  plus the env-var prefix in four frozen source files; the governance manifest records a
  re-hash "approved by Ruhaan Shah (freeze owner), relayed by Het Patel". The model
  artifact is byte-identical (`12ed0602…15fe`) and the freeze check passes. The contract
  file's SHA-256 is now `eb279266…baad` (was `be836ddd…00f4`). If that approval was not
  given, this needs reverting on `main`; the audit did not change it either way.
* **`.env` on the developer machine** has `GLIMPSE_DEMO_MODE` / `…_DEMO_PASSWORD` lines
  commented out; the server ignores commented lines.

## Not blockers, but not done

Demo recording; hackathon submission upload; Safari/Firefox and real-phone passes;
assistive-technology session; real provider connections (unavailable anywhere).
