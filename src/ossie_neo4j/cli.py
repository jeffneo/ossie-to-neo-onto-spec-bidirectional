"""Command-line interface: validate, normalize, and convert between OGS and Ossie.

Converts OGS models to and from Ossie ontology, semantic model and mapping documents.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .convert import Conversion, ConversionError, Loss, to_neo4j, to_ossie
from .io import DocumentKind, dump, dumps_yaml
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
        ("to-ossie", "convert an OGS document to Ossie ontology / semantic model / mapping"),
        ("to-neo4j", "convert Ossie documents (ontology, semantic model, mapping) to OGS"),
    ):
        c = sub.add_parser(name, help=help_text)
        c.add_argument(
            "file",
            help="the input: an OGS document, or an Ossie ontology or semantic model",
        )
        c.add_argument(
            "-o",
            "--out",
            help="output file (.yaml or .json) when there is one output document; "
            "default: print YAML to stdout",
        )
        if name == "to-ossie":
            c.add_argument(
                "--out-dir",
                help="write each output document to <dir>/<id>.<kind>.<format>",
            )
            c.add_argument("--format", choices=["yaml", "json"], default="yaml")
        else:
            c.add_argument(
                "--semantic-model",
                help="an Ossie semantic model to combine with the ontology given as FILE",
            )
            c.add_argument(
                "--mapping",
                help="an Ossie mapping document linking the ontology and semantic model",
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
    try:
        if args.command == "to-ossie":
            result: Conversion = to_ossie(args.file, stash=not args.no_stash)
        else:
            result = to_neo4j(
                args.file,
                semantic_model=args.semantic_model,
                mapping=args.mapping,
                stash=not args.no_stash,
            )
    except ConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    for i in result.input_issues:
        print(f"input: {i}", file=sys.stderr)

    docs = result.documents
    if getattr(args, "out_dir", None):
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = _output_stem(result)
        for kind, doc in docs.items():
            path = out_dir / f"{stem}.{_SUFFIX[kind]}.{args.format}"
            dump(doc, path)
            print(f"wrote {path}", file=sys.stderr)
    elif args.out:
        if len(docs) > 1:
            print(
                f"error: {len(docs)} output documents ({', '.join(docs)}); use --out-dir",
                file=sys.stderr,
            )
            return 1
        dump(result.output, args.out)
    else:
        print("---\n".join(dumps_yaml(d) for d in docs.values()), end="")
    if args.report:
        Path(args.report).write_text(json.dumps(result.report.to_dict(), indent=2) + "\n")

    lossy = [e for e in result.report.entries if e.kind is not Loss.STASHED]
    stashed = len(result.report.entries) - len(lossy)
    summary = f"{result.report.direction}: {stashed} stashed, {len(lossy)} approximated/dropped"
    print(summary, file=sys.stderr)
    for e in lossy:
        print(f"  [{e.kind}] {e.code} at {e.path}: {e.message}", file=sys.stderr)
    return 2 if args.strict and lossy else 0


_SUFFIX = {
    DocumentKind.OSSIE_ONTOLOGY: "ontology",
    DocumentKind.OSSIE_SEMANTIC_MODEL: "semantic_model",
    DocumentKind.OSSIE_MAPPING: "mapping",
    DocumentKind.OGS: "ogs",
}


def _output_stem(result: Conversion) -> str:
    """A file-name stem for the outputs: the source model's id, else the kebab-case name."""
    from .convert.naming import kebab

    doc = result.output
    ours = {k: v for k, v in (doc.get("annotations") or {}).items() if k == "neo4j:id"}
    return ours.get("neo4j:id") or kebab(doc.get("name") or "model")


if __name__ == "__main__":
    sys.exit(main())
