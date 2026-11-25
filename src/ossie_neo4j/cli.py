"""Command-line interface.

Phase 0 provides ``validate`` and ``normalize``; ``to-ossie`` and ``to-neo4j`` come in phase 1.
"""

from __future__ import annotations

import argparse
import json
import sys

from .io import dumps_yaml
from .issues import Severity
from .pipeline import read


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ossie-neo4j", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    v = sub.add_parser(
        "validate", help="validate OGS or Ossie documents (the kind is auto-detected)"
    )
    v.add_argument("files", nargs="+")
    v.add_argument("--json", action="store_true", help="print issues as JSON")

    n = sub.add_parser(
        "normalize", help="print the normalized (canonical longhand) form of a document"
    )
    n.add_argument("file")
    n.add_argument("--format", choices=["yaml", "json"], default="yaml")

    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args.files, args.json)
    return _normalize(args.file, args.format)


def _validate(files: list[str], as_json: bool) -> int:
    failed = False
    report = []
    for f in files:
        loaded = read(f)
        failed |= not loaded.ok
        if as_json:
            report.append(
                {
                    "file": f,
                    "kind": loaded.kind,
                    "ok": loaded.ok,
                    "issues": [
                        {
                            "code": i.code,
                            "severity": i.severity,
                            "path": i.path,
                            "message": i.message,
                        }
                        for i in loaded.issues
                    ],
                }
            )
            continue
        status = "ok" if loaded.ok else "INVALID"
        print(f"{f}: {loaded.kind}: {status}")
        for i in loaded.issues:
            print(f"  {i}")
    if as_json:
        print(json.dumps(report, indent=2))
    return 1 if failed else 0


def _normalize(file: str, fmt: str) -> int:
    loaded = read(file)
    for i in loaded.issues:
        print(i, file=sys.stderr)
    if loaded.normalized is None:
        return 1
    data = loaded.normalized.model_dump(mode="json", by_alias=True, exclude_none=True)
    print(
        json.dumps(data, indent=2, ensure_ascii=False) if fmt == "json" else dumps_yaml(data),
        end="",
    )
    return 0 if not any(i.severity is Severity.ERROR for i in loaded.issues) else 1


if __name__ == "__main__":
    sys.exit(main())
