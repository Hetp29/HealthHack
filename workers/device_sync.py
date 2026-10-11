"""Device sync worker.

    python -m workers.device_sync [--loop SECONDS]

Pulls new data for every OAuth connection that is `authorized` or `connected`. A
failure for one connection is recorded on that connection and does not stop the rest.
Rate-limited connections are simply attempted again on the next pass.
"""

from __future__ import annotations

import argparse
import logging
import time

from sqlalchemy import select

from backend.app.v2 import core
from backend.app.v2.db import tables as t
from backend.app.v2.deps import CONSENT_NECESSARY, V2Context, has_consent
from backend.app.v2.providers import registry
from backend.app.v2.services import devices

logger = logging.getLogger("glucotwin.workers.device_sync")


def run_once(ctx: V2Context) -> dict:
    tally: dict[str, int] = {}
    providers = set(registry.oauth_adapters(ctx.settings))
    with ctx.engine.connect() as conn:
        targets = conn.execute(
            select(t.device_connections.c.user_id, t.device_connections.c.provider)
            .where(t.device_connections.c.status.in_(("authorized", "connected", "error")))
        ).all()
    for user_id, provider in targets:
        if provider not in providers:
            continue
        # One transaction per connection, so one bad sync cannot roll back another.
        with ctx.engine.begin() as conn:
            if not has_consent(conn, user_id, CONSENT_NECESSARY):
                outcome = "skipped_no_consent"
            else:
                try:
                    outcome = devices.sync(conn, ctx, user_id, provider)["outcome"]
                except core.V2Error as error:
                    outcome = error.code
        tally[outcome] = tally.get(outcome, 0) + 1
    logger.info("device_sync %s", " ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "nothing_to_do")
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
