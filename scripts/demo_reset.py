"""Return the demonstration to its initial state.

    python -m scripts.demo_reset [--to CHECKPOINT]

Deletes one demonstration's account (`--scenario new_user` by default, or
`returning_user`) -- that one account's rows and nothing else -- and creates it again
at the start of its presented journey. The other demonstration is not touched. Safe to run repeatedly. It refuses to act if
the demo email belongs to an ordinary (non-demo) account.

Existing demo session tokens stop working; sign in again.
"""

from __future__ import annotations

import argparse

from scripts._demo_common import build_app, run_guarded
from scripts.demo_seed import describe


def main(argv: list[str] | None = None) -> int:
    from backend.app.v2.demo import replay

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=["new_user", "returning_user"], default="new_user",
                        help="which demonstration (default: new_user)")
    parser.add_argument("--to", default=None, help="advance to this checkpoint after resetting")
    args = parser.parse_args(argv)

    app, ctx = build_app()
    state = run_guarded(lambda: replay.reset(app, ctx, args.scenario))
    if args.to:
        user_id = replay._demo_user_id(ctx, args.scenario)
        state = run_guarded(lambda: replay.advance(app, ctx, user_id, to=args.to))
    print("demo reset")
    describe(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
