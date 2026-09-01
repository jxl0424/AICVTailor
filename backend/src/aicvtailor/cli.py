"""Small CLI for the operations that shouldn't need the UI."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import health, paths
from .db import init_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aicvtailor")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create the SQLite schema if absent")
    doctor = sub.add_parser("doctor", help="run health probes and print the report")
    doctor.add_argument("--json", action="store_true", help="machine-readable output")

    models = sub.add_parser("models", help="show the live model catalogue and role resolution")
    models.add_argument(
        "--refresh", action="store_true", help="bypass the 24h cache and refetch"
    )
    models.add_argument("--all", action="store_true", help="list every live model id")

    show = sub.add_parser("parse", help="parse a .tex resume and print its structure")
    show.add_argument("path", nargs="?", help="defaults to data/master/master.tex")
    show.add_argument("--json", action="store_true", help="dump the IR as JSON")
    show.add_argument(
        "--check",
        action="store_true",
        help="verify the round-trip properties on this file and exit non-zero on failure",
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="show library log output"
    )
    args = parser.parse_args(argv)

    # The CLI formats its own findings; the library logger would print each
    # warning a second time.
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.ERROR)

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

    if args.command == "models":
        return _cmd_models(args)

    if args.command == "parse":
        return _cmd_parse(args)

    return 1


def _cmd_models(args) -> int:
    from .config import get_settings
    from .llm import catalogue

    if not get_settings().nvidia_api_key and args.refresh:
        print("NVIDIA_API_KEY is not set, so the live catalogue cannot be fetched.")
        print("Showing whatever is cached instead.\n")

    ids = catalogue.fetch_catalogue(force=args.refresh)
    print(f"{len(ids)} models in the catalogue"
          f"{' (cached)' if not args.refresh else ''}\n")

    if args.all:
        for model_id in sorted(ids):
            print(f"  {model_id}")
        print()

    print("role resolution:")
    for resolution in catalogue.resolve_all(ids).values():
        mark = "+" if resolution.verified else "!"
        print(f" {mark} {resolution.role.value:<10} {resolution.model}")
        print(f"   {'':<10} matched {resolution.matched!r} via {resolution.source}")
        if resolution.warning:
            print(f"   {'':<10} -> {resolution.warning}")
    return 0


def _cmd_parse(args) -> int:
    from .latex import parse

    target = Path(args.path) if args.path else paths.MASTER_DIR / "master.tex"
    if not target.exists():
        print(f"no such file: {target}", file=sys.stderr)
        return 1

    text = target.read_text(encoding="utf-8")
    doc = parse(text)

    if args.check:
        return _check_roundtrips(doc, text)

    if args.json:
        print(json.dumps(_as_dict(doc), indent=2))
        return 0

    print(f"{target}  ({len(text)} bytes)")
    print(
        f"  {len(doc.sections)} sections, {len(list(doc.entries()))} entries, "
        f"{len(list(doc.bullets()))} bullets, {len(list(doc.skill_lines()))} skill lines\n"
    )
    for section in doc.sections:
        print(f"[{section.id}] {section.title}")
        for entry in section.entries:
            fields = "  ".join(
                f"{f.role_guess}={f.text.strip()[:30]!r}" for f in entry.fields
            )
            print(f"  ({entry.id}) {entry.kind}: {fields}")
            for bullet in entry.bullets:
                flag = " [protected]" if bullet.protected else ""
                print(f"      {bullet.id}  {bullet.text.strip()[:64]}...{flag}")
        for line in section.skill_lines:
            print(f"  ({line.id}) {line.label}: {', '.join(line.values)}")
    return 0


def _check_roundtrips(doc, text: str) -> int:
    """The Phase 1 gate, runnable against any file the user points it at."""
    failures: list[str] = []

    if doc.to_source() != text:
        failures.append("identity: regenerating with no edits changed the file")

    edits = [doc.edit(b.id, b.text) for b in doc.bullets()]
    edits += [doc.edit(k.id, k.values_span.text(text)) for k in doc.skill_lines()]
    if doc.to_source(edits) != text:
        failures.append("restore: rewriting each span with its own text changed the file")

    for bullet in doc.bullets():
        out = doc.to_source([doc.edit(bullet.id, bullet.text + " SENTINEL")])
        if out[: bullet.span.start] != text[: bullet.span.start] or out[
            bullet.span.end + 9 :
        ] != text[bullet.span.end :]:
            failures.append(f"mutation: editing {bullet.id} disturbed bytes outside its span")
            break

    for line in failures:
        print(f" ! {line}")
    if not failures:
        print(f" + identity, restore and mutation round-trips all byte-identical")
        print(f"   ({len(edits)} editable spans, {len(list(doc.bullets()))} bullets)")
    return 1 if failures else 0


def _as_dict(doc) -> dict:
    return {
        "sections": [
            {
                "id": s.id,
                "title": s.title,
                "entries": [
                    {
                        "id": e.id,
                        "kind": e.kind,
                        "fields": [
                            {"index": f.index, "role_guess": f.role_guess, "text": f.text}
                            for f in e.fields
                        ],
                        "bullets": [
                            {
                                "id": b.id,
                                "fingerprint": b.fingerprint,
                                "text": b.text,
                                "protected": list(b.protected),
                            }
                            for b in e.bullets
                        ],
                    }
                    for e in s.entries
                ],
                "skills": [
                    {"id": k.id, "label": k.label, "values": list(k.values)}
                    for k in s.skill_lines
                ],
            }
            for s in doc.sections
        ]
    }


if __name__ == "__main__":
    sys.exit(main())
