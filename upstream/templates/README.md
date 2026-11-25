# Neo4j Ontology Graph Spec ↔ Apache Ossie

Converts between the [Neo4j Ontology Graph Spec](https://neo4j.com/docs/ontology-graph-spec/)
(OGS 1.1.0, YAML or JSON) and Apache Ossie. A single OGS document describes a property-graph
schema (nodes, relationships, properties and constraints) and optionally the relational tables and
mappings that populate it. It corresponds to the full set of three Ossie documents:

| OGS | Ossie |
|---|---|
| nodes, relationships, properties, constraints | ontology |
| `extensions.data_source_tables` | semantic model |
| `extensions.data_source_mappings` | mapping document |

Both directions are supported. When converting there and back, anything without a native home
on the other side is kept: in `nonportable_extensions` with `vendor_name: NEO4J` (or `neo4j:*`
ontology annotations) going to Ossie, and in `custom.ossie` going to OGS. All upstream ontology,
semantic model and mapping examples round-trip losslessly. Every conversion produces a loss
report that marks each construct as stashed, approximated or dropped.

## Install and run

```sh
uv sync
uv run ossie-neo4j --help
```

```sh
# OGS ➜ Ossie: writes <id>.ontology.yaml, <id>.semantic_model.yaml, <id>.mapping.yaml
uv run ossie-neo4j convert model.ogs.yaml --out-dir out/

# Ossie ➜ OGS (any of the three documents; the direction is auto-detected)
uv run ossie-neo4j convert out/*.yaml -o model.ogs.yaml

# A semantic model on its own: derive nodes, relationships and Data Importer mappings
uv run ossie-neo4j to-neo4j ../ossie/examples/tpcds_semantic_model.yaml --derive-graph -o tpcds.ogs.yaml
```

Other commands: `to-ossie`, `to-neo4j`, `validate`, `normalize`, `migrate` (Ossie 0.1.1 ➜
0.3.0.dev0), and the interop contract `import -i <ogs> -o <ossie>` / `export -i <ossie> -o
<ogs>`. Flags: `--report FILE` (loss report as JSON), `--strict` (exit 2 if anything was
approximated or dropped), `--no-stash` (clean output, no vendor data), `--only KIND`, `-v`.

Python API:

```python
from ossie_neo4j.convert import to_neo4j, to_ossie

result = to_ossie("model.ogs.yaml")
result.documents  # {DocumentKind: dict} — ontology / semantic model / mapping
result.report  # LossReport

ogs = to_neo4j("x.ontology.yaml", semantic_model="x.semantic_model.yaml", mapping="x.mapping.yaml")
```

## Mapping summary

- **Ontology.** Node ⇄ EntityType concept. Property ⇄ a binary relationship to a value
  concept. The 12 Neo4j types without a built-in concept (e.g. ZONED DATETIME, POINT, VECTOR)
  use generated `Neo4j<Type>` value types. `LIST<X>` ⇄ a relationship with no multiplicity.
  Optional labels ⇄ unary relationships. Implied labels ⇄ `extends`. `KEY` ⇄ `identify_by`.
  End counts ⇄ `multiplicity`. Relationship properties ⇄ extra named roles. Any other n-ary
  relationship is reified as a node. Relationship types come from verbalizations
  (`{City} is located in {State}` → `IS_LOCATED_IN`).
- **Semantic model.** Table ⇄ dataset, column ⇄ field (`expression.sql` = the column), the
  suggested Neo4j type ⇄ `datatype`, primary key ⇄ `primary_key`, foreign key ⇄
  relationship. Metrics, derived fields, unique keys and `ai_context` have no OGS home and are
  kept.
- **Mapping.** `NODE` mapping ⇄ an object mapping plus a link mapping keyed by
  `identify_by`. `RELATIONSHIP` mapping ⇄ a link-mapping tree that follows the relationship's
  roles. OGS mappings can't express filters, nested referents or unary links. When a mapping
  document uses those, it is kept whole and restored while the OGS mappings are unchanged.
- **Core-only mode** (`--derive-graph`) synthesizes an ontology and a mapping from a semantic
  model on its own: keyed nodes per dataset and ManyToOne relationships from the many side.
- **Ossie 0.1.1** input is migrated on read, including keys and relationship columns that name
  source columns (PR #486).

## Vendor validator

The package registers `NEO4J` under the `ossie.vendor_validators` entry point:

```sh
uv run --with ./neo4j ossie/validation/validate.py --vendor NEO4J model.yaml
```

## Notes

- The OGS 1.1.0 schema and the Ossie 0.3.0.dev0 / 0.1.1 schemas are vendored under
  `src/ossie_neo4j/schemas/`, so validation works offline. A test checks the Ossie schemas
  against the `ossie` submodule.
- Interop: `import` gives a semantic model only (`data_source_tables` ➜ datasets). `export`
  derives a graph from the semantic model, and the export ➜ import round trip preserves the
  model exactly.
- How OGS relationship end counts map to Ossie multiplicity relies on one reading of OGS
  counts: a count bounds how many relationships *each node at that end* has. It lives in a
  single function, `convert/common.py:link_multiplicity`.
