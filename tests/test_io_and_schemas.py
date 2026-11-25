from __future__ import annotations

import pytest
from conftest import ALL_FIXTURES, minimal_ogs

from ossie_neo4j.io import DocumentKind, UnknownDocumentKind, detect_kind, load
from ossie_neo4j.schemas import validate_schema

EXPECTED_KIND = {
    "movies": DocumentKind.OGS,
    "movies.longhand": DocumentKind.OGS,
    "movies-import": DocumentKind.OGS,
    "minimal": DocumentKind.OSSIE_SEMANTIC_MODEL,
    "tpcds_semantic_model": DocumentKind.OSSIE_SEMANTIC_MODEL,
    "flights.semantic_model": DocumentKind.OSSIE_SEMANTIC_MODEL,
    "flights.ontology": DocumentKind.OSSIE_ONTOLOGY,
    "flights.mapping": DocumentKind.OSSIE_MAPPING,
    "foaf_owl_import": DocumentKind.OSSIE_ONTOLOGY,
    "sap_business_partner.semantic_model": DocumentKind.OSSIE_SEMANTIC_MODEL,
    "sap_business_partner.ontology": DocumentKind.OSSIE_ONTOLOGY,
    "sap_business_partner.mapping": DocumentKind.OSSIE_MAPPING,
}


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_detect_kind(path):
    assert detect_kind(load(path)) == EXPECTED_KIND[path.stem]


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_fixtures_are_schema_valid(path):
    doc = load(path)
    assert validate_schema(doc, detect_kind(doc)) == []


def test_unknown_kind():
    with pytest.raises(UnknownDocumentKind):
        detect_kind({"hello": "world"})


def test_yaml_and_json_movies_are_identical():
    from conftest import fixture

    assert fixture("ogs/movies.yaml") == fixture("ogs/movies.json")


def test_ogs_schema_rejects_unknown_keys():
    issues = validate_schema(minimal_ogs(colour="red"), DocumentKind.OGS)
    assert [i.code for i in issues] == ["schema_violation"]


def test_ogs_schema_rejects_bad_type():
    doc = minimal_ogs(nodes={"n": {"properties": {"p": {"type": "TEXT"}}}})
    issues = validate_schema(doc, DocumentKind.OGS)
    assert issues and issues[0].path == "nodes.n.properties.p.type"


def test_ossie_schema_pins_version(minimal):
    minimal["version"] = "0.1.1"
    issues = validate_schema(minimal, DocumentKind.OSSIE_SEMANTIC_MODEL)
    assert [i.path for i in issues] == ["version"]


def test_ossie_cross_file_refs_resolve_offline():
    # mapping.json uses $refs to raw.githubusercontent.com URLs. An invalid nested
    # ConceptMapping proves that those refs resolved to the vendored copy of ontology.json.
    doc = {
        "version": "0.3.0.dev0",
        "name": "m",
        "ontology_ref": {"name": "o"},
        "semantic_model_ref": {"name": "s"},
        "concept_mappings": [{"concept": "X"}],  # needs object_mappings or link_mappings
    }
    issues = validate_schema(doc, DocumentKind.OSSIE_MAPPING)
    assert issues and issues[0].path == "concept_mappings.0"
