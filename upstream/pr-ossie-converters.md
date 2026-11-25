# Draft PR: apache/ossie-converters — add the Neo4j converter

**Branch:** `add-neo4j-converter` (staged in `research/ossie-converters`, worktree
`upstream/work/ossie-converters`)
**Title:** `feat(neo4j): add Neo4j Ontology Graph Spec converter`

## Summary

Adds `neo4j/` (`apache-ossie-neo4j`), a bidirectional converter between the
[Neo4j Ontology Graph Spec](https://neo4j.com/docs/ontology-graph-spec/) (OGS 1.1.0) and
Apache Ossie. One OGS document corresponds to the full set of three Ossie documents: nodes and
relationships to the ontology, `data_source_tables` to the semantic model, and
`data_source_mappings` to the mapping document.

- **Lossless round trips.** Data without a native home is kept in `NEO4J`
  `nonportable_extensions` / `neo4j:*` annotations (going to Ossie) or `custom.ossie` (going to
  OGS). It is restored only while the native data it depends on is unchanged. Every ontology,
  semantic model and mapping example in apache/ossie round-trips losslessly.
- **Loss report.** Every conversion marks each construct as stashed, approximated or dropped.
  `--strict` exits 2 if anything was approximated or dropped.
- **Core-only mode.** `--derive-graph` derives keyed nodes, relationships and importable
  mappings from a semantic model on its own. The interop `export` command uses it.
- **0.1.1 input** is migrated on read, including keys that name source columns (#486).
- **`NEO4J` vendor validator** under `ossie.vendor_validators`. Its registration in
  apache/ossie is a separate PR: <link>.

The converter README covers the mapping rules.

Repository wiring (per CONTRIBUTING.md): `.github/labeler.yml`, `.github/dependabot.yml`,
`converter-neo4j-ci.yml`, the issue-template dropdowns, and `interop-ci.yml` paths. Interop
registration: the `interop/manifest.yaml` entry, `neo4j` in `NATIVE_SHAPES`, a `neo4j` native
content inspector in `interop/checks.py` (node and relationship properties plus table columns),
and the registration tests. Converter counts in the READMEs go from 28 to 29.

## Related Issues

dev@ thread: <link>. Vendor token: <link>.

## Interop results (local run: `--converters neo4j,dbt,databricks`)

- canonical tpcds / flights → neo4j: **OK**. neo4j movies → neo4j / databricks: **OK**
  (Tiers A and B). databricks sales ↔ neo4j: **OK**. All portability cases → neo4j: **OK**.
- neo4j movies → dbt: **FAIL** in dbt's exporter. The movies fixture has a composite primary
  key, which dbt rejects ("MetricFlow entities cannot represent composite keys"); canonical
  tpcds → dbt fails the same way.
- dbt orders → neo4j, Tier B: **LOSSY**, with no inventory changes. The grade comes from an
  Ossie validator warning already in the dbt-imported model (metric `total_amount` references
  `orders.amount`, which is not a declared field). dbt → databricks gets the same grade.

## Checklist

### Converters
- [x] New converters include tests under the converter's test directory (250 tests)
- [x] `.github/labeler.yml`, `.github/dependabot.yml`, a `converter-neo4j-ci.yml` workflow and
  the `Converter` dropdown in `bug_report.yml` and `feature_request.yml` are updated for the new
  path

### Documentation
- [x] The converter's `README.md` describes usage, the mapping rules and limitations

### Tests
- [x] `neo4j`: 250 passed on Python 3.11 and 3.14, including schema-drift tests against the
  `ossie` submodule
- [x] `interop/tests`: 372 passed, 52 skipped (370 baseline + 2 new `neo4j` cases)
- [x] `dev/check-issue-templates` passes

### Compliance
- [x] ASF license headers on all new source files (`dev/check-license` passes)
- [x] No new third-party dependencies: pydantic, PyYAML, jsonschema and referencing are
  already used by other converters
- [ ] Vendored third-party content, **to confirm before opening**: the OGS 1.1.0 JSON Schema
  (`src/ossie_neo4j/schemas/ogs-1.1.0/schema.json`) and the `movies` fixtures derived from the
  OGS docs. The Ossie schemas and examples are copies of Apache-2.0 apache/ossie files.
