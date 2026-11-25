"""Command-line interface for converting between the Neo4j Ontology Graph Spec and Apache Ossie.

Commands:
  convert    convert in whichever direction the inputs imply (auto-detected)
  to-ossie   OGS ➜ Ossie ontology / semantic model / mapping
  to-neo4j   Ossie ontology / semantic model / mapping ➜ OGS
  validate   validate documents of any kind
  normalize  print a document's normalized (canonical) form
  migrate    migrate an Ossie 0.1.1 (OSI) document to 0.3.0.dev0

Exit codes: 0 success, 1 invalid input or usage error, 2 lossy result under --strict.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .convert import Conversion, ConversionError, Loss, to_neo4j, to_ossie
from .convert.naming import kebab
from .io import DocumentKind, UnknownDocumentKind, detect_kind, dump, dumps_yaml, load
from .issues import Issue, Severity
from .ossie.migrate import is_legacy, migrate
from .pipeline import read
from .schemas import validate_legacy_schema

_SUFFIX = {
    DocumentKind.OSSIE_ONTOLOGY: "ontology",
    DocumentKind.OSSIE_SEMANTIC_MODEL: "semantic_model",
    DocumentKind.OSSIE_MAPPING: "mapping",
    DocumentKind.OGS: "ogs",
}
_ONLY = {
    "ontology": DocumentKind.OSSIE_ONTOLOGY,
    "semantic-model": DocumentKind.OSSIE_SEMANTIC_MODEL,
    "mapping": DocumentKind.OSSIE_MAPPING,
}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return args.run(args)
    except (OSError, ValueError, UnknownDocumentKind) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ossie-neo4j",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    c = sub.add_parser("convert", help="convert in whichever direction the inputs imply")
    c.add_argument(
        "files",
        nargs="+",
        help="one OGS document, or up to one each of an Ossie ontology, semantic model and mapping",
    )
    _output_args(c, multi=True)
    _ossie_out_args(c)
    _neo4j_args(c, with_inputs=False)
    _conversion_args(c)
    c.set_defaults(run=_cmd_convert)

    c = sub.add_parser("to-ossie", help="convert an OGS document to Ossie documents")
    c.add_argument("file", help="an OGS document")
    _output_args(c, multi=True)
    _ossie_out_args(c)
    _conversion_args(c)
    c.set_defaults(run=_cmd_to_ossie)

    c = sub.add_parser("to-neo4j", help="convert Ossie documents to one OGS document")
    c.add_argument("file", help="an Ossie ontology, or a semantic model on its own")
    _output_args(c, multi=False)
    _neo4j_args(c, with_inputs=True)
    _conversion_args(c)
    c.set_defaults(run=_cmd_to_neo4j)

    v = sub.add_parser("validate", help="validate OGS or Ossie documents (auto-detected)")
    v.add_argument("files", nargs="+")
    v.add_argument("--json", action="store_true", help="print issues as JSON")
    v.set_defaults(run=lambda a: _validate(a.files, a.json))

    n = sub.add_parser("normalize", help="print the normalized (canonical) form of a document")
    n.add_argument("file")
    n.add_argument("--format", choices=["yaml", "json"], default="yaml")
    n.set_defaults(run=lambda a: _normalize(a.file, a.format))

    m = sub.add_parser("migrate", help="migrate an Ossie 0.1.1 (OSI) document to 0.3.0.dev0")
    m.add_argument("file")
    _output_args(m, multi=True)
    m.add_argument("-v", "--verbose", action="store_true", help="also print info messages")
    m.set_defaults(run=_cmd_migrate)
    return parser


def _output_args(p: argparse.ArgumentParser, *, multi: bool) -> None:
    p.add_argument("-o", "--out", help="output file (.yaml or .json); default: YAML to stdout")
    if multi:
        p.add_argument("--out-dir", help="write each output document to <dir>/<id>.<kind>.<format>")
        p.add_argument("--format", choices=["yaml", "json"], default="yaml")


def _ossie_out_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--only",
        action="append",
        choices=sorted(_ONLY),
        help="emit only this Ossie document (repeatable), e.g. --only semantic-model",
    )


def _neo4j_args(p: argparse.ArgumentParser, *, with_inputs: bool) -> None:
    if with_inputs:
        p.add_argument("--semantic-model", help="an Ossie semantic model to combine with FILE")
        p.add_argument("--mapping", help="an Ossie mapping document linking the two")
    p.add_argument(
        "--derive-graph",
        action="store_true",
        help="core-only mode: derive nodes, relationships and mappings from a semantic model "
        "given on its own",
    )


def _conversion_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--report", help="write the full loss report as JSON to this file")
    p.add_argument(
        "--strict", action="store_true", help="exit 2 if anything was approximated or dropped"
    )
    p.add_argument(
        "--no-stash",
        action="store_true",
        help="don't keep source-only data in vendor annotations / custom data",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="also print info messages about the inputs"
    )


# --- commands --------------------------------------------------------------------------------


def _cmd_to_ossie(args: argparse.Namespace) -> int:
    return _run(args, lambda: to_ossie(args.file, stash=not args.no_stash))


def _cmd_to_neo4j(args: argparse.Namespace) -> int:
    return _run(
        args,
        lambda: to_neo4j(
            args.file,
            semantic_model=args.semantic_model,
            mapping=args.mapping,
            stash=not args.no_stash,
            derive_graph=args.derive_graph,
        ),
    )


def _cmd_convert(args: argparse.Namespace) -> int:
    by_kind: dict[DocumentKind, str] = {}
    for f in args.files:
        doc = load(f)
        kind = DocumentKind.OSSIE_SEMANTIC_MODEL if is_legacy(doc) else detect_kind(doc)
        if kind in by_kind:
            print(f"error: two {kind} inputs: {by_kind[kind]} and {f}", file=sys.stderr)
            return 1
        by_kind[kind] = f
    if DocumentKind.OGS in by_kind:
        if len(by_kind) > 1:
            print("error: give either one OGS document or Ossie documents", file=sys.stderr)
            return 1
        return _run(args, lambda: to_ossie(by_kind[DocumentKind.OGS], stash=not args.no_stash))
    onto = by_kind.get(DocumentKind.OSSIE_ONTOLOGY)
    sm = by_kind.get(DocumentKind.OSSIE_SEMANTIC_MODEL)
    return _run(
        args,
        lambda: to_neo4j(
            onto or sm,
            semantic_model=sm if onto else None,
            mapping=by_kind.get(DocumentKind.OSSIE_MAPPING),
            stash=not args.no_stash,
            derive_graph=args.derive_graph,
        ),
    )


def _run(args: argparse.Namespace, convert) -> int:
    try:
        result: Conversion = convert()
    except ConversionError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    _print_issues(result.input_issues, args.verbose, prefix="input: ")

    docs = dict(result.documents)
    if getattr(args, "only", None):
        wanted = {_ONLY[o] for o in args.only}
        docs = {k: v for k, v in docs.items() if k in wanted}
        if not docs:
            print("error: none of the requested documents was produced", file=sys.stderr)
            return 1
    if not _write(docs, args, _output_stem(result)):
        return 1
    if args.report:
        Path(args.report).write_text(json.dumps(result.report.to_dict(), indent=2) + "\n")

    lossy = [e for e in result.report.entries if e.kind is not Loss.STASHED]
    stashed = len(result.report.entries) - len(lossy)
    summary = f"{result.report.direction}: {stashed} stashed, {len(lossy)} approximated/dropped"
    print(summary, file=sys.stderr)
    for e in lossy:
        print(f"  [{e.kind}] {e.code} at {e.path}: {e.message}", file=sys.stderr)
    return 2 if args.strict and lossy else 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    doc = load(args.file)
    if not is_legacy(doc):
        print(f"error: {args.file} is not an Ossie 0.1.x document", file=sys.stderr)
        return 1
    issues = validate_legacy_schema(doc)
    if issues:
        _print_issues(issues, True)
        return 1
    docs, issues = migrate(doc)
    _print_issues(issues, args.verbose)
    if len(docs) == 1:
        out = {DocumentKind.OSSIE_SEMANTIC_MODEL: docs[0]}
        return 0 if _write(out, args, kebab(docs[0]["name"])) else 1
    if args.out:
        print(f"error: {len(docs)} semantic models; use --out-dir", file=sys.stderr)
        return 1
    for d in docs:
        if not _write({DocumentKind.OSSIE_SEMANTIC_MODEL: d}, args, kebab(d["name"])):
            return 1
    return 0


# --- helpers ---------------------------------------------------------------------------------


def _write(docs: dict, args: argparse.Namespace, stem: str) -> bool:
    if getattr(args, "out_dir", None):
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for kind, doc in docs.items():
            path = out_dir / f"{stem}.{_SUFFIX[kind]}.{args.format}"
            dump(doc, path)
            print(f"wrote {path}", file=sys.stderr)
    elif args.out:
        if len(docs) > 1:
            names = ", ".join(docs)
            print(f"error: {len(docs)} output documents ({names}); use --out-dir", file=sys.stderr)
            return False
        dump(next(iter(docs.values())), args.out)
    else:
        print("---\n".join(dumps_yaml(d) for d in docs.values()), end="")
    return True


def _print_issues(issues: list[Issue], verbose: bool, prefix: str = "") -> None:
    infos = 0
    for i in issues:
        if i.severity is Severity.INFO and not verbose:
            infos += 1
            continue
        print(f"{prefix}{i}", file=sys.stderr)
    if infos:
        print(f"{prefix}({infos} info message(s); -v to show)", file=sys.stderr)


def _output_stem(result: Conversion) -> str:
    """A file-name stem for the outputs: the source model's id, else the kebab-case name."""
    doc = result.output
    return (doc.get("annotations") or {}).get("neo4j:id") or kebab(doc.get("name") or "model")


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
