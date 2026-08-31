"""Small CLI for the operations that shouldn't need the UI."""

from __future__ import annotations

import argparse
import json
import sys

from . import health, paths
from .db import init_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aicvtailor")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create the SQLite schema if absent")
    doctor = sub.add_parser("doctor", help="run health probes and print the report")
    doctor.add_argument("--json", action="store_true", help="machine-readable output")

    args = parser.parse_args(argv)

    if args.command == "init-db":
        paths.ensure_dirs()
        init_db()
        print(f"database ready at {paths.DB_PATH}")
        return 0

    if args.command == "doctor":
        report = health.run_all()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"overall: {report['status']}  (provider={report['provider']})\n")
            for probe in report["probes"]:
                mark = {"ok": "+", "degraded": "~", "unavailable": "!"}[probe["status"]]
                print(f" {mark} {probe['name']:<15} {probe['detail']}")
                if probe["fallback"]:
                    print(f"   {'':<15} -> {probe['fallback']}")
        return 0 if report["status"] != "unavailable" else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
