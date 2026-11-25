"""Phase 4: Ossie 0.1.1 input, core-only mode, and the polished CLI."""

from __future__ import annotations

import copy
import json

import pytest
from conftest import FIXTURES, fixture

from ossie_neo4j.cli import main
from ossie_neo4j.convert import ConversionError, to_neo4j, to_ossie
from ossie_neo4j.io import DocumentKind, load
from ossie_neo4j.ossie.migrate import is_legacy, migrate
from ossie_neo4j.pipeline import read

LEGACY = FIXTURES / "ossie-0.1.1/tpcds_semantic_model.yaml"
SM = DocumentKind.OSSIE_SEMANTIC_MODEL


def _normalized(doc) -> dict:
    out = read(doc).normalized.model_dump(mode="json", by_alias=True, exclude_none=True)
    out.pop("schema", None)
    return out


def _legacy(**model) -> dict:
    base = {
        "name": "m",
        "datasets": [
            {
                "name": "t",
                "source": "s.t",
                "primary_key": ["id"],
                "fields": [
                    {
                        "name": "x",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "x"}]},
                    }
                ],
            }
        ],
    }
    base.update(model)
    return {"version": "0.1.1", "semantic_model": [base]}


# --- migration -------------------------------------------------------------------------------


def test_upstream_0_1_1_example_migrates():
    raw = load(LEGACY)
    assert is_legacy(raw)
    docs, issues = migrate(raw)
    assert len(docs) == 1
    doc = docs[0]
    assert doc["version"] == "0.3.0.dev0"
    sales = doc["datasets"][0]
    assert sales["fields"][0]["expression"] == {"sql": "ss_sold_date_sk"}
    # 0.1.1 keys named columns; ss_ticket_number had no field, so one is added.
    assert {"name": "ss_ticket_number", "expression": {"sql": "ss_ticket_number"}} in sales[
        "fields"
    ]
    ext = doc["nonportable_extensions"][0]
    assert ext["vendor_name"] == "SALESFORCE" and isinstance(ext["data"], dict)
    codes = {i.code for i in issues}
    assert {"migration_promoted_expression", "migration_added_key_field"} <= codes


def test_read_migrates_transparently():
    loaded = read(LEGACY)
    assert loaded.ok and loaded.kind is SM
    assert loaded.issues[0].code == "migrated_from_0_1_1"
    assert to_neo4j(LEGACY).output["extensions"]["data_source_tables"]["store_sales"]


def test_dialect_specific_expressions_stay_dialects():
    doc = _legacy()
    doc["semantic_model"][0]["datasets"][0]["fields"][0]["expression"]["dialects"].append(
        {"dialect": "SNOWFLAKE", "expression": "X::int"}
    )
    field = migrate(doc)[0][0]["datasets"][0]["fields"][0]
    assert len(field["expression"]["dialects"]) == 2


@pytest.mark.parametrize(
    "data, expected",
    [('{"a": 1}', {"a": 1}), ("[1, 2]", {"value": [1, 2]}), ("not json", {"raw": "not json"})],
)
def test_custom_extension_data_becomes_object(data, expected):
    doc = _legacy(custom_extensions=[{"vendor_name": "DBT", "data": data}])
    migrated = migrate(doc)[0][0]
    assert migrated["nonportable_extensions"] == [{"vendor_name": "DBT", "data": expected}]
    assert "custom_extensions" not in migrated


def test_relationship_columns_map_to_fields():
    doc = _legacy()
    m = doc["semantic_model"][0]
    t_ref = {"dialects": [{"dialect": "ANSI_SQL", "expression": "T_ID"}]}
    m["datasets"].append(
        {"name": "u", "source": "s.u", "fields": [{"name": "t_ref", "expression": t_ref}]}
    )
    m["relationships"] = [
        {"name": "r", "from": "u", "to": "t", "from_columns": ["T_ID"], "to_columns": ["id"]}
    ]
    rel = migrate(doc)[0][0]["relationships"][0]
    assert rel["from_columns"] == ["t_ref"]  # the field over column T_ID
    assert read(migrate(doc)[0][0]).ok


def test_multiple_models():
    doc = _legacy()
    doc["semantic_model"].append(copy.deepcopy(doc["semantic_model"][0]) | {"name": "n"})
    assert len(migrate(doc)[0]) == 2
    loaded = read(doc)
    assert [i.code for i in loaded.issues] == ["multiple_semantic_models"]


def test_invalid_legacy_document():
    doc = _legacy()
    doc["semantic_model"][0]["datasets"][0]["bogus"] = 1
    assert read(doc).issues[0].code == "schema_violation"


# --- core-only mode --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def derived():
    return to_neo4j(FIXTURES / "ossie/tpcds_semantic_model.yaml", derive_graph=True)


def test_datasets_become_keyed_nodes(derived):
    nodes = derived.output["nodes"]
    assert list(nodes) == ["store-sales", "date-dim", "customer", "item", "store"]
    sales = nodes["store-sales"]
    assert sales["labels"] == {"identifier": "StoreSales"}
    assert list(sales["constraints"].values())[0] == {
        "type": "KEY",
        "properties": ["ss_item_sk", "ss_ticket_number"],
        "name": "ss_item_sk_ss_ticket_number_StoreSales_key",
    }
    # Foreign-key-only fields are replaced by relationships, not kept as properties.
    assert "ss_customer_sk" not in sales["properties"]
    assert sales["properties"]["ss_sales_price"]["type"] == "FLOAT"


def test_relationships_point_from_many_side(derived):
    rel = derived.output["relationships"]["store-sales-to-customer"]
    assert rel["type"] == "STORE_SALES_TO_CUSTOMER"
    assert rel["from"] == {"node": "store-sales", "max_count": 1}
    assert rel["to"] == {"node": "customer"}


def test_mappings_make_it_importable(derived):
    maps = derived.output["extensions"]["data_source_mappings"]["default"]
    node = next(m for m in maps if m.get("node") == "customer")
    assert node["table"] == "customer" and node["key"] == ["c_customer_sk"]
    rel = next(m for m in maps if m.get("relationship") == "store-sales-to-customer")
    assert rel["to_node"] == {
        "label": "Customer",
        "properties": {"c_customer_sk": {"column": "ss_customer_sk"}},
    }
    assert "mapping" not in derived.output["extensions"].get("custom", {}).get("ossie", {})


def test_derived_losses_are_reported(derived):
    codes = derived.report.codes()
    assert "derived_field_not_property" in codes  # customer_full_name
    assert "decimal_as_float" in codes


def test_datasets_without_keys_get_nodes_but_no_mappings():
    result = to_neo4j(FIXTURES / "ossie/flights.semantic_model.yaml", derive_graph=True)
    assert len(result.output["nodes"]) == 6
    assert "data_source_mappings" not in result.output["extensions"]
    assert result.report.codes().count("derived_no_key") == 6


def test_derived_graph_round_trips_through_ossie(derived):
    docs = to_ossie(derived.output).documents
    assert set(docs) == {
        DocumentKind.OSSIE_ONTOLOGY,
        SM,
        DocumentKind.OSSIE_MAPPING,
    }
    back = to_neo4j(
        docs[DocumentKind.OSSIE_ONTOLOGY],
        semantic_model=docs[SM],
        mapping=docs[DocumentKind.OSSIE_MAPPING],
    ).output
    assert _normalized(back) == _normalized(derived.output)
    assert _normalized(docs[SM]) == _normalized(FIXTURES / "ossie/tpcds_semantic_model.yaml")


def test_derive_graph_needs_semantic_model_alone():
    with pytest.raises(ConversionError, match="on its own"):
        to_neo4j(FIXTURES / "ossie/flights.ontology.yaml", derive_graph=True)


def test_derive_graph_from_legacy_input():
    result = to_neo4j(LEGACY, derive_graph=True)
    assert "derived_untyped_fields" in result.report.codes()  # 0.1.1 has no datatypes
    assert result.output["nodes"]["customer"]["properties"]["c_first_name"]["type"] == "STRING"


# --- CLI -------------------------------------------------------------------------------------


def test_cli_version(capsys):
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0
    assert "ossie-neo4j" in capsys.readouterr().out


def test_cli_convert_detects_direction(tmp_path):
    out = tmp_path / "out"
    assert main(["convert", str(FIXTURES / "ogs/movies-import.yaml"), "--out-dir", str(out)]) == 0
    files = sorted(str(p) for p in out.iterdir())
    assert len(files) == 3
    back = tmp_path / "back.json"
    assert main(["convert", *files, "-o", str(back)]) == 0
    assert _normalized(json.loads(back.read_text())) == _normalized(
        fixture("ogs/movies-import.yaml")
    )


def test_cli_convert_rejects_duplicate_kinds(capsys):
    sm = str(FIXTURES / "ossie/minimal.yaml")
    assert main(["convert", sm, str(FIXTURES / "ossie/tpcds_semantic_model.yaml")]) == 1
    assert "two ossie-semantic-model inputs" in capsys.readouterr().err
    assert main(["convert", sm, str(FIXTURES / "ogs/movies.yaml")]) == 1


def test_cli_only(capsys):
    args = ["to-ossie", str(FIXTURES / "ogs/movies-import.yaml"), "--only", "mapping"]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "concept_mappings" in out and "---" not in out


def test_cli_derive_graph(tmp_path, capsys):
    out = tmp_path / "g.yaml"
    assert main(["to-neo4j", str(LEGACY), "--derive-graph", "-o", str(out)]) == 0
    assert len(load(out)["nodes"]) == 5
    err = capsys.readouterr().err
    assert "info message(s); -v to show" in err  # migration notes are summarized


def test_cli_migrate(tmp_path, capsys):
    out = tmp_path / "m.yaml"
    assert main(["migrate", str(LEGACY), "-o", str(out)]) == 0
    assert load(out)["version"] == "0.3.0.dev0"
    assert main(["migrate", str(FIXTURES / "ossie/minimal.yaml")]) == 1
    assert "not an Ossie 0.1.x document" in capsys.readouterr().err


def test_cli_migrate_multiple_models(tmp_path):
    doc = _legacy()
    doc["semantic_model"].append(copy.deepcopy(doc["semantic_model"][0]) | {"name": "n"})
    src = tmp_path / "multi.json"
    src.write_text(json.dumps(doc))
    assert main(["migrate", str(src), "-o", str(tmp_path / "x.yaml")]) == 1
    out = tmp_path / "out"
    assert main(["migrate", str(src), "--out-dir", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == [
        "m.semantic_model.yaml",
        "n.semantic_model.yaml",
    ]
