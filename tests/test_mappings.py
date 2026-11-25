"""Phase 3: OGS data_source_mappings ⇄ Ossie mapping document."""

from __future__ import annotations

import copy
import json

import pytest
from conftest import FIXTURES, fixture, minimal_ogs

from ossie_neo4j.cli import main
from ossie_neo4j.convert import ConversionError, Loss, to_neo4j, to_ossie
from ossie_neo4j.convert.mappings import column_ref, parse_column_ref
from ossie_neo4j.io import DocumentKind
from ossie_neo4j.pipeline import read

ONTO = DocumentKind.OSSIE_ONTOLOGY
SM = DocumentKind.OSSIE_SEMANTIC_MODEL
MAP = DocumentKind.OSSIE_MAPPING
TRIOS = ["flights", "sap_business_partner"]


def _normalized(doc) -> dict:
    out = read(doc).normalized.model_dump(mode="json", by_alias=True, exclude_none=True)
    out.pop("schema", None)
    return out


def _trio(base: str) -> tuple:
    return tuple(
        FIXTURES / f"ossie/{base}.{k}.yaml" for k in ("ontology", "semantic_model", "mapping")
    )


def _trio_to_neo4j(base: str, **kw):
    onto, sm, mapping = _trio(base)
    return to_neo4j(onto, semantic_model=sm, mapping=mapping, **kw)


def _docs_to_neo4j(docs: dict, **kw):
    return to_neo4j(docs[ONTO], semantic_model=docs[SM], mapping=docs.get(MAP), **kw)


def _cm(mapping: dict, concept: str) -> dict:
    return next(c for c in mapping["concept_mappings"] if c["concept"] == concept)


@pytest.fixture(scope="module")
def movies():
    return to_ossie(FIXTURES / "ogs/movies-import.yaml")


# --- round trips -----------------------------------------------------------------------------


def test_ogs_mappings_round_trip_losslessly(movies):
    assert set(movies.documents) == {ONTO, SM, MAP}
    back = _docs_to_neo4j(movies.documents).output
    assert _normalized(back) == _normalized(FIXTURES / "ogs/movies-import.yaml")
    assert '"ossie"' not in json.dumps(back)  # nothing needed stashing


@pytest.mark.parametrize("base", TRIOS)
def test_ossie_trios_round_trip_losslessly(base):
    docs = to_ossie(_trio_to_neo4j(base).output).documents
    for kind, src in zip((ONTO, SM, MAP), _trio(base), strict=True):
        assert _normalized(docs[kind]) == _normalized(src), kind


# --- OGS ➜ Ossie -----------------------------------------------------------------------------


def test_mapping_document_header(movies):
    m = movies.documents[MAP]
    assert m["name"] == "movies_import_mapping"
    assert m["ontology_ref"] == {"name": "Movies (import)", "iri": "./movies-import.ontology.yaml"}
    assert m["semantic_model_ref"]["iri"] == "./movies-import.semantic_model.yaml"


def test_node_mapping(movies):
    person = _cm(movies.documents[MAP], "Person")
    identity = {"referent_mappings": [{"relationship": "person_id", "expression": "people.id"}]}
    assert person["object_mappings"] == [identity]
    assert person["link_mappings"] == [
        {
            "object_mapping": identity,
            "children": [
                {
                    "object_mapping": {"concept": "String", "expression": "people.full_name"},
                    "relationship": "name",
                },
                {
                    "object_mapping": {"concept": "Date", "expression": "people.birth_date"},
                    "relationship": "born",
                },
            ],
        }
    ]


def test_relationship_mapping_follows_roles(movies):
    actor = _cm(movies.documents[MAP], "Actor")
    root = actor["link_mappings"][0]
    assert root["object_mapping"]["referent_mappings"][0]["expression"] == "cast.person_id"
    target = root["children"][0]
    assert target["object_mapping"]["concept"] == "Movie"
    assert "relationship" not in target  # the binary prefix of a ternary relationship
    role = target["children"][0]
    assert role == {
        "object_mapping": {"concept": "String", "expression": "cast.role"},
        "relationship": "acted_in",
    }


def test_ogs_only_details_in_neo4j_extension(movies):
    ext = movies.documents[MAP]["nonportable_extensions"][0]
    assert ext["vendor_name"] == "NEO4J"
    entries = ext["data"]["sets"]["default"]
    assert entries[0] == {
        "type": "NODE",
        "node": "person",
        "table": "people",
        "id": "map-person",
        "mode": "MERGE",
    }
    assert entries[3]["raw"]["type"] == "QUERY"
    assert "mapping_query" in movies.report.codes()


def test_no_stash_drops_ogs_only_details():
    result = to_ossie(FIXTURES / "ogs/movies-import.yaml", stash=False)
    assert "nonportable_extensions" not in result.documents[MAP]
    assert any(e.kind is Loss.DROPPED and e.code == "mapping_query" for e in result.report.entries)


def test_mappings_without_nodes_stay_on_semantic_model():
    tables = {"t": {"source": "s", "columns": [{"name": "c"}]}}
    mappings = {"default": [{"type": "QUERY", "table": "t", "query": "RETURN 1"}]}
    doc = minimal_ogs(extensions={"data_source_tables": tables, "data_source_mappings": mappings})
    result = to_ossie(doc)
    assert set(result.documents) == {SM}
    back = to_neo4j(result.documents[SM]).output
    assert back["extensions"]["data_source_mappings"] == mappings


@pytest.mark.parametrize(
    "dataset, field",
    [("people", "id"), ("CUSTOMER SALES", "full name"), ('we"ird', "x")],
)
def test_column_refs_round_trip(dataset, field):
    assert parse_column_ref(column_ref(dataset, field)) == (dataset, field)


def test_column_ref_rejects_expressions():
    assert parse_column_ref("FLIGHT.id WHERE ( FLIGHT.diverted == TRUE )") is None
    assert parse_column_ref("UPPER(T.x)") is None


# --- Ossie ➜ OGS -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def flights():
    return _trio_to_neo4j("flights")


def _mappings(result) -> list[dict]:
    return result.output["extensions"]["data_source_mappings"]["default"]


def test_object_and_link_mappings_merge_into_node_mappings(flights):
    airport = next(m for m in _mappings(flights) if m.get("node") == "airport")
    assert airport["table"] == "AIRPORT"
    assert airport["key"] == ["code"]
    assert airport["properties"] == {
        "code": {"column": "code"},
        "longitude": {"column": "longitude"},
        "name": {"column": "name"},
        "latitude": {"column": "latitude"},
    }


def test_link_children_become_relationship_mappings(flights):
    operated = next(m for m in _mappings(flights) if m.get("relationship") == "operated-by")
    assert operated == {
        "type": "RELATIONSHIP",
        "relationship": "operated-by",
        "table": "FLIGHT",
        "from_node": {"label": "Flight", "properties": {"id": {"column": "id"}}},
        "to_node": {"label": "Carrier", "properties": {"code": {"column": "carrier_code"}}},
    }


def test_inexpressible_constructs_are_reported_and_document_kept(flights):
    codes = flights.report.codes()
    # Runway is identified through Airport; Flight's canceled / diverted flags are unary.
    assert codes.count("mapping_nested_referent") == 7
    assert codes.count("mapping_unary") == 2
    assert "mapping_document_kept" in codes
    kept = flights.output["extensions"]["custom"]["ossie"]["mapping"]
    assert kept["document"]["name"] == "flights_mapping"
    assert not any(m.get("node") == "runway" for m in _mappings(flights))


def test_edited_ogs_mappings_win_over_kept_document(flights):
    ogs = copy.deepcopy(flights.output)
    _mappings_list = ogs["extensions"]["data_source_mappings"]["default"]
    _mappings_list[:] = [m for m in _mappings_list if m.get("node") != "manufacturer"]
    mapping = to_ossie(ogs).documents[MAP]
    assert "Manufacturer" not in [c["concept"] for c in mapping["concept_mappings"]]
    assert "Runway" not in [c["concept"] for c in mapping["concept_mappings"]]  # not expressible


def test_fully_expressible_mapping_is_not_kept_whole(movies):
    docs = copy.deepcopy(movies.documents)
    del docs[MAP]["nonportable_extensions"]  # as if written by hand, without OGS details
    out = _docs_to_neo4j(docs).output
    assert "mapping" not in out["extensions"].get("custom", {}).get("ossie", {})
    assert [m["type"] for m in out["extensions"]["data_source_mappings"]["default"]] == [
        "NODE",
        "NODE",
        "RELATIONSHIP",
    ]


def test_mapping_needs_ontology_and_semantic_model():
    onto, sm, mapping = _trio("flights")
    with pytest.raises(ConversionError, match="needs both"):
        to_neo4j(onto, mapping=mapping)


def test_mapping_ref_mismatch_warns():
    onto, sm, mapping = _trio("flights")
    md = fixture("ossie/flights.mapping.yaml")
    md["ontology_ref"]["name"] = "Something else"
    result = to_neo4j(onto, semantic_model=sm, mapping=md)
    assert [i.code for i in result.input_issues] == ["mapping_ref_mismatch"]


@pytest.mark.parametrize("base", TRIOS)
def test_no_stash_trios_are_valid_and_clean(base):
    result = _trio_to_neo4j(base, stash=False)
    assert '"ossie"' not in json.dumps(result.output)


# --- CLI -------------------------------------------------------------------------------------


def test_cli_mapping_round_trip(tmp_path):
    onto, sm, mapping = _trio("sap_business_partner")
    ogs = tmp_path / "sap.ogs.yaml"
    args = ["to-neo4j", str(onto), "--semantic-model", str(sm), "--mapping", str(mapping)]
    assert main([*args, "-o", str(ogs)]) == 0
    out = tmp_path / "out"
    assert main(["to-ossie", str(ogs), "--out-dir", str(out)]) == 0
    back = out / "sap-business-partner.mapping.yaml"
    assert _normalized(back) == _normalized(mapping)
