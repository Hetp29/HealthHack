"""Outcome scoring worker.

    python -m workers.outcome_scoring            # one pass, then exit
    python -m workers.outcome_scoring --loop 300 # every 300 seconds

Scores every episode whose response window and grace period have passed. Safe to run
on a schedule, from several processes at once, or never: the API also scores a user's
due episodes whenever that user asks for a forecast or an outcome, so this worker only
keeps outcomes current for people who are not actively using the app.

Stateless and idempotent. It holds no timers of its own -- the schedule belongs to the
platform (cron, EventBridge, a queue consumer).
"""

from __future__ import annotations

import argparse
import logging
import time

from backend.app.v2.deps import V2Context
from backend.app.v2.services import outcomes

logger = logging.getLogger("glucotwin.workers.outcomes")


def run_once(ctx: V2Context) -> dict:
    with ctx.engine.begin() as conn:
        tally = outcomes.score_due(conn, ctx)
    logger.info("outcome_scoring %s", " ".join(f"{k}={v}" for k, v in tally.items()))
    return tally


def main(argv: list[str] | None = None) -> int:
    from workers._context import build_worker_context

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--loop", type=float, default=None, metavar="SECONDS")
    args = parser.parse_args(argv)
    ctx = build_worker_context()
    while True:
        run_once(ctx)
        if args.loop is None:
            return 0
        time.sleep(args.loop)


if __name__ == "__main__":
    raise SystemExit(main())
