from __future__ import annotations

import copy
import json

import pytest
from conftest import FIXTURES, fixture, minimal_ogs, minimal_ontology

from ossie_neo4j.cli import main
from ossie_neo4j.convert import ConversionError, Loss, to_neo4j, to_ossie
from ossie_neo4j.io import DocumentKind
from ossie_neo4j.pipeline import read

OGS_SOURCES = ["ogs/movies.yaml", "ogs/movies.json", "ogs/movies-import.yaml"]
ONTOLOGY_SOURCES = [
    "ossie/flights.ontology.yaml",
    "ossie/foaf_owl_import.yaml",
    "ossie/sap_business_partner.ontology.yaml",
]


def _normalized(doc) -> dict:
    out = read(doc).normalized.model_dump(mode="json", by_alias=True, exclude_none=True)
    out.pop("schema", None)  # OGS output always declares the current schema version
    return out


def _concept(onto: dict, name: str) -> dict:
    return next(c for c in onto["ontology"] if c["concept"] == name)


def _rel(concept: dict, name: str) -> dict:
    return next(r for r in concept.get("relationships", []) if r["name"] == name)


def _ann(d: dict) -> dict:
    return d.get("annotations") or {}


# --- round trips -----------------------------------------------------------------------------


def _back_to_neo4j(conversion, **kw) -> dict:
    """Convert every document of an OGS ➜ Ossie conversion back to OGS."""
    docs = conversion.documents
    onto = docs.get(DocumentKind.OSSIE_ONTOLOGY)
    sm = docs.get(DocumentKind.OSSIE_SEMANTIC_MODEL)
    if onto is None:
        return to_neo4j(sm, **kw).output
    return to_neo4j(onto, semantic_model=sm, **kw).output


@pytest.mark.parametrize("name", OGS_SOURCES)
def test_ogs_round_trip_is_lossless(name):
    src = FIXTURES / name
    back = _back_to_neo4j(to_ossie(src))
    assert _normalized(back) == _normalized(src)


@pytest.mark.parametrize("name", ONTOLOGY_SOURCES)
def test_ossie_round_trip_is_lossless(name):
    src = FIXTURES / name
    back = to_ossie(to_neo4j(src).output).output
    assert _normalized(back) == _normalized(src)


@pytest.mark.parametrize("name", OGS_SOURCES)
def test_ogs_round_trip_adds_no_ossie_stash(name):
    """Values that would be regenerated identically are never stashed, so there's no noise."""
    back = _back_to_neo4j(to_ossie(FIXTURES / name))
    assert '"ossie"' not in json.dumps(back)


def test_ossie_round_trip_adds_no_neo4j_annotations():
    back = to_ossie(to_neo4j(FIXTURES / "ossie/foaf_owl_import.yaml").output).output
    assert "neo4j:" not in json.dumps(back)


@pytest.mark.parametrize("name", OGS_SOURCES + ONTOLOGY_SOURCES)
def test_no_stash_still_produces_valid_output(name):
    convert = to_ossie if name.startswith("ogs/") else to_neo4j
    result = convert(FIXTURES / name, stash=False)
    text = json.dumps(result.documents)
    assert '"ossie"' not in text and "neo4j:" not in text
    assert all(e.kind is not Loss.STASHED for e in result.report.entries)


# --- OGS ➜ Ossie -----------------------------------------------------------------------------


def test_movies_to_ossie():
    onto = to_ossie(FIXTURES / "ogs/movies.yaml").output
    assert onto["name"] == "Movies"
    person = _concept(onto, "Person")
    assert person["description"] == "A person who acts in movies."
    assert _ann(person)["neo4j:aliases"] == ["Actor", "Performer"]
    name = _rel(person, "name")
    assert name["roles"] == [{"concept": "String"}]
    assert name["multiplicity"] == "ManyToOne"
    assert name["verbalizes"] == ["{Person} has name {String}"]
    # must_exist has no Ossie equivalent, so its EXISTS constraint is stashed.
    assert _ann(person)["neo4j:constraints"]["exists_person_name"]["type"] == "EXISTS"
    # A unique property is a OneToOne attribute.
    assert _rel(_concept(onto, "Movie"), "title")["multiplicity"] == "OneToOne"
    # A relationship property becomes an extra, named role.
    acted = _rel(person, "acted_in")
    assert acted["roles"] == [
        {"concept": "Movie"},
        {
            "concept": "String",
            "name": "role",
            "annotations": {"neo4j:property": {"description": "The character the person played."}},
        },
    ]
    assert acted["verbalizes"] == ["{Person} acted in {Movie} with role {String:role}"]


def test_types_keys_labels_and_counts():
    onto = to_ossie(FIXTURES / "ogs/movies-import.yaml").output
    zoned = _concept(onto, "Neo4jZonedDatetime")
    assert zoned["type"] == "ValueType" and zoned["extends"] == ["DateTime"]
    assert _ann(zoned) == {"neo4j:generated": True, "neo4j:type": "ZONED DATETIME"}

    movie = _concept(onto, "Movie")
    assert movie["identify_by"] == ["title", "released"]  # composite KEY
    assert "multiplicity" not in _rel(movie, "genres")  # LIST<STRING> is multi-valued
    assert _rel(movie, "premiered_at")["roles"] == [{"concept": "Neo4jZonedDatetime"}]

    person = _concept(onto, "Person")
    assert person["identify_by"] == ["person_id"]
    assert _rel(person, "person_id")["multiplicity"] == "OneToOne"

    actor = _concept(onto, "Actor")
    assert actor["extends"] == ["Person"]
    assert _rel(actor, "retired")["verbalizes"] == ["{Actor} is retired"]  # optional label

    # from.count 1 implies ManyToOne, but a relationship with properties is n-ary, so the exact
    # counts are stashed instead.
    reviewed = _rel(_concept(onto, "Review"), "reviewed")
    assert _ann(reviewed)["neo4j:from"] == {"count": 1}


@pytest.mark.parametrize(
    "from_end, to_end, multiplicity, stashed",
    [
        ({}, {}, None, False),
        ({"max_count": 1}, {}, "ManyToOne", False),
        ({"max_count": 1}, {"max_count": 1}, "OneToOne", False),
        ({"count": 1}, {}, "ManyToOne", True),
        ({}, {"min_count": 1}, None, True),
    ],
)
def test_counts_to_multiplicity(from_end, to_end, multiplicity, stashed):
    doc = minimal_ogs(
        nodes={"a": {"label": "A"}, "b": {"label": "B"}},
        relationships={
            "r": {"type": "R", "from": {"node": "a", **from_end}, "to": {"node": "b", **to_end}}
        },
    )
    result = to_ossie(doc)
    rel = _rel(_concept(result.output, "A"), "r")
    assert rel.get("multiplicity") == multiplicity
    assert ("neo4j:from" in _ann(rel) or "neo4j:to" in _ann(rel)) == stashed
    assert _normalized(to_neo4j(result.output).output) == _normalized(doc)


def test_self_relationship_names_target_role():
    doc = minimal_ogs(
        nodes={"p": {"label": "Person"}},
        relationships={"k": {"type": "KNOWS", "from": {"node": "p"}, "to": {"node": "p"}}},
    )
    rel = _rel(_concept(to_ossie(doc).output, "Person"), "knows")
    assert rel["roles"] == [{"concept": "Person", "name": "target"}]
    assert rel["verbalizes"] == ["{Person} knows {Person:target}"]


def test_implied_label_without_node_becomes_stub():
    doc = minimal_ogs(nodes={"a": {"labels": {"identifier": "Actor", "implied": ["Person"]}}})
    onto = to_ossie(doc).output
    assert _concept(onto, "Person") == {
        "concept": "Person",
        "type": "EntityType",
        "annotations": {"neo4j:stub": True},
    }
    back = to_neo4j(onto).output
    assert list(back["nodes"]) == ["a"]
    assert back["nodes"]["a"]["labels"]["implied"] == ["Person"]


def test_same_type_to_different_targets_gets_distinct_names():
    doc = minimal_ogs(
        nodes={"p": {"label": "Person"}, "m": {"label": "Movie"}, "b": {"label": "Book"}},
        relationships={
            "likes-movie": {"type": "LIKES", "from": {"node": "p"}, "to": {"node": "m"}},
            "likes-book": {"type": "LIKES", "from": {"node": "p"}, "to": {"node": "b"}},
        },
    )
    result = to_ossie(doc)
    names = [r["name"] for r in _concept(result.output, "Person")["relationships"]]
    assert names == ["likes", "likes_book"]
    assert _normalized(to_neo4j(result.output).output) == _normalized(doc)


def test_untyped_property_round_trips():
    doc = minimal_ogs(nodes={"n": {"label": "N", "properties": {"blob": {}}}})
    result = to_ossie(doc)
    rel = _rel(_concept(result.output, "N"), "blob")
    assert rel["roles"] == [{"concept": "Neo4jAny"}]
    assert _ann(rel)["neo4j:untyped"] is True
    assert _normalized(to_neo4j(result.output).output) == _normalized(doc)


# --- Ossie ➜ OGS -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def flights_ogs():
    return to_neo4j(FIXTURES / "ossie/flights.ontology.yaml")


def test_unary_relationships_become_optional_labels(flights_ogs):
    assert flights_ogs.output["nodes"]["flight"]["labels"]["optional"] == ["Canceled", "Diverted"]


def test_n_ary_relationships_are_reified(flights_ogs):
    out = flights_ogs.output
    node = out["nodes"]["flight-registers-longitude-series"]
    assert node["labels"]["identifier"] == "FlightRegistersLongitudeSeries"
    assert {k: v["type"] for k, v in node["properties"].items()} == {
        "date_time": "LOCAL DATETIME",
        "degrees_longitude": "FLOAT",
    }
    link = out["relationships"]["flight-registers-longitude-series"]
    assert (link["from"], link["to"]) == (
        {"node": "flight"},
        {"node": "flight-registers-longitude-series"},
    )
    assert "reified_n_ary" in flights_ogs.report.codes()


def test_link_types_come_from_verbalizations(flights_ogs):
    rels = flights_ogs.output["relationships"]
    assert rels["state"]["type"] == "IS_LOCATED_IN"  # '{City} is located in {State}'
    assert rels["state"]["from"] == {"node": "city", "max_count": 1}  # ManyToOne


def test_identify_by_with_links_is_not_a_key(flights_ogs):
    city = flights_ogs.output["nodes"]["city"]
    assert "constraints" not in city  # identify_by [name, state]: state is a link
    assert flights_ogs.report.codes().count("identify_by_not_key") == 3


def test_value_types_flatten_and_decimal_is_approximated(flights_ogs):
    runway = flights_ogs.output["nodes"]["runway"]
    assert runway["properties"]["length"]["type"] == "FLOAT"  # RunwayLength ➜ NrFeet ➜ Decimal
    assert "decimal_as_float" in flights_ogs.report.codes()


def test_inherited_identify_by_becomes_key_on_subtype():
    out = to_neo4j(FIXTURES / "ossie/sap_business_partner.ontology.yaml").output
    customer = out["nodes"]["customer"]
    assert customer["labels"]["implied"] == ["BusinessPartner"]
    assert customer["constraints"]["key_customer_nr"]["type"] == "KEY"
    nr = customer["properties"]["nr"]
    assert nr["extensions"]["custom"]["ossie"]["inherited_from"] == "BusinessPartner"


def test_multi_valued_attribute_becomes_list_and_any_role_is_open_end():
    nickname = {
        "name": "nickname",
        "roles": [{"concept": "String"}],
        "verbalizes": ["{Person} has nickname {String}"],
    }
    likes = {"name": "likes", "roles": [{"concept": "Any"}], "verbalizes": ["{Person} likes {Any}"]}
    onto = minimal_ontology(
        {"concept": "Person", "type": "EntityType", "relationships": [nickname, likes]}
    )
    out = to_neo4j(onto).output
    assert out["nodes"]["person"]["properties"]["nickname"]["type"] == "LIST<STRING>"
    assert out["relationships"]["likes"]["to"] == {}


# --- native wins -----------------------------------------------------------------------------


def test_edited_role_concept_wins_over_stashed_type():
    onto = to_ossie(FIXTURES / "ogs/movies-import.yaml").output
    edited = copy.deepcopy(onto)
    _rel(_concept(edited, "Movie"), "premiered_at")["roles"] = [{"concept": "Date"}]
    out = to_neo4j(edited).output
    assert out["nodes"]["movie"]["properties"]["premiered_at"]["type"] == "DATE"


def test_edited_multiplicity_wins_over_stashed_counts():
    doc = minimal_ogs(
        nodes={"a": {"label": "A"}, "b": {"label": "B"}},
        relationships={"r": {"type": "R", "from": {"node": "a", "count": 1}, "to": {"node": "b"}}},
    )
    onto = to_ossie(doc).output
    _rel(_concept(onto, "A"), "r")["multiplicity"] = "OneToOne"
    rel = to_neo4j(onto).output["relationships"]["r"]
    assert (rel["from"], rel["to"]) == (
        {"node": "a", "max_count": 1},
        {"node": "b", "max_count": 1},
    )


def test_edited_ogs_description_wins_over_stash():
    ogs = to_neo4j(FIXTURES / "ossie/foaf_owl_import.yaml").output
    ogs["nodes"]["person"]["description"] = "Edited in OGS."
    onto = to_ossie(ogs).output
    assert _concept(onto, "Person")["description"] == "Edited in OGS."
    assert _ann(_concept(onto, "Person"))["vs:term_status"] == "stable"  # stash still applied


# --- errors and CLI --------------------------------------------------------------------------


def test_wrong_input_kind():
    with pytest.raises(ConversionError, match="expected ogs"):
        to_ossie(FIXTURES / "ossie/minimal.yaml")


def test_invalid_input():
    bad = minimal_ogs(relationships={"r": {"type": "R", "from": {"node": "nope"}, "to": {}}})
    with pytest.raises(ConversionError) as e:
        to_ossie(bad)
    assert e.value.issues[0].code == "missing_relation_from_node"


def test_cli_to_ossie_and_back(tmp_path, capsys):
    onto = tmp_path / "movies.ontology.yaml"
    report = tmp_path / "loss.json"
    assert (
        main(
            [
                "to-ossie",
                str(FIXTURES / "ogs/movies.yaml"),
                "-o",
                str(onto),
                "--report",
                str(report),
            ]
        )
        == 0
    )
    assert json.loads(report.read_text())["direction"] == "ogs->ossie"
    back = tmp_path / "movies.json"
    assert main(["to-neo4j", str(onto), "-o", str(back)]) == 0
    assert _normalized(json.loads(back.read_text())) == _normalized(fixture("ogs/movies.yaml"))
    assert "ossie->ogs" in capsys.readouterr().err


def test_cli_strict_fails_on_lossy(capsys):
    assert main(["to-neo4j", str(FIXTURES / "ossie/flights.ontology.yaml"), "--strict"]) == 2
    assert "reified_n_ary" in capsys.readouterr().err


def test_cli_reports_conversion_errors(capsys):
    assert main(["to-neo4j", str(FIXTURES / "ogs/movies.yaml")]) == 1
    assert "expected ossie-ontology" in capsys.readouterr().err
