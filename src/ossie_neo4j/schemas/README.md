# Pinned schemas

These are vendored copies of the normative JSON Schemas. They are loaded offline by `ossie_neo4j.schemas`. Do not edit them by hand: re-copy them from the source instead.

| Folder | Source |
|---|---|
| `ogs-1.1.0/schema.json` | https://neo4j.com/docs/ontology-graph-spec/1.1.0/schema.json, captured 2026-11-24 |
| `ossie-0.3.0.dev0/{ossie-schema,ontology,mapping}.json` | [apache/ossie](https://github.com/apache/ossie) at commit `6792db4f553695f258cbe288f9a41b06f1ac2169`, files `core-spec/ossie-schema.json`, `ontology/ontology.json` and `ontology/mapping.json`. Licensed under Apache-2.0 |

The Ossie schemas refer to each other through `https://raw.githubusercontent.com/apache/ossie/main/...` URLs. `ossie_neo4j.schemas` resolves those URLs to the local copies, so validation never fetches over the network and always uses the pinned commit.

To move to a new Ossie commit:
1. Update `research/ossie`.
2. Copy the three files here.
3. Update the commit hash in this file, in `ossie_neo4j/ossie/__init__.py` (`PINNED_COMMIT`), and in `research/README.md`.
4. Run the tests.
