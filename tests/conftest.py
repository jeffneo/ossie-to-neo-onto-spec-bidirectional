from __future__ import annotations

import copy
from pathlib import Path

import pytest

from ossie_neo4j.io import load

FIXTURES = Path(__file__).parent / "fixtures"
OGS_FIXTURES = sorted((FIXTURES / "ogs").glob("*.[yj]*"))
OSSIE_FIXTURES = sorted((FIXTURES / "ossie").glob("*.[yj]*"))
ALL_FIXTURES = OGS_FIXTURES + OSSIE_FIXTURES


def fixture(rel: str) -> dict:
    return load(FIXTURES / rel)


@pytest.fixture
def movies() -> dict:
    return copy.deepcopy(fixture("ogs/movies.yaml"))


@pytest.fixture
def minimal() -> dict:
    return copy.deepcopy(fixture("ossie/minimal.yaml"))


def minimal_ogs(**overrides) -> dict:
    doc = {
        "schema": "https://neo4j.com/docs/ontology-graph-spec/1.1.0/schema.json",
        "version": "1",
        "id": "t",
    }
    doc.update(overrides)
    return doc


def minimal_ontology(*concepts: dict, **overrides) -> dict:
    doc = {"version": "0.3.0.dev0", "name": "t", "ontology": list(concepts)}
    doc.update(overrides)
    return doc
