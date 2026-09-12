import argparse

import m5
from m5.util import addToPath, fatal

addToPath("../../")

from gate4_runtime import (
    GATE4_DEFAULTS,
    add_gate4_arguments,
    assemble,
    define_gate4_options,
    normalize_gate4_arguments,
    run_simulation,
    validate_gate4_arguments,
)


def main():
    parser = argparse.ArgumentParser()
    define_gate4_options(parser)
    parser.add_argument("--plan-image", required=True)
    parser.add_argument("--sim-tick-limit", type=int, default=2_000_000)
    parser.set_defaults(**GATE4_DEFAULTS)
    add_gate4_arguments(parser)
    args = parser.parse_known_args()[0]
    if args.sim_tick_limit <= 0:
        fatal("Gate4 simulation tick limit must be positive")
    if not args.plan_image:
        fatal("Gate4 plan image path must not be empty")
    try:
        normalize_gate4_arguments(args)
        validate_gate4_arguments(args)
    except ValueError as error:
        fatal(str(error))
    assemble(args, args.plan_image)
    event = run_simulation(args.sim_tick_limit)
    raise SystemExit(event.getCode())


main()
