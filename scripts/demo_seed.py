"""Initialize the demonstration from a clean application database.

    export GLUCOTWIN_DEMO_PASSWORD='choose-a-password'
    alembic upgrade head
    python -m scripts.demo_seed [--to CHECKPOINT]

Creates the fictional demo account and positions the replay at its start, or, with
`--to`, advances it to a checkpoint (`retrieval_activation`, `final_meal`, `end`, or
any checkpoint id) so a presentation can begin with history already built.

Requires the frozen fs-1.3 artifact and the demo data bundle. Neither is in git and
neither is ever substituted: if one is missing this command stops and says so.

Idempotent: if the demo account already exists it is left exactly as it is (use
`python -m scripts.demo_reset` to start over). It touches no other account.
"""

from __future__ import annotations

import argparse

from scripts._demo_common import build_app, fail, fail_with, run_guarded


def describe(state: dict) -> None:
    progress = state["personalization_progress"]
    nxt = state["next_checkpoint"]
    print(f"scenario       {state['scenario_id']}")
    print(f"status         {state['status']}  (event {state['event_index']} of {state['event_count']})")
    print(f"replay time    {state['position']}   [historical; wall clock is {state['wall_clock']}]")
    print(f"data released  {state['data']['readings_released']} of {state['data']['readings_total']} readings, "
          f"{state['data']['meals_logged']} of {state['data']['meals_total']} meals")
    print(f"personal hist. {progress['admissible_prior_meals']} admissible prior meals, "
          f"retrieval_active={progress['retrieval_active']}")
    print(f"next           {nxt['id'] + ' at ' + nxt['at'] if nxt else '(replay complete)'}")


def main(argv: list[str] | None = None) -> int:
    from backend.app.v2 import core
    from backend.app.v2.demo import replay

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--to", default=None, help="advance to this checkpoint after initializing")
    args = parser.parse_args(argv)

    app, ctx = build_app()
    try:
        state = replay.initialize(app, ctx)
        print("demo account created")
    except core.V2Error as error:
        if error.code != "demo_already_initialized":
            fail_with(error)
        user_id = replay._demo_user_id(ctx)
        if user_id is None:
            fail("demo_email_in_use: a non-demo account uses the demo email; refusing to touch it")
        state = run_guarded(lambda: replay.state(ctx, user_id))
        print("demo account already exists; left unchanged")
    if args.to:
        user_id = replay._demo_user_id(ctx)
        state = run_guarded(lambda: replay.advance(app, ctx, user_id, to=args.to))
    describe(state)
    print(f"sign in as     {ctx.settings.demo_email}  (password: the value of GLUCOTWIN_DEMO_PASSWORD)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
