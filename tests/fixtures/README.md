# Test fixtures

| File | Origin |
|---|---|
| `ogs/movies.yaml`, `ogs/movies.json` | Verbatim from the OGS "Getting started" page (https://neo4j.com/docs/ontology-graph-spec/format/getting-started/). Uses shorthand. |
| `ogs/movies.longhand.yaml` | `movies.yaml` written in longhand, following the "Shorthand and longhand" page. This is the expected output of `ogs.normalize`. |
| `ogs/movies-import.yaml` | Written for this project. It covers the OGS features the spec's own examples leave out: implied and optional labels, keys, vector, list and temporal types, counts, indexes, display, tables, foreign keys, and all three mapping kinds. |
| `ossie/*` | Verbatim from [apache/ossie](https://github.com/apache/ossie) `examples/` at commit `6792db4` (Apache-2.0; the license headers are kept). |

The Ossie fixtures include:
- `minimal.{yaml,json}`
- `tpcds_semantic_model.yaml` (a large semantic model)
- the `flights.*` and `sap_business_partner.*` sets, each a semantic model, an ontology and a mapping document
- `foaf_owl_import.yaml`, an ontology imported from OWL that uses `iri`, `prefixes` and `annotations`
