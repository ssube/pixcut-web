import argparse
from pathlib import Path


def migrate(root):
    from alembic import command
    from alembic.config import Config
    from .db import engine_for

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    engine = engine_for(root)
    config.attributes["engine"] = engine
    command.upgrade(config, "head")
    engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="PixCut simulator development service")
    parser.add_argument(
        "command",
        choices=[
            "migrate",
            "serve",
            "worker",
            "hardware-worker",
            "self-test",
            "probe",
        ],
    )
    parser.add_argument(
        "--once", action="store_true", help="Process at most one queued sheet"
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--all", action="store_true", help="Self-test: list all USB devices"
    )
    parser.add_argument(
        "--json", action="store_true", help="Self-test: emit a JSON report"
    )
    args = parser.parse_args()
    if args.command == "self-test":
        from .discovery import self_test

        raise SystemExit(self_test(include_all=args.all, json_output=args.json))
    if args.command == "probe":
        if args.all:
            parser.error("--all is not supported for the USB probe")
        from .probe import probe_cli

        raise SystemExit(probe_cli(json_output=args.json))
    if args.all or args.json:
        parser.error(
            "--all is only valid with self-test; --json supports self-test and probe"
        )
    from .db import data_dir

    root = data_dir()
    if args.command == "migrate":
        from .worker import worker_lock

        root.mkdir(parents=True, exist_ok=True)
        with worker_lock(root):
            migrate(root)
    elif args.command == "serve":
        import uvicorn

        uvicorn.run(
            "pixcut.api:create_app", factory=True, host="127.0.0.1", port=args.port
        )
    elif args.command == "worker":
        from .service import Service
        from .worker import SimulatorWorker, worker_lock

        with worker_lock(root):
            SimulatorWorker(Service(root)).run(once=args.once)
    else:
        from .printcut import HardwareWorker
        from .service import Service
        from .worker import worker_lock

        if __import__("os").environ.get("PIXCUT_EXECUTION_MODE") != "hardware":
            raise RuntimeError(
                "Set PIXCUT_EXECUTION_MODE=hardware to run the USB worker"
            )
        with worker_lock(root):
            HardwareWorker(Service(root)).run(once=args.once)


if __name__ == "__main__":
    main()
