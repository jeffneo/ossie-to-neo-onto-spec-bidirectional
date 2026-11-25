from __future__ import annotations

import pytest
from conftest import fixture, minimal_ontology

from ossie_neo4j.ossie.models import AIContextObject, Ontology, SemanticModel
from ossie_neo4j.ossie.normalize import compact_iri, expand_iri, normalize
from ossie_neo4j.ossie.rules import check_ontology, check_semantic_model

# --- normalize -------------------------------------------------------------------------------


def test_string_ai_context_becomes_object(minimal):
    minimal["ai_context"] = "Use for order analytics"
    minimal["datasets"][0]["fields"][0]["ai_context"] = "the id"
    out, issues = normalize(SemanticModel.model_validate(minimal))
    assert issues == []
    assert out.ai_context == AIContextObject(instructions="Use for order analytics")
    assert out.datasets[0].fields[0].ai_context.instructions == "the id"


def test_object_ai_context_kept_with_extras(minimal):
    minimal["ai_context"] = {"synonyms": ["sales"], "vendor_hint": 1}
    out, _ = normalize(SemanticModel.model_validate(minimal))
    assert out.ai_context.synonyms == ["sales"]
    assert out.ai_context.model_extra == {"vendor_hint": 1}


def test_foaf_iris_expanded():
    onto = Ontology.model_validate(fixture("ossie/foaf_owl_import.yaml"))
    out, issues = normalize(onto)
    assert issues == []
    person = next(c for c in out.ontology if c.concept == "Person")
    assert person.iri == "http://xmlns.com/foaf/0.1/Person"
    assert person.relationships[0].iri == "http://xmlns.com/foaf/0.1/name"
    # The input is not mutated, and the prefixes are kept so the IRIs can be compacted later.
    assert onto.ontology[0].iri.startswith("foaf:")
    assert out.prefixes == onto.prefixes


def test_unknown_prefix_warns_and_is_left_alone():
    doc = minimal_ontology({"concept": "X", "type": "EntityType", "iri": "ex:X"})
    out, issues = normalize(Ontology.model_validate(doc))
    assert out.ontology[0].iri == "ex:X"
    assert [(i.code, i.severity) for i in issues] == [("unknown_iri_prefix", "warning")]


@pytest.mark.parametrize("iri", ["foaf:Person", "http://example.com/x", "urn:isbn:1"])
def test_expand_compact_round_trip(iri):
    prefixes = {"foaf": "http://xmlns.com/foaf/0.1/"}
    expanded, _ = expand_iri(iri, prefixes)
    assert compact_iri(expanded, prefixes) == iri


# --- semantic model rules --------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "ossie/minimal.yaml",
        "ossie/tpcds_semantic_model.yaml",
        "ossie/flights.semantic_model.yaml",
        "ossie/sap_business_partner.semantic_model.yaml",
    ],
)
def test_semantic_model_fixtures_follow_rules(name):
    assert check_semantic_model(SemanticModel.model_validate(fixture(name))) == []


def _sm_codes(doc: dict) -> list[str]:
    return sorted(i.code for i in check_semantic_model(SemanticModel.model_validate(doc)))


def test_case_insensitive_duplicates(minimal):
    ds = minimal["datasets"][0]
    minimal["datasets"].append({**ds, "name": "ORDERS", "fields": list(ds["fields"])})
    ds["fields"].append({"name": "ORDER_ID", "expression": {"sql": "x"}})
    assert _sm_codes(minimal) == ["duplicate_dataset_name", "duplicate_field_name"]


def test_key_rules(minimal):
    minimal["datasets"][0]["primary_key"] = ["order_id", "nope"]
    minimal["datasets"][0]["unique_keys"] = [["order_id", "ORDER_ID"]]
    assert _sm_codes(minimal) == ["duplicate_key_field", "unknown_key_field"]


def test_relationship_rules(minimal):
    minimal["relationships"] = [
        {
            "name": "r1",
            "from": "orders",
            "to": "customers",
            "from_columns": ["order_id"],
            "to_columns": ["id"],
        },
        {
            "name": "r2",
            "from": "Orders",
            "to": "orders",
            "from_columns": ["order_id", "x"],
            "to_columns": ["ORDER_ID"],
        },
    ]
    assert _sm_codes(minimal) == [
        "relationship_column_count_mismatch",
        "unknown_relationship_dataset",
        "unknown_relationship_field",
    ]


# --- ontology rules --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "ossie/flights.ontology.yaml",
        "ossie/foaf_owl_import.yaml",
        "ossie/sap_business_partner.ontology.yaml",
    ],
)
def test_ontology_fixtures_follow_rules(name):
    assert check_ontology(Ontology.model_validate(fixture(name))) == []


def _onto_codes(*concepts: dict) -> list[str]:
    return sorted(
        i.code for i in check_ontology(Ontology.model_validate(minimal_ontology(*concepts)))
    )


def test_unknown_and_invalid_extends():
    assert _onto_codes(
        {"concept": "A", "type": "EntityType", "extends": ["Nope"]},
        {"concept": "B", "type": "EntityType", "extends": ["String"]},
    ) == ["invalid_extends", "unknown_concept"]


def test_value_type_needs_builtin_base_and_no_cycles():
    assert _onto_codes(
        {"concept": "V", "type": "ValueType"},
        {"concept": "X", "type": "ValueType", "extends": ["Y"]},
        {"concept": "Y", "type": "ValueType", "extends": ["X"]},
    ) == ["extends_cycle", "extends_cycle", "value_type_without_builtin_base"]


def test_duplicates_and_builtins():
    assert _onto_codes(
        {"concept": "String", "type": "ValueType"},
        {"concept": "A", "type": "EntityType"},
        {"concept": "A", "type": "EntityType"},
    ) == ["duplicate_concept", "redefines_builtin_concept"]


def test_relationship_rules_ontology():
    rel = {
        "name": "r",
        "verbalizes": ["{A} r {B} {C}"],
        "roles": [{"concept": "B"}, {"concept": "Nope"}],
    }
    assert _onto_codes(
        {
            "concept": "A",
            "type": "EntityType",
            "relationships": [{**rel, "multiplicity": "OneToOne"}, rel],
        },
        {"concept": "B", "type": "EntityType"},
    ) == ["duplicate_relationship", "one_to_one_not_binary", "unknown_concept", "unknown_concept"]


def test_identify_by_may_use_inherited_relationship():
    base = {
        "concept": "Base",
        "type": "EntityType",
        "relationships": [
            {
                "name": "nr",
                "roles": [{"concept": "Integer"}],
                "verbalizes": ["{Base} has {Integer}"],
            }
        ],
    }
    assert (
        _onto_codes(
            base,
            {"concept": "Sub", "type": "EntityType", "extends": ["Base"], "identify_by": ["nr"]},
        )
        == []
    )
    assert _onto_codes(base, {"concept": "Other", "type": "EntityType", "identify_by": ["nr"]}) == [
        "unknown_identifying_relationship"
    ]
