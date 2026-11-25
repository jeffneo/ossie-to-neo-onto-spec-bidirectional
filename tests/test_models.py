from __future__ import annotations

import pytest
from conftest import ALL_FIXTURES, fixture

from ossie_neo4j.io import load
from ossie_neo4j.ogs import models as ogs
from ossie_neo4j.ossie import models as ossie
from ossie_neo4j.pipeline import _MODEL_FOR, detect_kind


def _drop_nulls(o):
    if isinstance(o, dict):
        return {k: _drop_nulls(v) for k, v in o.items() if v is not None}
    if isinstance(o, list):
        return [_drop_nulls(x) for x in o]
    return o


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda p: p.name)
def test_models_round_trip_losslessly(path):
    """Model parse plus to_dict must reproduce the input exactly (apart from explicit nulls)."""
    raw = load(path)
    model = _MODEL_FOR[detect_kind(raw)].model_validate(raw)
    assert model.model_dump(mode="json", by_alias=True, exclude_none=True) == _drop_nulls(raw)


def test_ogs_aliases():
    m = ogs.GraphModel.model_validate(fixture("ogs/movies.yaml"))
    assert m.schema_.endswith("1.0.0/schema.json")
    assert m.relationships["acted-in"].from_.node == "person"
    assert m.nodes["person"].identifier_label == "Person"


def test_ogs_index_options_dotted_keys():
    m = ogs.GraphModel.model_validate(fixture("ogs/movies-import.yaml"))
    opts = m.nodes["person"].extensions.indexes["person-embedding"].options
    assert isinstance(opts, ogs.VectorOptions)
    assert opts.dimensions == 384
    assert ogs.to_dict(opts)["vector.similarity_function"] == "cosine"


def test_ogs_mapping_union_discriminates():
    m = ogs.GraphModel.model_validate(fixture("ogs/movies-import.yaml"))
    kinds = [type(x).__name__ for x in m.extensions.data_source_mappings["default"]]
    assert kinds == ["NodeMapping", "NodeMapping", "RelationshipMapping", "QueryMapping"]


@pytest.mark.parametrize(
    "t, is_list, is_vector, element",
    [
        ("STRING", False, False, "STRING"),
        ("LIST<LOCAL DATETIME>", True, False, "LOCAL DATETIME"),
        ("VECTOR<FLOAT32>", False, True, "FLOAT32"),
    ],
)
def test_neo4j_type_helpers(t, is_list, is_vector, element):
    nt = ogs.Neo4jType(t)
    assert (nt.is_list, nt.is_vector, nt.element.value) == (is_list, is_vector, element)


def test_ossie_relationship_from_alias():
    m = ossie.SemanticModel.model_validate(fixture("ossie/tpcds_semantic_model.yaml"))
    rel = m.relationships[0]
    assert rel.from_ and ossie.to_dict(rel)["from"] == rel.from_


def test_ossie_field_is_time_default():
    f = ossie.Field_(name="d", expression={"sql": "d"}, datatype="Date")
    assert f.is_time
    f2 = ossie.Field_(
        name="d", expression={"sql": "d"}, datatype="Date", dimension={"is_time": False}
    )
    assert not f2.is_time


def test_ossie_expression_requires_sql_or_dialects():
    with pytest.raises(ValueError):
        ossie.Expression()


def test_ossie_ontology_arity():
    m = ossie.Ontology.model_validate(fixture("ossie/flights.ontology.yaml"))
    arities = {r.arity for c in m.ontology for r in c.relationships or []}
    assert 2 in arities
