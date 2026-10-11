"""Medication reminder worker.

    python -m workers.reminders [--loop SECONDS]

Creates a `scheduled` dose slot, and an in-app notification record, for every
reminder time that has passed. It never records a dose as taken.

The API materializes a user's due slots on demand, so in-app reminders work without
this worker. It is needed once a push or email channel exists, to raise a reminder for
someone who does not have the app open.
"""

from __future__ import annotations

import argparse
import logging
import time

from backend.app.v2.deps import V2Context
from backend.app.v2.services import medications

logger = logging.getLogger("glucotwin.workers.reminders")


def run_once(ctx: V2Context) -> int:
    with ctx.engine.begin() as conn:
        created = medications.materialize_reminders(conn, ctx)
    logger.info("reminders created=%d", created)
    return created


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
