from __future__ import annotations

import json

import pytest
from conftest import ALL_FIXTURES, FIXTURES, minimal_ogs

from ossie_neo4j.cli import main
from ossie_neo4j.pipeline import read


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_every_fixture_reads_cleanly(path):
    loaded = read(path)
    assert loaded.ok, [str(i) for i in loaded.issues]
    assert loaded.normalized is not None


def test_schema_errors_stop_before_rules():
    # This document breaks the schema (unknown key) and a rule (dangling relationship end).
    # Only the schema issue is reported, because later stages don't run.
    doc = minimal_ogs(bogus=1, relationships={"r": {"type": "R", "from": {"node": "x"}, "to": {}}})
    loaded = read(doc)
    assert [i.code for i in loaded.issues] == ["schema_violation"]
    assert loaded.model is None


def test_rule_errors_stop_before_normalize():
    doc = minimal_ogs(relationships={"r": {"type": "R", "from": {"node": "x"}, "to": {}}})
    loaded = read(doc)
    assert [i.code for i in loaded.issues] == ["missing_relation_from_node"]
    assert loaded.model is not None and loaded.normalized is None


def test_cli_validate_ok(capsys):
    assert main(["validate", str(FIXTURES / "ogs/movies.yaml")]) == 0
    assert ": ogs: ok" in capsys.readouterr().out


def test_cli_validate_failure_json(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps(minimal_ogs(relationships={"r": {"type": "R", "from": {"node": "x"}, "to": {}}}))
    )
    assert main(["validate", "--json", str(bad)]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report[0]["issues"][0]["code"] == "missing_relation_from_node"


def test_cli_normalize(capsys):
    assert main(["normalize", "--format", "json", str(FIXTURES / "ogs/movies.yaml")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["nodes"]["person"]["labels"] == {"identifier": "Person"}
