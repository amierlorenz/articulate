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
    run_parser.add_argument("--only", "--step", dest="only_step", help="Run only a single registered step")

    # articulate quota
    subparsers.add_parser("quota", help="Today's ledger per model (stub in Phase 0 slice 1)")

    # articulate setup [secrets|shortcuts] [--shortcuts]
    setup_parser = subparsers.add_parser("setup", help="Interactive one-time setup")
    setup_parser.add_argument(
        "target",
        nargs="?",
        choices=["secrets", "shortcuts"],
        default=None,
        help="Optional setup target (e.g. 'secrets', 'shortcuts')",
    )
    setup_parser.add_argument(
        "--shortcuts",
        action="store_true",
        help="Setup master-todo bridge shortcuts",
    )

    # articulate doctor
    subparsers.add_parser("doctor", help="Read-only checks of dependencies (§3.12)")

    # articulate schemas
    subparsers.add_parser("schemas", help="Write schemas/*.json from Pydantic models (stub in Phase 0 slice 1)")

    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    if args.subcommand == "run":
        return run_job(args.job, only_step=args.only_step)

    if args.subcommand == "quota":
        from articulate.core.config import Config, load_models_config
        from articulate.core.db import Database
        from articulate.core.models.quota import format_quota_report
        from articulate.core.paths import get_db_path, models_toml_path

        db = Database(get_db_path())
        db.migrate()
        cfg: Config | None = None
        try:
            if models_toml_path().exists():
                cfg = Config(models=load_models_config(db=db))
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not load models config for quota report: %s", e)

        print(format_quota_report(db, cfg))
        return 0

    if args.subcommand == "setup":
        if getattr(args, "shortcuts", False) or getattr(args, "target", None) == "shortcuts":
            from articulate.core.setup import setup_shortcuts

            ok = setup_shortcuts(interactive=True)
            return 0 if ok else 1

        from articulate.core.setup import run_setup
        return run_setup(secrets_only=(getattr(args, "target", None) == "secrets"), interactive=True)

    if args.subcommand == "doctor":
        from articulate.core.doctor import run_doctor
        return run_doctor()

    if args.subcommand == "schemas":
        from articulate.core.handoff_schemas import write_schemas
        paths = write_schemas()
        for p in paths:
            print(f"Wrote {p}")
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
