# ossie-bidirectional

A converter that translates in both directions between the **Neo4j Ontology Graph Spec** (OGS) and **Apache Ossie** (incubating; formerly Open Semantic Interchange, OSI).

> Status: **phase 0 done**. The scaffold, pinned schemas, models, fixtures, normalizers and validation rules are in place. Conversion starts in phase 1. See [Development](#development).

## 1. The two formats at a glance

| | Neo4j Ontology Graph Spec | Apache Ossie |
|---|---|---|
| What it describes | A property-graph schema: node kinds, relationship kinds, properties, constraints. It can also map relational tables onto the graph. | Semantic models (datasets, fields, metrics, relationships). Also an ontology layer (concepts, n-ary relationships) and mapping documents that link the two. |
| Serialization | One YAML/JSON document | Three YAML/JSON documents: **semantic model**, **ontology**, **mapping**. The root keys tell you which is which. |
| Normative schema | `https://neo4j.com/docs/ontology-graph-spec/1.1.0/schema.json` (JSON Schema 2020-12) | `core-spec/ossie-schema.json`, `ontology/ontology.json`, `ontology/mapping.json` in [apache/ossie](https://github.com/apache/ossie) |
| Version we target | **1.1.0** (reads 1.0.0) | **0.3.0.dev0**, pinned to a repo commit (reads 0.1.1 via migration) |
| Identity | Local map-key ids. No IRIs. One `reference` URI per element. | Local names, resolved case-insensitively. Optional `iri`, plus `prefixes`, on ontology elements. |
| Typing | Neo4j types (`STRING`, `LOCAL DATETIME`, `VECTOR<FLOAT32>`, …) | Core: `String`, `Integer`, `Decimal`, `Float`, `Boolean`, `Date`, `Time`, `DateTime`, `DateTimeTz`, `Opaque`. Ontology: built-in value concepts. |
| Inheritance | None. `labels.implied` is a flattened supertype list. | `extends` on concepts |
| Metrics | None | First-class (`metrics`, using the SQL expression dialect `OSSIE_SQL_2026` plus vendor dialects) |
| Extension point | `extensions.custom` (free-form, namespaced by tool) | `nonportable_extensions: [{vendor_name, data}]`. Ontology documents use `annotations`. |

The key observation is that **one OGS document maps onto a set of three Ossie documents**:

```
                 OGS document                         Ossie documents
 ┌─────────────────────────────────────┐      ┌──────────────────────────────┐
 │ nodes / relationships / properties  │ <==> │ ontology        (*.ontology) │
 │ extensions.data_source_tables       │ <==> │ semantic model  (datasets)   │
 │ extensions.data_source_mappings     │ <==> │ mapping         (concept →   │
 │                                     │      │                  dataset)    │
 └─────────────────────────────────────┘      └──────────────────────────────┘
```

The converter therefore treats **OGS ↔ Ossie ontology** as its main axis. Tables and mappings are converted when they are present.

## 2. Architecture

```
  parse ─► validate ─► normalize ─► map (via IR) ─► emit ─► validate ─► loss report
```

1. **Parse and validate the input** against its own JSON Schema, plus the library-level cross-reference rules (OGS "Consistent Schema" rules; Ossie name resolution).
2. **Normalize** so the mapping only sees one form of each construct:
   - OGS: expand shorthand into longhand (`label` → `labels.identifier`; `must_exist`/`unique`/`key` → `constraints`), and treat `null` as absent.
   - Ossie: turn string `ai_context` into an object, resolve `prefix:local` IRIs, and lowercase names for lookups only.
3. **Map through a small intermediate representation (IR):** `EntityType`, `Attribute`, `Link` (n-ary, with roles), `Constraint`, `Table`, `Column`, `ForeignKey`, `Mapping`, `Metric`, plus a `passthrough` bag on every element. Each direction is a pair of functions, `ogs → IR → ossie` and `ossie → IR → ogs`, so each side is written only once.
4. **Emit, re-validate the output, and write a loss report.** The report is machine-readable JSON plus warnings, and lists every construct that was approximated, stashed, or dropped.

**Round-trip preservation.** When something has no native home in the target format, we stash it instead of dropping it:
- Going to Ossie: core and mapping documents get `nonportable_extensions` with `vendor_name: NEO4J`; ontology elements get `annotations` keyed `neo4j:*`.
- Going to OGS: `extensions.custom.ossie` (and `custom.ossie` on individual elements).
- When reading, a stashed value is restored only if the native fields it relates to are unchanged. If someone edited the target format, those edits win. This is the same approach the Solid converter in `ossie-converters` uses.

**Implementation choice.** Python 3.11+ with Pydantic models. This matches the Ossie Python SDK and the hub-and-spoke [apache/ossie-converters](https://github.com/apache/ossie-converters) repo, so the converter can later be contributed upstream as a `neo4j/` converter. Output is validated with `jsonschema` against both specs' schemas. In CI we also run the official OGS validator (`@neo4j/ontology-graph-spec`, npm) as a cross-check.

## 3. Mapping rules

### 3.1 Schema layer: OGS ↔ Ossie ontology

| OGS | Ossie ontology | Notes |
|---|---|---|
| model `id`, `name`, `description` | root `name`, `description` | OGS `id` → `annotations.neo4j:id`. OGS `version` → `annotations.neo4j:version`. Ossie `version` is fixed to the spec version. |
| Node (`labels.identifier`) | `concept`, `type: EntityType` | Concept name = identifier label. The node id is stashed. |
| Node `description` | concept `description` | |
| Node `aliases` | `annotations.neo4j:aliases` | Ossie concepts have no synonyms field (`ai_context` exists only at the ontology root). |
| Node `reference` | concept `iri` | |
| `labels.implied` | `extends` | If an implied label is not itself a node, emit a stub `EntityType` concept. |
| `labels.optional` | `annotations.neo4j:optional_labels` | |
| Property | binary relationship from the concept to a value concept, `multiplicity: ManyToOne` | e.g. `{Person} has name {String}`. Relationship `name` = property id. |
| Property `type` | role concept: a built-in (`String`, `Integer`, `Float`, `Boolean`, `Date`, `DateTime`) | Types without a built-in (`DURATION`, `POINT`, `UUID`, `LOCAL TIME`, `ZONED *`, `LIST<…>`, `VECTOR<…>`) get a generated `ValueType` that `extends` the nearest built-in, with the exact Neo4j type in `annotations.neo4j:type` (and `neo4j:dimension` for vectors). |
| Property `pattern`, `one_of` | generated `ValueType` + `requires`, or annotation | Use `requires` only once the expression grammar is pinned down (see §6). Until then, use annotations. |
| `KEY` constraint (single or composite) | `identify_by` + `multiplicity: OneToOne` on single-property keys | |
| `UNIQUE` | `multiplicity: OneToOne` | |
| `EXISTS` | `annotations.neo4j:must_exist` (later a `requires` expression) | |
| `PROPERTY_TYPE` | implied by the role concept | |
| Relationship | ontology relationship declared on the `from` concept, with a role for the `to` concept | `name` = relationship id. `verbalizes: ['{From} TYPE {To}']`. The OGS `type` is stashed as `neo4j:type`. |
| Relationship end counts | `multiplicity` where it can express them (max 1 → `ManyToOne`, both sides → `OneToOne`) | Exact counts are always stashed in `neo4j:from_counts` / `neo4j:to_counts`. |
| Relationship properties | **extra roles** on the relationship (n-ary) | e.g. `{Person} acted in {Movie} as {String}`. This is a natural fit, not a lossy one. |
| Constraints and indexes on relationships, `extensions.indexes`, `display` | annotations | Carried for round-tripping only. |

**Reverse-direction specifics (Ossie → OGS):**
- `ValueType` concepts are resolved down to their built-in base to choose the Neo4j `type`. The full `extends` chain is stashed.
- Binary relationships whose second role is a **value** concept become **properties**. Those whose second role is an **entity** concept become **relationships**. Relationship `type` = stashed type, or the name in UPPER_SNAKE_CASE.
- N-ary relationships (three or more roles):
  - If exactly two roles are entities, the entity roles become `from`/`to` and the value roles become relationship properties.
  - Otherwise the relationship is **reified** as an intermediate node with one relationship per role. This is reported as approximated.
- `extends` → `labels.implied`, flattened transitively, because OGS does not support inheritance.
- `identify_by` → `KEY` constraint over the corresponding properties. `identify_by` may name a relationship *inherited* from a supertype: in the upstream SAP example, `Customer` is identified by `BusinessPartner.nr`. The KEY therefore goes on the subtype node, over a property copied down from the supertype.
- `derived_by`, `requires`, and unary relationships → stashed in `extensions.custom.ossie` and reported. OGS has no equivalent.

### 3.2 Physical layer: OGS tables ↔ Ossie semantic model

| OGS `extensions.data_source_tables` | Ossie semantic model |
|---|---|
| table id + `source` | `dataset.name` + `dataset.source` |
| `columns[]` | `fields[]`: `expression.sql` = column name; `datatype` from the column's `suggested` Neo4j type (falling back to source `type`) |
| `primary_keys` | `primary_key` |
| `foreign_keys` | `relationships[]`: `from` = this table, `to` = `references.table`, with `from_columns`/`to_columns` |
| column `nullable`, `size`, `supported` | `nonportable_extensions[NEO4J]` |
| — | Ossie `metrics` (model- and dataset-scoped) → `extensions.custom.ossie.metrics`. OGS has no metrics. |

### 3.3 Mapping layer: OGS mappings ↔ Ossie mapping document

| OGS `data_source_mappings` | Ossie `concept_mappings` |
|---|---|
| `NODE` mapping (node, table, key, properties) | `object_mappings` with `referent_mappings` over the `identify_by` relationships (`expression: TABLE.column`). Non-key properties become `link_mappings`. |
| `RELATIONSHIP` mapping (from_node/to_node keys) | `link_mappings` on the relationship |
| `QUERY` mapping | `nonportable_extensions[NEO4J]` (no Ossie equivalent) |
| `mode`, `match_label`, `depends_on` | `nonportable_extensions[NEO4J]` |

Ossie notes that tool support for standalone mapping documents is "not yet available". We emit the standalone form and also read the deprecated embedded `ontology_mappings` form.

### 3.4 "Core-only" mode

Many Ossie producers (dbt, Snowflake, etc.) emit **only a semantic model**, with no ontology. For those inputs, the converter derives a graph:
- each dataset becomes a node (label = PascalCase of the dataset name);
- each field becomes a property;
- `primary_key` becomes a `KEY` constraint;
- each relationship becomes a relationship with `to.max_count: 1`, typed with the relationship name in UPPER_SNAKE_CASE;
- `data_source_tables` and `data_source_mappings` are filled in automatically, so the result can be loaded with Aura Data Importer.

In the reverse direction, `--target semantic-model` emits only the core document, for consumers that don't read the ontology layer.

## Development

```bash
uv sync
```

```bash
uv run pytest
```

```bash
uv run ossie-neo4j validate tests/fixtures/ogs/movies.yaml tests/fixtures/ossie/flights.ontology.yaml
```

```bash
uv run ossie-neo4j normalize tests/fixtures/ogs/movies.yaml
```

Package layout (`src/ossie_neo4j/`):

| Module | What it does |
|---|---|
| `io.py` | Loads and dumps YAML/JSON. Works out which of the four document kinds a document is from its root keys. |
| `schemas/` | Vendored, pinned JSON Schemas (OGS 1.1.0; Ossie 0.3.0.dev0 at commit `6792db4`) and offline validation. Ossie's cross-file `$ref`s resolve to the local copies. |
| `ogs/models.py`, `ossie/models.py` | Pydantic models that mirror the schemas one to one (`extra="forbid"`). `ossie/models.py` is the only module that knows Ossie's shape. |
| `ogs/normalize.py` | Expands shorthand into longhand. Its output matches the spec's longhand example exactly. |
| `ossie/normalize.py` | Turns a string `ai_context` into an object. Expands `prefix:local` IRIs, and provides `compact_iri` for the reverse. |
| `ogs/rules.py`, `ossie/rules.py` | The cross-reference rules that JSON Schema can't express: the OGS "Core" rules, plus Ossie name-resolution and ontology rules. |
| `pipeline.py` | `read()`: parse ➜ schema ➜ model ➜ rules ➜ normalize. A stage runs only if the earlier stages found no errors. |
| `cli.py` | `ossie-neo4j validate` and `ossie-neo4j normalize`. |

## 4. CLI (planned for phase 1)

```bash
ossie-neo4j to-ossie model.ogs.yaml --out ./out/
```
→ `out/<id>.ontology.yaml`, plus `<id>.semantic_model.yaml` and `<id>.mapping.yaml` when the input has tables and mappings.

```bash
ossie-neo4j to-neo4j --ontology x.ontology.yaml --semantic-model x.semantic_model.yaml --mapping x.mapping.yaml --out model.ogs.yaml
```

```bash
ossie-neo4j to-neo4j --semantic-model x.semantic_model.yaml --out model.ogs.yaml
```
This runs in core-only mode.

Common flags: `--report loss.json`, `--strict` (fail on any lossy construct), `--no-stash` (clean output with no vendor extensions).

## 5. Testing strategy

- **Fixtures:**
  - OGS: the spec's `movies` example (both shorthand and longhand forms).
  - Ossie: `minimal.yaml`, the `flights.{ontology,semantic_model,mapping}.yaml` trio, `foaf_owl_import.yaml`, and `tpcds_semantic_model.yaml`.
- **Round-trip tests:** both `OGS → Ossie → OGS` and `Ossie → OGS → Ossie` must give a result equal to the input after normalization (with stashing on). Each lossy construct gets a golden loss report.
- **Schema validity:** every output must validate against the target spec's schema and pass the OGS library's "All" validator set.
- **Property-based tests** (Hypothesis): generate random valid OGS models and check that round-trips never raise and never silently drop data.
- **Upstream:** later, add the converter to the `ossie-converters/interop` test matrix.

## 6. Open questions / risks

1. **OGS cardinality direction.** It is unclear whether `to.min_count` bounds relationships per `to` node or per `from` node; the docs and examples are ambiguous. We need to confirm with the OGS authors before mapping counts to `multiplicity`.
2. **Ossie `requires` / `derived_by` grammar.** It is not formally pinned, so we stash these instead of translating them, until a grammar exists in the spec.
3. **Ossie version churn.** 0.3.0.dev0 changes weekly (recent renames: `custom_extensions` → `nonportable_extensions`, `uri` → `iri`, and the `semantic_model` wrapper was removed). We pin a commit and isolate Ossie models behind one module.
4. **`NEO4J` vendor token.** Propose registering it in Ossie's `extensions/vendors.md`.
5. **Case sensitivity.** OGS ids and labels are case-sensitive; Ossie names resolve case-insensitively. The converter must detect collisions such as `Person` vs `person` and rename them deterministically.
6. **Same relationship type across node pairs.** In OGS this needs one entry per pair; Ossie relationships are scoped to a concept. We use `Concept.relname` as the stable key.

## 7. Phased plan

| Phase | Deliverable |
|---|---|
| 0 | Repo scaffold, pinned schemas, Pydantic models for both specs, fixtures, normalizers |
| 1 | OGS nodes/relationships/properties ↔ Ossie ontology, with stashing and the loss report |
| 2 | OGS tables ↔ Ossie semantic model (datasets, fields, keys, FK relationships, metrics stash) |
| 3 | OGS mappings ↔ Ossie mapping document |
| 4 | Core-only mode, Ossie 0.1.1 → 0.3.0 migration on input, CLI polish |
| 5 | Upstream: contribute to `apache/ossie-converters`, and propose the `NEO4J` vendor token |

## References

- Neo4j Ontology Graph Spec: https://neo4j.com/docs/ontology-graph-spec/ (format reference, shorthand, rules, interoperability, libraries/migration)
- OGS schema: https://neo4j.com/docs/ontology-graph-spec/1.1.0/schema.json
- Apache Ossie: https://ossie.apache.org/ · https://github.com/apache/ossie (`core-spec/`, `ontology/`, `examples/`)
- Ossie converters: https://github.com/apache/ossie-converters (see `ontology/`, `linkml/`, and `solid/` for prior art)
- Ossie incubator proposal: https://cwiki.apache.org/confluence/spaces/INCUBATOR/pages/430408796/OssieProposal
