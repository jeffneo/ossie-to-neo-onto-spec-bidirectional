"""Command-line interface: validate, normalize, and convert between OGS and Ossie.

Phase 1 converts OGS models to and from Ossie *ontology* documents. The semantic model and
mapping documents arrive in phases 2 and 3.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .convert import Conversion, ConversionError, Loss, to_neo4j, to_ossie
from .io import dump, dumps_yaml
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

    for name, help_text in (
        ("to-ossie", "convert an OGS document to an Ossie ontology document"),
        ("to-neo4j", "convert an Ossie ontology document to an OGS document"),
    ):
        c = sub.add_parser(name, help=help_text)
        c.add_argument("file")
        c.add_argument(
            "-o", "--out", help="output file (.yaml or .json); default: print YAML to stdout"
        )
        c.add_argument("--report", help="write the loss report as JSON to this file")
        c.add_argument(
            "--strict",
            action="store_true",
            help="fail (exit 2) if anything was approximated or dropped",
        )
        c.add_argument(
            "--no-stash",
            action="store_true",
            help="don't keep source-only data in vendor annotations / custom data",
        )

    args = parser.parse_args(argv)
    if args.command == "validate":
        return _validate(args.files, args.json)
    if args.command == "normalize":
        return _normalize(args.file, args.format)
    return _convert(args)


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


def _convert(args: argparse.Namespace) -> int:
    convert = to_ossie if args.command == "to-ossie" else to_neo4j
    try:
        result: Conversion = convert(args.file, stash=not args.no_stash)
    except ConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for i in result.input_issues:
        print(f"input: {i}", file=sys.stderr)
    if args.out:
        dump(result.output, args.out)
    else:
        print(dumps_yaml(result.output), end="")
    if args.report:
        Path(args.report).write_text(json.dumps(result.report.to_dict(), indent=2) + "\n")

    lossy = [e for e in result.report.entries if e.kind is not Loss.STASHED]
    stashed = len(result.report.entries) - len(lossy)
    summary = f"{result.report.direction}: {stashed} stashed, {len(lossy)} approximated/dropped"
    print(summary, file=sys.stderr)
    for e in lossy:
        print(f"  [{e.kind}] {e.code} at {e.path}: {e.message}", file=sys.stderr)
    return 2 if args.strict and lossy else 0


if __name__ == "__main__":
    sys.exit(main())
