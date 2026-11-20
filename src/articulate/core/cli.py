"""CLI for Articulate (§3.12)."""
import argparse
import logging
import sys

from articulate.core.jobs import run_job

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """Main entry point for articulate CLI."""
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="articulate",
        description="Articulate: second brain and study curator",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # articulate run <job> [--only <step>]
    run_parser = subparsers.add_parser("run", help="Run a scheduled job (nightly, pass2, watch)")
    run_parser.add_argument("job", choices=["nightly", "pass2", "watch", "manual"], help="Job name")
    run_parser.add_argument("--only", dest="only_step", help="Run only a single registered step")

    # articulate quota
    subparsers.add_parser("quota", help="Today's ledger per model (stub in Phase 0 slice 1)")

    # articulate setup
    subparsers.add_parser("setup", help="Interactive one-time setup (stub in Phase 0 slice 1)")

    # articulate doctor
    subparsers.add_parser("doctor", help="Read-only checks of dependencies (stub in Phase 0 slice 1)")

    # articulate schemas
    subparsers.add_parser("schemas", help="Write schemas/*.json from Pydantic models (stub in Phase 0 slice 1)")

    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    if args.subcommand == "run":
        return run_job(args.job, only_step=args.only_step)

    if args.subcommand == "quota":
        print("Articulate Model Quota Ledger:")
        print("(Quota tracking implemented in issue #51)")
        return 0

    if args.subcommand == "setup":
        print("Setup helper will be available in issue #53.")
        return 0

    if args.subcommand == "doctor":
        print("Doctor helper will be available in issue #53.")
        return 0

    if args.subcommand == "schemas":
        print("Schemas generator will be available in issue #52.")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
