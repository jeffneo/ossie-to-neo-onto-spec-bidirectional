# Draft: email to dev@ossie.apache.org

> Send from your own address once subscribed (`dev-subscribe@ossie.apache.org`). Fill in the
> placeholders. Converter contributions are coordinated on this list (apache/ossie
> CONTRIBUTING.md), so send it before opening the PRs.

**Subject:** [DISCUSS] Neo4j Ontology Graph Spec converter for ossie-converters

Hi all,

I'm <name>, and I work on <role> at Neo4j. I'd like to contribute a converter between Apache
Ossie and the Neo4j Ontology Graph Spec (OGS, https://neo4j.com/docs/ontology-graph-spec/),
plus a `NEO4J` entry in `extensions/vendors.md`.

OGS is a YAML/JSON format for property-graph schemas: nodes, relationships, properties and
constraints, plus optionally the relational tables and mappings that populate them. One OGS
document lines up with the full set of three Ossie documents:

- nodes / relationships / properties ⇄ the ontology
- `data_source_tables` ⇄ the semantic model
- `data_source_mappings` ⇄ the mapping document

The converter is a Python package in the usual layout (`neo4j/`, `apache-ossie-neo4j`). It
converts in both directions, and anything without a native home on the other side is kept in
`NEO4J` nonportable extensions / `neo4j:*` annotations, so a round trip doesn't lose it. Every
ontology, semantic model and mapping example in apache/ossie round-trips losslessly. Each
conversion produces a loss report that marks each construct as stashed, approximated or
dropped.

It also:
- registers a `NEO4J` validator under `ossie.vendor_validators`, for `validate.py --vendor NEO4J`;
- can derive a graph from a semantic model on its own (keyed nodes per dataset, relationships
  from the foreign keys, importable mappings); this is what its interop `export` command does;
- reads 0.1.1 documents through a migration that maps key columns to fields (#486);
- passes the interop matrix: the canonical tpcds and flights models, plus pairs with
  databricks and dbt.

A few modelling choices I'd welcome views on:
1. **Mapping documents.** OGS mappings can't express filtered expressions, nested referents or
   unary links (as in the Flights mapping). For those documents the converter keeps the
   original mapping document whole and restores it as long as the OGS side is unchanged,
   instead of degrading it.
2. **Property types.** Neo4j types without a built-in concept (ZONED DATETIME, POINT, VECTOR,
   ...) become generated `Neo4j<Type>` value types that extend the nearest built-in.
3. **Optional labels.** These map to unary relationships, and the reverse.

Unless there are objections, I'll open two PRs: the one-line vendor registration in
apache/ossie (with the matching `OssieVendor` enum member), then the converter in
apache/ossie-converters. Parts of the converter were developed with AI assistance (Claude), in
line with the ASF Generative Tooling guidance; I've reviewed it and take responsibility for it.

Thanks,
<name>
