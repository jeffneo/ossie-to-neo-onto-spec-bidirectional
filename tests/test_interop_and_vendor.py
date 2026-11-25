"""The apache/ossie-converters interop contract, the NEO4J vendor validator, and schema drift."""

from __future__ import annotations

import json
from importlib.metadata import entry_points
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FIXTURES

from ossie_neo4j.cli import main
from ossie_neo4j.io import load
from ossie_neo4j.pipeline import read
from ossie_neo4j.vendor import check

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "src/ossie_neo4j/schemas/ossie-0.3.0.dev0"
# The apache/ossie checkout: research/ossie here, the `ossie` submodule in ossie-converters.
OSSIE_CHECKOUTS = [ROOT / "research/ossie", ROOT.parent / "ossie"]


def _site(kind: str) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, name="m", dataset=None, node={})


# --- interop contract ------------------------------------------------------------------------


def test_export_then_import_preserves_the_semantic_model(tmp_path):
    src = FIXTURES / "ossie/tpcds_semantic_model.yaml"
    ogs, back = tmp_path / "x.ogs.yaml", tmp_path / "x.sm.yaml"
    assert main(["export", "-i", str(src), "-o", str(ogs)]) == 0
    assert len(load(ogs)["nodes"]) == 5  # derived graph
    assert main(["import", "-i", str(ogs), "-o", str(back)]) == 0

    def norm(p):
        return read(p).normalized.model_dump(mode="json", by_alias=True, exclude_none=True)

    assert norm(back) == norm(src)


def test_import_needs_tables(tmp_path, capsys):
    out = tmp_path / "x.yaml"
    assert main(["import", "-i", str(FIXTURES / "ogs/movies.yaml"), "-o", str(out)]) == 1
    assert "no data_source_tables" in capsys.readouterr().err


# --- vendor validator ------------------------------------------------------------------------


def test_vendor_validator_is_registered():
    eps = entry_points(group="ossie.vendor_validators")
    assert eps["NEO4J"].load() is check


def test_vendor_validator_accepts_what_we_write(tmp_path):
    out = tmp_path / "out"
    assert main(["to-ossie", str(FIXTURES / "ogs/movies-import.yaml"), "--out-dir", str(out)]) == 0
    sm = load(out / "movies-import.semantic_model.yaml")
    sites = []
    for d in sm["datasets"]:
        sites += [("dataset", e) for e in d.get("nonportable_extensions", [])]
        for f in d.get("fields", []):
            sites += [("field", e) for e in f.get("nonportable_extensions", [])]
    for r in sm.get("relationships", []):
        sites += [("relationship", e) for e in r.get("nonportable_extensions", [])]
    mapping = load(out / "movies-import.mapping.yaml")
    sites += [("mapping", e) for e in mapping["nonportable_extensions"]]
    assert sites
    for kind, ext in sites:
        assert ext["vendor_name"] == "NEO4J"
        assert check(ext["data"], _site(kind)) == [], (kind, ext)


@pytest.mark.parametrize(
    "kind, payload, expected",
    [
        ("field", {"suggested": "TEXT"}, ["NEO4J 'suggested' is not a Neo4j type: 'TEXT'"]),
        ("field", {"size": True}, ["NEO4J 'size' has the wrong type (bool)"]),
        ("field", {"supported": ["STRING", "NOPE"]}, ["NEO4J 'supported' lists an unknown"]),
        ("dataset", {"bogus": 1}, [{"message": "unknown NEO4J key 'bogus'"}]),
        ("metric", {}, [{"message": "ossie-neo4j writes no NEO4J data on a metric"}]),
        ("field", {"suggested": None, "type": "VARCHAR"}, []),
    ],
)
def test_vendor_validator_findings(kind, payload, expected):
    found = check(payload, _site(kind))
    assert len(found) == len(expected)
    for f, e in zip(found, expected, strict=True):
        if isinstance(e, str):
            assert isinstance(f, str) and f.startswith(e)
        else:
            assert f["message"] == e["message"] and f["severity"] == "warning"


# --- vendored schemas ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vendored, upstream",
    [
        ("ossie-schema.json", "core-spec/ossie-schema.json"),
        ("ontology.json", "ontology/ontology.json"),
        ("mapping.json", "ontology/mapping.json"),
    ],
)
def test_vendored_schemas_match_the_ossie_checkout(vendored, upstream):
    checkout = next((c for c in OSSIE_CHECKOUTS if (c / upstream).is_file()), None)
    if checkout is None:
        pytest.skip("no apache/ossie checkout next to this package")
    assert json.loads((SCHEMAS / vendored).read_text()) == json.loads(
        (checkout / upstream).read_text()
    ), f"{vendored} drifted from {checkout / upstream}; re-vendor it (schemas/README.md)"
