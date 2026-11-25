# ossie-bidirectional

A converter that translates in both directions between the **Neo4j Ontology Graph Spec** (OGS) and **Apache Ossie** (incubating; formerly Open Semantic Interchange, OSI).

> Status: **phase 3 done**. One OGS document converts to and from the full set of three Ossie documents: *ontology*, *semantic model* and *mapping*. Every fixture, including the upstream Flights and SAP ontology / semantic model / mapping sets, round-trips losslessly, and each conversion produces a loss report. Next is phase 4: core-only mode, Ossie 0.1.1 input, and CLI polish. See [Development](#development).

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
  parse ─► validate ─► normalize ─► map ─► emit ─► validate ─► loss report
```

1. **Parse and validate the input** against its own JSON Schema, plus the library-level cross-reference rules (OGS "Consistent Schema" rules; Ossie name resolution).
2. **Normalize** so the mapping only sees one form of each construct:
   - OGS: expand shorthand into longhand (`label` → `labels.identifier`; `must_exist`/`unique`/`key` → `constraints`), and treat `null` as absent.
   - Ossie: turn string `ai_context` into an object, resolve `prefix:local` IRIs, and lowercase names for lookups only.
3. **Map, using normalized longhand OGS as the hub.** We first planned a separate intermediate representation. In phase 1 it turned out that normalized OGS already *is* one: a closed, typed graph schema with an extension point (`custom`) on every element. So each direction is one module: `convert/to_ossie.py` (OGS ➜ Ossie) and `convert/to_ogs.py` (Ossie ➜ OGS). Phases 2–3 slot into the OGS `extensions` that already exist (`data_source_tables`, `data_source_mappings`).
4. **Emit, re-validate the output, and write a loss report.** The report is machine-readable JSON plus warnings, and lists every construct that was approximated, stashed, or dropped.

**Round-trip preservation.** When something has no native home in the target format, we stash it instead of dropping it:
- Going to Ossie: core and mapping documents get `nonportable_extensions` with `vendor_name: NEO4J`; ontology elements get `annotations` keyed `neo4j:*`.
- Going to OGS: `extensions.custom.ossie` (and `custom.ossie` on individual elements).
- When reading, a stashed value is restored only if the native fields it relates to are unchanged. If someone edited the target format, those edits win. This is the same approach the Solid converter in `ossie-converters` uses. For example, a stashed type is ignored if the role concept changed, and stashed counts are ignored if the multiplicity changed.
- **Defaults are never stashed.** Both directions share the generators for default names, verbalizations, relationship types and constraint ids (`convert/common.py`). A value is stashed only if it differs from what the reverse direction would regenerate. As a result, converting there and back adds no `neo4j:*` or `custom.ossie` noise.

**Implementation choice.** Python 3.11+ with Pydantic models. This matches the Ossie Python SDK and the hub-and-spoke [apache/ossie-converters](https://github.com/apache/ossie-converters) repo, so the converter can later be contributed upstream as a `neo4j/` converter. Output is validated with `jsonschema` against both specs' schemas. In CI we also run the official OGS validator (`@neo4j/ontology-graph-spec`, npm) as a cross-check.

## 3. Mapping rules

### 3.1 Schema layer: OGS ↔ Ossie ontology

These are the rules as implemented in phase 1. "Stashed" means kept in `neo4j:*` annotations going to Ossie, or in `custom.ossie` going to OGS, and restored on the way back.

| OGS | Ossie ontology | Notes |
|---|---|---|
| model `name`, `description` | root `name`, `description` | `id` and `version` are stashed when they differ from the defaults (kebab-case name; `1.0.0`). OGS output always declares schema 1.1.0. |
| Node with `labels.identifier` | `EntityType` concept named after the label | Node id = kebab-case of the concept name, unless one is stashed. |
| Node `description`, `reference` | concept `description`, `iri` | IRIs are compacted with the document's `prefixes` when writing Ossie. |
| Node `aliases` | stashed | Ossie concepts have no synonyms field. |
| `labels.implied` | `extends` | An implied label with no node becomes a stub concept (`neo4j:stub`). Going back, `extends` is flattened transitively; the exact `extends` list is stashed only if it differs. |
| `labels.optional` | **unary relationship** (`{Flight} was canceled`) | A native mapping in both directions. Optional labels are OGS's state labels, and unary relationships are Ossie's flags. |
| Property | binary relationship to a value concept | `{Person} has name {String}`. Relationship name = property id. |
| Property `type` | role concept | STRING, INTEGER, FLOAT, BOOLEAN, DATE and LOCAL DATETIME map to built-ins. Every other type uses a **generated** value type `Neo4j<Type>` that extends the nearest built-in, so the exact type is native in Ossie: for example `Neo4jZonedDatetime` extends `DateTime`, and `Neo4jVectorFloat32` and `Neo4jPoint` extend `String`. |
| `LIST<X>` | relationship to `X` with **no multiplicity** | This is how Ossie expresses a multi-valued attribute. |
| single-valued property | `multiplicity: ManyToOne` | |
| single-property `UNIQUE` | `multiplicity: OneToOne` | |
| `KEY` (single or composite) | `identify_by` | A single-property key also makes the attribute OneToOne. Only the first KEY maps; others are stashed. |
| `EXISTS`, composite `UNIQUE`, `PROPERTY_TYPE`, custom constraint names | stashed (`neo4j:constraints`) | Only constraints that wouldn't be regenerated identically are stashed. |
| `name`, `pattern`, `one_of`, `dimension`, property `aliases` | stashed | Ossie's `requires` grammar isn't pinned down yet (§6). |
| Relationship | relationship declared on the `from` concept, with the `to` concept as its role | Name = snake_case of the type, or `<type>_<target>` if that name is already taken. Verbalization: `{Person} acted in {Movie}`. A self-relationship names its role `target`. |
| Relationship end counts | `multiplicity` | `from` at most 1 → `ManyToOne`; both ends at most 1 → `OneToOne`. Counts that this can't express exactly are stashed and reported. This depends on how OGS counts are read (§6). |
| Relationship properties | **extra named roles** (n-ary) | `{Person} acted in {Movie} with role {String:role}`. The property's metadata goes in the role's `neo4j:property` annotation. A LIST-valued property becomes a single-valued role, which is reported as approximated. |
| End given by label only, or no end | stub concept, or `Any` | |
| Indexes, `display`, `custom` | stashed | |
| `extensions.data_source_*` | stashed for now | Mapped natively in phases 2 and 3. |

**Reverse-direction specifics (Ossie ➜ OGS).** Each relationship on an EntityType concept is classified by its roles:

| Roles | OGS |
|---|---|
| none (unary) | an optional label: `canceled` → `Canceled` |
| one value concept | a property |
| one entity concept (or `Any`) | a relationship. Its **type comes from the verbalization**: `{City} is located in {State}` → `IS_LOCATED_IN`, falling back to the name in UPPER_SNAKE_CASE |
| one entity concept plus value concepts | a relationship with properties |
| anything else | **reified** as an intermediate node, with one relationship per entity role and the value roles as properties. Reported as approximated; the original is stashed and restored on the way back. Example: `{Flight} at {DateTime} registers {DegreesLongitude}` |

- `ValueType` concepts are flattened: each is resolved through `extends` to a Neo4j type, and the definitions are stashed. `Decimal` becomes `FLOAT`, reported as approximated.
- `identify_by` → a `KEY` constraint, if every member is an attribute. Members may be **inherited** from a supertype: in the upstream SAP example, `Customer` is identified by `BusinessPartner.nr`. The property is then copied down onto the subtype node, marked `inherited_from`. If `identify_by` includes a link (Flights: `City` is identified by `[name, state]`), no KEY is written, because a Neo4j KEY can't cover a relationship. That case is reported.
- `derived_by`, `requires`, `ai_context`, `prefixes`, non-`neo4j:` annotations, and the deprecated embedded `ontology_mappings` → stashed and reported. Concept order and relationship order are stashed only if they differ from what OGS ➜ Ossie would produce.

### 3.2 Physical layer: OGS tables ↔ Ossie semantic model

As implemented in phase 2 (`convert/semantic.py`):

| OGS `extensions.data_source_tables` | Ossie semantic model | Notes |
|---|---|---|
| table id, `source` | dataset `name`, `source` | Names that clash case-insensitively are renamed and reported. |
| column | field: `name` = column name, `expression.sql` = the column as an SQL identifier | Names that aren't plain identifiers are double-quoted (`"full name"`). |
| column `suggested` (Neo4j type) | field `datatype` | INTEGER* → Integer, FLOAT* → Float, ZONED DATETIME → DateTimeTz, LOCAL TIME → Time; lists, vectors, points and the like → Opaque. Without `suggested`, the datatype is inferred from the source `type` (`VARCHAR` → String). A `suggested` type that the datatype wouldn't reproduce, such as FLOAT32, is stashed. |
| column source `type`, `size`, `nullable`, `supported`, vector `dimension`, `custom` | `nonportable_extensions[NEO4J]` on the field | Ossie has no nullability or source types. |
| `primary_keys` (column names) | `primary_key` (field names) | |
| foreign key | relationship: `from` = the table holding the key (the many side), `to` = the referenced table | Name = foreign-key id, made unique across the model. If the key doesn't list referenced columns, `to_columns` is the referenced table's primary key, flagged `implicit_columns`. A foreign key to an unknown table or undeclared columns is kept on the dataset and reported. |
| model `name`, `description` | semantic model `name`, `description` | With an ontology, the OGS name comes from the ontology, so the semantic model's name is stashed only if it differs (Flights: `Flights` vs `Flights semantic model`). Without an ontology, OGS `id`, `version` and `custom` ride on the semantic model's `NEO4J` extension. |

Ossie ➜ OGS specifics:
- **Derived fields.** A field whose expression is not a bare column reference, or that only has `dialects`, has no OGS column. An example is tpcds `customer_full_name = c_first_name || ' ' || c_last_name`. It is stashed (`custom.ossie.derived_fields`) and restored at its original position. So is a second field on a column that's already used. A primary key or relationship that uses a derived field is stashed and reported.
- A field whose name differs from its column (`order_id` over `ORDER_NO`) keeps the column name in OGS and stashes the field name.
- `Decimal` → suggested `FLOAT`, reported as approximated, with `Decimal` stashed. `Opaque` → no suggested type.
- Field `description`, `label`, `dimension` and `ai_context`, dataset `description`, `ai_context` and `unique_keys`, and **metrics** (model- and dataset-scoped) → stashed in `custom.ossie` and reported. OGS has no metrics.
- Case-insensitive name references (`from: Orders` for dataset `orders`) are resolved, and the original spelling is stashed.

### 3.3 Mapping layer: OGS mappings ↔ Ossie mapping document

As implemented in phase 3 (`convert/mappings.py`). A mapping document is produced when the OGS model has nodes, tables *and* mappings. It references the other two documents by name, with `iri: ./<id>.ontology.yaml` and `./<id>.semantic_model.yaml`. Column references are written `DATASET.field`.

| OGS `data_source_mappings` | Ossie `concept_mappings` |
|---|---|
| `NODE` mapping (node, table, key, properties) | On the node's concept: an object mapping that identifies objects by the key properties (`referent_mappings` → `TABLE.column`). Plus a link mapping with that identity, whose children populate the other properties (`{concept: String, expression: people.full_name}` → `name`). With no explicit `key`, the concept's `identify_by` is used. |
| `RELATIONSHIP` mapping (from_node, to_node, properties) | A link-mapping tree on the source concept. The levels follow the relationship's roles: the source identity, then the target identity, then one level per relationship property. The last level names the relationship. This matches the tree semantics of the Ossie spec, where a level's depth is the arity of the relationship it names. |
| `QUERY` mapping | No Ossie equivalent. Kept in `nonportable_extensions[NEO4J]`. |
| set id, mapping order, `id`, `mode`, `match_label`, `depends_on`, explicit `key`, non-default end labels | `nonportable_extensions[NEO4J]` on the mapping document. Left out entirely when the concept mappings alone reproduce the OGS mappings. |

**Ossie ➜ OGS** parses the trees back. Object mappings and link mappings that share an identity merge into one `NODE` mapping. A child that names a link becomes a `RELATIONSHIP` mapping. Constructs OGS mappings can't express are reported as approximated and left out of the OGS mappings:
- filtered or computed expressions (`FLIGHT.id WHERE ( FLIGHT.cancelled == TRUE )`);
- **nested referents**, i.e. entities identified through other entities (Flights: `Runway` by `designator` + `airport.code`);
- expressions that span datasets;
- unary relationships, which are optional labels in OGS (OGS mappings can't set labels);
- mappings to reified relationships.

**Keeping the original document.** After parsing, the converter regenerates a mapping document from the resulting OGS mappings. If that doesn't reproduce the input, the input document is kept whole in `custom.ossie.mapping`, together with a snapshot of the OGS mappings it produced. Going back to Ossie, the kept document is restored only while the OGS mappings still equal the snapshot. Once they've been edited, the mapping document is regenerated from OGS (native wins). The upstream Flights and SAP mappings are kept this way. A mapping that OGS can express fully is never kept whole.

Without nodes or without tables there is no mapping document. The OGS mappings then ride along in the ontology's annotations or on the semantic model's `NEO4J` extension, as in phase 2.

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
| `convert/to_ossie.py`, `convert/to_ogs.py` | The two directions of the ontology conversion (§3.1). They also assemble the semantic-model half. |
| `convert/semantic.py` | Tables ⇄ semantic model, in both directions (§3.2). Also builds the `TableIndex` (table ⇄ dataset, column ⇄ field). |
| `convert/mappings.py` | OGS mappings ⇄ Ossie concept mappings, in both directions (§3.3). Uses a `MapContext` built from the OGS document, the ontology, the link correspondences and the `TableIndex`. |
| `convert/common.py` | Conventions both directions share: annotation keys, default ids, names, verbalizations and relationship types, constraint derivation, and count ⇄ multiplicity. |
| `convert/types.py` | Neo4j type ⇄ value concept tables, generated `Neo4j*` value types, and the value-type resolver. Also Neo4j type ⇄ Ossie field `datatype`, and source-type inference. |
| `convert/report.py` | The loss report, with entries of kind `stashed`, `approximated` or `dropped`. |
| `convert/__init__.py` | Public API: `to_ossie(ogs)` gives up to three documents (ontology, semantic model, mapping); `to_neo4j(ontology_or_sm, semantic_model=..., mapping=...)` gives one OGS document. Each validates the inputs, converts them, re-validates the outputs, and returns `Conversion.documents` with a loss report. `to_neo4j` warns if the mapping's `*_ref` names don't match the documents given. |
| `cli.py` | `ossie-neo4j validate`, `normalize`, `to-ossie` and `to-neo4j`. |

## 4. CLI

```bash
uv run ossie-neo4j to-ossie tests/fixtures/ogs/movies-import.yaml --out-dir out/ --report loss.json
```
This writes `out/movies-import.ontology.yaml`, `.semantic_model.yaml` and `.mapping.yaml`.

```bash
uv run ossie-neo4j to-neo4j out/movies-import.ontology.yaml --semantic-model out/movies-import.semantic_model.yaml --mapping out/movies-import.mapping.yaml -o back.ogs.yaml
```

```bash
uv run ossie-neo4j to-neo4j tests/fixtures/ossie/tpcds_semantic_model.yaml -o tpcds.ogs.yaml
```
A semantic model on its own produces an OGS document with tables and no nodes. Deriving nodes from datasets is core-only mode, in phase 4.

Output:
- `to-ossie` writes an ontology if the model has nodes, a semantic model if it has tables, and a mapping document if it has nodes, tables and mappings.
- Use `--out-dir DIR` (with `--format yaml|json`) for more than one document. `-o FILE` works when there's one.
- Without either option, YAML goes to stdout, one `---`-separated document per output.
- A summary of the loss report goes to stderr.

Flags:
- `--report FILE` writes the full loss report as JSON;
- `--strict` exits with code 2 if anything was approximated or dropped;
- `--no-stash` produces clean output with no vendor annotations, extensions or custom data. Everything that would have been stashed is reported as dropped.

## 5. Testing strategy

- **Fixtures:**
  - OGS: the spec's `movies` example (both shorthand and longhand forms).
  - Ossie: `minimal.yaml`, the `flights.{ontology,semantic_model,mapping}.yaml` trio, `foaf_owl_import.yaml`, and `tpcds_semantic_model.yaml`.
- **Round-trip tests:** both `OGS → Ossie → OGS` and `Ossie → OGS → Ossie` must give a result equal to the input after normalization (with stashing on). Each lossy construct gets a golden loss report.
- **Schema validity:** every output must validate against the target spec's schema and pass the OGS library's "All" validator set.
- **Property-based tests** (Hypothesis): generate random valid OGS models and check that round-trips never raise and never silently drop data.
- **Upstream:** later, add the converter to the `ossie-converters/interop` test matrix.

## 6. Open questions / risks

1. **OGS cardinality direction.** It is unclear whether `to.min_count` bounds relationships per `to` node or per `from` node; the docs and examples are ambiguous. Phase 1 reads a count as bounding how many relationships *each node at that end* has; this matches the docs' reading of `to: min_count: 1` as "every movie has at least one actor". The rule lives in one function, `convert/common.py:link_multiplicity`, so it is easy to flip once the OGS authors confirm.
2. **Ossie `requires` / `derived_by` grammar.** It is not formally pinned, so we stash these instead of translating them, until a grammar exists in the spec.
3. **Ossie version churn.** 0.3.0.dev0 changes weekly (recent renames: `custom_extensions` → `nonportable_extensions`, `uri` → `iri`, and the `semantic_model` wrapper was removed). We pin a commit and isolate Ossie models behind one module.
4. **`NEO4J` vendor token.** Propose registering it in Ossie's `extensions/vendors.md`.
5. **Case sensitivity.** OGS ids and labels are case-sensitive; Ossie names resolve case-insensitively. The converter must detect collisions such as `Person` vs `person` and rename them deterministically.
6. **Same relationship type across node pairs.** In OGS this needs one entry per pair; Ossie relationships are scoped to a concept. We use `Concept.relname` as the stable key.

## 7. Phased plan

| Phase | Deliverable |
|---|---|
| 0 ✅ | Repo scaffold, pinned schemas, Pydantic models for both specs, fixtures, normalizers |
| 1 ✅ | OGS nodes/relationships/properties ↔ Ossie ontology, with stashing and the loss report |
| 2 ✅ | OGS tables ↔ Ossie semantic model (datasets, fields, keys, FK relationships, metrics stash) |
| 3 ✅ | OGS mappings ↔ Ossie mapping document |
| 4 | Core-only mode, Ossie 0.1.1 → 0.3.0 migration on input, CLI polish |
| 5 | Upstream: contribute to `apache/ossie-converters`, and propose the `NEO4J` vendor token |

## References

- Neo4j Ontology Graph Spec: https://neo4j.com/docs/ontology-graph-spec/ (format reference, shorthand, rules, interoperability, libraries/migration)
- OGS schema: https://neo4j.com/docs/ontology-graph-spec/1.1.0/schema.json
- Apache Ossie: https://ossie.apache.org/ · https://github.com/apache/ossie (`core-spec/`, `ontology/`, `examples/`)
- Ossie converters: https://github.com/apache/ossie-converters (see `ontology/`, `linkml/`, and `solid/` for prior art)
- Ossie incubator proposal: https://cwiki.apache.org/confluence/spaces/INCUBATOR/pages/430408796/OssieProposal
