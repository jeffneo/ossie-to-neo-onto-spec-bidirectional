# Research snapshots

These are reference copies of both specs, captured on 2026-11-24. Do not edit them; they exist so we can read the specs offline.

| Folder | What it is | Source |
|---|---|---|
| `neo4j-ogs/` | Every page of the Neo4j Ontology Graph Spec docs, saved as `.html` and extracted `.txt`, plus the normative `schema.json` (1.1.0) | https://neo4j.com/docs/ontology-graph-spec/ |
| `ossie/` | Git clone of the Apache Ossie spec repo at commit `6792db4f553695f258cbe288f9a41b06f1ac2169` (version 0.3.0.dev0) | https://github.com/apache/ossie |
| `ossie-converters/` | Git clone of the Ossie converters repo at commit `4661b0d93c0ff79d060feeb7c9a79821c3879065` | https://github.com/apache/ossie-converters |

Key files:
- `neo4j-ogs/schema.json`
- `ossie/core-spec/ossie-schema.json`
- `ossie/ontology/ontology.json`
- `ossie/ontology/mapping.json`
- `ossie/examples/`
- `ossie-converters/{ontology,linkml,solid}/`

The two Ossie clones are gitignored. To recreate them at the pinned commits in a fresh checkout:

```bash
git clone https://github.com/apache/ossie research/ossie && git -C research/ossie checkout 6792db4f553695f258cbe288f9a41b06f1ac2169
```

```bash
git clone https://github.com/apache/ossie-converters research/ossie-converters && git -C research/ossie-converters checkout 4661b0d93c0ff79d060feeb7c9a79821c3879065
```

To refresh the Ossie clones:

```bash
git -C research/ossie pull
```

```bash
git -C research/ossie-converters pull
```

After pulling, update the commit hashes above. The commit pin in the top-level README should change only when we deliberately move to a new version.
