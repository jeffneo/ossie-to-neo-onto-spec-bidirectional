"""Phase 2: OGS data_source_tables ⇄ Ossie semantic model."""

from __future__ import annotations

import copy
import json

import pytest
from conftest import FIXTURES, fixture, minimal_ogs

from ossie_neo4j.cli import main
from ossie_neo4j.convert import ConversionError, Loss, to_neo4j, to_ossie
from ossie_neo4j.io import DocumentKind, load
from ossie_neo4j.pipeline import read

ONTO = DocumentKind.OSSIE_ONTOLOGY
SM = DocumentKind.OSSIE_SEMANTIC_MODEL

PAIRS = [
    ("ossie/flights.ontology.yaml", "ossie/flights.semantic_model.yaml"),
    ("ossie/sap_business_partner.ontology.yaml", "ossie/sap_business_partner.semantic_model.yaml"),
    (None, "ossie/minimal.yaml"),
    (None, "ossie/tpcds_semantic_model.yaml"),
]


def _normalized(doc) -> dict:
    out = read(doc).normalized.model_dump(mode="json", by_alias=True, exclude_none=True)
    out.pop("schema", None)
    return out


def _to_neo4j(onto, sm, **kw):
    if onto is None:
        return to_neo4j(FIXTURES / sm, **kw)
    return to_neo4j(FIXTURES / onto, semantic_model=FIXTURES / sm, **kw)


def _back(conversion) -> dict:
    docs = conversion.documents
    if ONTO in docs:
        mapping = docs.get(DocumentKind.OSSIE_MAPPING)
        return to_neo4j(docs[ONTO], semantic_model=docs.get(SM), mapping=mapping).output
    return to_neo4j(docs[SM]).output


def _dataset(sm: dict, name: str) -> dict:
    return next(d for d in sm["datasets"] if d["name"] == name)


def _field(ds: dict, name: str) -> dict:
    return next(f for f in ds["fields"] if f["name"] == name)


def _neo4j_data(obj: dict) -> dict:
    return next(
        (e["data"] for e in obj.get("nonportable_extensions", []) if e["vendor_name"] == "NEO4J"),
        {},
    )


TABLES = {
    "people": {
        "source": "db.people",
        "columns": [
            {"name": "id", "type": "VARCHAR(36)", "suggested": "STRING", "nullable": False},
            {"name": "full name", "type": "VARCHAR"},
            {"name": "born", "suggested": "DATE"},
            {"name": "score", "suggested": "FLOAT32"},
        ],
        "primary_keys": ["id"],
    },
    "friends": {
        "source": "db.friends",
        "columns": [{"name": "a", "suggested": "STRING"}, {"name": "b", "suggested": "STRING"}],
        "foreign_keys": {
            "a-fk": {"columns": ["a"], "references": {"table": "people"}},
            "b-fk": {"columns": ["b"], "references": {"table": "people", "columns": ["id"]}},
            "bad": {"columns": ["a"], "references": {"table": "nope"}},
        },
    },
}


# --- round trips -----------------------------------------------------------------------------


@pytest.mark.parametrize("onto, sm", PAIRS, ids=lambda x: x or "-")
def test_ossie_documents_round_trip_losslessly(onto, sm):
    ogs = _to_neo4j(onto, sm).output
    docs = to_ossie(ogs).documents
    assert _normalized(docs[SM]) == _normalized(FIXTURES / sm)
    if onto:
        assert _normalized(docs[ONTO]) == _normalized(FIXTURES / onto)
    else:
        assert ONTO not in docs


def test_ogs_tables_round_trip_losslessly():
    doc = minimal_ogs(extensions={"data_source_tables": copy.deepcopy(TABLES)})
    result = to_ossie(doc)
    assert set(result.documents) == {SM}  # no nodes, so no ontology
    assert _normalized(_back(result)) == _normalized(doc)


@pytest.mark.parametrize("onto, sm", PAIRS, ids=lambda x: x or "-")
def test_no_stash_output_is_valid_and_clean(onto, sm):
    result = _to_neo4j(onto, sm, stash=False)
    assert '"ossie"' not in json.dumps(result.output)
    back = to_ossie(result.output, stash=False)
    assert "NEO4J" not in json.dumps(back.documents) and "neo4j:" not in json.dumps(back.documents)


# --- OGS ➜ Ossie -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tables_sm():
    result = to_ossie(minimal_ogs(name="Friends", extensions={"data_source_tables": TABLES}))
    return result.documents[SM], result.report


def test_tables_become_datasets(tables_sm):
    sm, _ = tables_sm
    assert sm["name"] == "Friends"
    people = _dataset(sm, "people")
    assert people["source"] == "db.people"
    assert people["primary_key"] == ["id"]
    ident = _field(people, "id")
    assert ident["expression"] == {"sql": "id"}
    assert ident["datatype"] == "String"
    assert _neo4j_data(ident) == {"type": "VARCHAR(36)", "nullable": False}


def test_column_names_and_types(tables_sm):
    people = _dataset(tables_sm[0], "people")
    # A name that isn't a plain identifier is quoted in SQL.
    spaced = _field(people, "full name")
    assert spaced["expression"] == {"sql": '"full name"'}
    # No suggested type: the datatype is inferred from the source type, and the missing
    # suggestion is recorded so it comes back missing.
    assert spaced["datatype"] == "String"
    assert _neo4j_data(spaced) == {"type": "VARCHAR", "suggested": None}
    # FLOAT32 maps to Float, but Float would come back as FLOAT, so the exact type is kept.
    score = _field(people, "score")
    assert score["datatype"] == "Float"
    assert _neo4j_data(score) == {"suggested": "FLOAT32"}


def test_foreign_keys_become_relationships(tables_sm):
    sm, report = tables_sm
    rels = {r["name"]: r for r in sm["relationships"]}
    assert rels["a-fk"] == {
        "name": "a-fk",
        "from": "friends",
        "to": "people",
        "from_columns": ["a"],
        "to_columns": ["id"],  # implicit: the referenced table's primary key
        "nonportable_extensions": [{"vendor_name": "NEO4J", "data": {"implicit_columns": True}}],
    }
    assert "nonportable_extensions" not in rels["b-fk"]
    # A foreign key to an unknown table can't be a relationship. It is kept on the dataset.
    assert "bad" not in rels
    assert "bad" in _neo4j_data(_dataset(sm, "friends"))["foreign_keys"]
    assert "foreign_key_not_representable" in report.codes()


def test_model_without_nodes_keeps_ogs_metadata_on_semantic_model():
    doc = minimal_ogs(
        id="x",
        name="Some Name",
        version="7",
        extensions={"data_source_tables": {"t": {"source": "s"}}, "custom": {"acme": 1}},
    )
    sm = to_ossie(doc).documents[SM]
    assert _neo4j_data(sm) == {"id": "x", "version": "7", "custom": {"acme": 1}}


def test_nothing_to_convert():
    with pytest.raises(ConversionError, match="nothing to convert"):
        to_ossie(minimal_ogs())


def test_tables_are_not_annotations_any_more():
    docs = to_ossie(FIXTURES / "ogs/movies-import.yaml").documents
    assert "neo4j:data_source_tables" not in docs[ONTO]["annotations"]
    assert [d["name"] for d in docs[SM]["datasets"]] == ["people", "movies", "cast"]


# --- Ossie ➜ OGS -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tpcds():
    return to_neo4j(FIXTURES / "ossie/tpcds_semantic_model.yaml")


def test_datasets_become_tables(tpcds):
    tables = tpcds.output["extensions"]["data_source_tables"]
    assert list(tables) == ["store_sales", "date_dim", "customer", "item", "store"]
    sales = tables["store_sales"]
    assert sales["source"] == "tpcds.public.store_sales"
    col = sales["columns"][0]
    assert col["name"] == "ss_sold_date_sk" and col["suggested"] == "INTEGER"
    assert col["custom"]["ossie"]["description"] == "Foreign key to date dimension"


def test_relationships_become_foreign_keys(tpcds):
    fks = tpcds.output["extensions"]["data_source_tables"]["store_sales"]["foreign_keys"]
    assert list(fks) == [
        "store_sales_to_date",
        "store_sales_to_customer",
        "store_sales_to_item",
        "store_sales_to_store",
    ]
    fk = fks["store_sales_to_customer"]
    assert fk["columns"] == ["ss_customer_sk"]
    assert fk["references"] == {"table": "customer", "columns": ["c_customer_sk"]}


def test_ossie_only_content_is_stashed_and_reported(tpcds):
    tables = tpcds.output["extensions"]["data_source_tables"]
    customer = tables["customer"]
    # customer_full_name is an expression, not a column: it is a derived field.
    names = [c["name"] for c in customer["columns"]]
    assert "customer_full_name" not in names
    derived = customer["custom"]["ossie"]["derived_fields"][0]["field"]
    assert derived["name"] == "customer_full_name"
    assert derived["expression"] == {"sql": "c_first_name || ' ' || c_last_name"}
    codes = tpcds.report.codes()
    for code in ("derived_field", "unique_keys", "dataset_metrics", "metrics", "decimal_as_float"):
        assert code in codes
    assert tpcds.output["extensions"]["custom"]["ossie"]["semantic_model"]["metrics"]


def test_field_name_differs_from_column_and_dialect_only_fields():
    sm = {
        "version": "0.3.0.dev0",
        "name": "m",
        "datasets": [
            {
                "name": "orders",
                "source": "s.orders",
                "primary_key": ["Order_ID"],
                "fields": [
                    {"name": "order_id", "expression": {"sql": "ORDER_NO"}, "datatype": "Integer"},
                    {
                        "name": "total",
                        "expression": {"dialects": [{"dialect": "SNOWFLAKE", "expression": "x"}]},
                    },
                    {"name": "opaque", "expression": {"sql": "blob"}, "datatype": "Opaque"},
                ],
            }
        ],
    }
    result = to_neo4j(sm)
    table = result.output["extensions"]["data_source_tables"]["orders"]
    assert [c["name"] for c in table["columns"]] == ["ORDER_NO", "blob"]
    assert table["columns"][0]["custom"]["ossie"]["name"] == "order_id"
    assert table["primary_keys"] == ["ORDER_NO"]
    assert table["columns"][1].get("suggested") is None
    assert _normalized(to_ossie(result.output).output) == _normalized(sm)


def test_semantic_model_name_is_kept_when_it_differs_from_ontology():
    ogs = _to_neo4j(*PAIRS[0]).output
    assert ogs["name"] == "Flights"  # from the ontology
    assert (
        ogs["extensions"]["custom"]["ossie"]["semantic_model"]["name"] == "Flights semantic model"
    )


# --- native wins -----------------------------------------------------------------------------


def test_edited_suggested_type_wins_over_stashed_datatype(tpcds):
    ogs = copy.deepcopy(tpcds.output)
    col = ogs["extensions"]["data_source_tables"]["store_sales"]["columns"][6]
    assert col["custom"]["ossie"]["datatype"] == "Decimal"
    col["suggested"] = "STRING"  # edited in OGS
    sm = to_ossie(ogs).documents[SM]
    assert _field(_dataset(sm, "store_sales"), "ss_sales_price")["datatype"] == "String"


def test_edited_datatype_wins_over_stashed_suggested(tables_sm):
    sm = copy.deepcopy(tables_sm[0])
    _field(_dataset(sm, "people"), "score")["datatype"] = "Integer"
    ogs = to_neo4j(sm).output
    score = ogs["extensions"]["data_source_tables"]["people"]["columns"][3]
    assert score["suggested"] == "INTEGER"


# --- CLI -------------------------------------------------------------------------------------


def test_cli_out_dir_and_semantic_model(tmp_path):
    out = tmp_path / "out"
    assert main(["to-ossie", str(FIXTURES / "ogs/movies-import.yaml"), "--out-dir", str(out)]) == 0
    onto = out / "movies-import.ontology.yaml"
    sm = out / "movies-import.semantic_model.yaml"
    mapping = out / "movies-import.mapping.yaml"
    assert onto.exists() and sm.exists() and mapping.exists()
    back = tmp_path / "back.yaml"
    args = ["to-neo4j", str(onto), "--semantic-model", str(sm), "--mapping", str(mapping)]
    assert main([*args, "-o", str(back)]) == 0
    assert _normalized(load(back)) == _normalized(fixture("ogs/movies-import.yaml"))


def test_cli_single_out_with_two_documents_fails(tmp_path, capsys):
    out = tmp_path / "x.yaml"
    assert main(["to-ossie", str(FIXTURES / "ogs/movies-import.yaml"), "-o", str(out)]) == 1
    assert "use --out-dir" in capsys.readouterr().err


def test_cli_stdout_has_one_yaml_document_per_output(capsys):
    assert main(["to-ossie", str(FIXTURES / "ogs/movies-import.yaml")]) == 0
    assert capsys.readouterr().out.count("\n---\n") == 2  # ontology, semantic model, mapping


def test_report_kinds_cover_semantic_layer(tpcds):
    kinds = {e.kind for e in tpcds.report.entries}
    assert kinds == {Loss.STASHED, Loss.APPROXIMATED}
