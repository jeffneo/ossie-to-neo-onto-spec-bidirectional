from __future__ import annotations

import pytest
from conftest import fixture, minimal_ogs

from ossie_neo4j.ogs.models import ConstraintType, GraphModel, to_dict
from ossie_neo4j.ogs.normalize import normalize
from ossie_neo4j.ogs.rules import check_rules


def _norm(doc: dict) -> tuple[dict, list]:
    model, issues = normalize(GraphModel.model_validate(doc))
    return to_dict(model), issues


# --- normalize -------------------------------------------------------------------------------


def test_movies_normalizes_to_spec_longhand(movies):
    out, issues = _norm(movies)
    assert issues == []
    assert out == fixture("ogs/movies.longhand.yaml")


def test_normalize_is_idempotent(movies):
    once, _ = _norm(movies)
    twice, _ = _norm(once)
    assert once == twice


def test_normalize_does_not_mutate_input(movies):
    model = GraphModel.model_validate(movies)
    normalize(model)
    assert model.nodes["person"].label == "Person"


def test_existing_longhand_constraint_not_duplicated():
    doc = minimal_ogs(
        nodes={
            "n": {
                "label": "N",
                "properties": {"p": {"type": "STRING", "unique": True}},
                "constraints": {"mine": {"type": "UNIQUE", "properties": ["p"]}},
            }
        }
    )
    out, _ = _norm(doc)
    assert list(out["nodes"]["n"]["constraints"]) == ["mine"]


def test_key_and_relationship_flags():
    out, _ = _norm(fixture("ogs/movies-import.yaml"))
    person = out["nodes"]["person"]["constraints"]
    assert person["key_person_person_id"] == {
        "type": "KEY",
        "properties": ["person_id"],
        "name": "person_id_Person_key",
    }
    acted = out["relationships"]["acted-in"]["constraints"]
    assert acted["exists_acted-in_roles"]["name"] == "roles_ACTED_IN_exists"
    # Composite keys written in longhand are kept unchanged.
    assert out["nodes"]["movie"]["constraints"]["movie-key"]["properties"] == ["title", "released"]


def test_false_flags_are_dropped_without_constraints():
    doc = minimal_ogs(nodes={"n": {"label": "N", "properties": {"p": {"unique": False}}}})
    out, _ = _norm(doc)
    assert "constraints" not in out["nodes"]["n"]
    assert out["nodes"]["n"]["properties"]["p"] == {}


def test_constraint_id_collision_gets_suffix():
    doc = minimal_ogs(
        nodes={
            "n": {
                "label": "N",
                "properties": {"p": {"must_exist": True}, "q": {}},
                "constraints": {"exists_n_p": {"type": "UNIQUE", "properties": ["q"]}},
            }
        }
    )
    out, _ = _norm(doc)
    assert out["nodes"]["n"]["constraints"]["exists_n_p_2"]["type"] == ConstraintType.EXISTS


def test_conflicting_label_reported():
    doc = minimal_ogs(nodes={"n": {"label": "A", "labels": {"identifier": "B"}}})
    out, issues = _norm(doc)
    assert [i.code for i in issues] == ["conflicting_identifier_label"]
    assert out["nodes"]["n"] == {"labels": {"identifier": "B"}}


def test_nulls_are_absent():
    doc = minimal_ogs(
        nodes={"n": {"label": None, "properties": {"p": {"key": None, "name": None}}}}
    )
    out, _ = _norm(doc)
    assert out["nodes"]["n"] == {"properties": {"p": {}}}


# --- rules -----------------------------------------------------------------------------------


def _codes(doc: dict) -> list[str]:
    return [i.code for i in check_rules(GraphModel.model_validate(doc))]


@pytest.mark.parametrize(
    "name", ["ogs/movies.yaml", "ogs/movies-import.yaml", "ogs/movies.longhand.yaml"]
)
def test_fixtures_follow_rules(name):
    # movies.yaml declares schema 1.0.0, which we read, so it raises no warning either.
    assert _codes(fixture(name)) == []


def test_unknown_schema_version_warns():
    assert _codes(minimal_ogs(schema="https://example.com/9.9/schema.json")) == [
        "unknown_schema_version"
    ]


def test_missing_relationship_ends():
    doc = minimal_ogs(
        nodes={"a": {}},
        relationships={"r": {"type": "R", "from": {"node": "a"}, "to": {"node": "nope"}}},
    )
    assert _codes(doc) == ["missing_relation_to_node"]


@pytest.mark.parametrize(
    "end, code",
    [
        ({"count": 1, "min_count": 0}, "count_with_min_max"),
        ({"count": -1}, "negative_count"),
        ({"min_count": 3, "max_count": 2}, "min_greater_than_max"),
    ],
)
def test_count_rules(end, code):
    doc = minimal_ogs(relationships={"r": {"type": "R", "from": {}, "to": end}})
    assert _codes(doc) == [code]


def test_constraint_rules():
    doc = minimal_ogs(
        nodes={
            "n": {
                "properties": {"a": {}, "b": {}},
                "constraints": {
                    "c1": {"type": "EXISTS", "properties": ["a", "b"]},
                    "c2": {"type": "UNIQUE", "properties": ["zzz"]},
                },
            }
        }
    )
    assert sorted(_codes(doc)) == ["constraint_property_count", "missing_constraint_property"]


def test_index_rules():
    doc = minimal_ogs(
        nodes={
            "n": {
                "properties": {"a": {}},
                "extensions": {
                    "indexes": {
                        "i": {
                            "type": "RANGE",
                            "labels": ["N"],
                            "properties": ["b"],
                            "options": {"type": "VECTOR"},
                        }
                    }
                },
            }
        }
    )
    assert sorted(_codes(doc)) == ["index_options_type_mismatch", "missing_index_property"]


def test_pattern_and_one_of():
    doc = minimal_ogs(nodes={"n": {"properties": {"a": {"pattern": ".*", "one_of": ["x"]}}}})
    assert _codes(doc) == ["pattern_and_one_of"]


def test_relationship_mapping_key():
    doc = minimal_ogs(
        nodes={"a": {}},
        relationships={
            "r": {"type": "R", "from": {"node": "a"}, "to": {"node": "a"}, "properties": {"p": {}}}
        },
        extensions={
            "data_source_mappings": {
                "s": [
                    {
                        "type": "RELATIONSHIP",
                        "relationship": "r",
                        "table": "t",
                        "from_node": {},
                        "to_node": {},
                        "key": ["p", "q"],
                    }
                ]
            }
        },
    )
    assert _codes(doc) == ["invalid_relationship_mapping_key"]


def test_length_rule():
    assert _codes(minimal_ogs(name="x" * 257)) == ["too_long"]
