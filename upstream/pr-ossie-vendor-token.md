# Draft PR: apache/ossie — register the NEO4J vendor token

**Branch:** `register-neo4j-vendor` (staged in `research/ossie`, worktree `upstream/work/ossie`)
**Title:** `docs(extensions): register the NEO4J vendor token`

## Summary

Registers `NEO4J` in `extensions/vendors.md` for the `nonportable_extensions` written by the
Neo4j Ontology Graph Spec converter proposed for apache/ossie-converters (see the dev@ thread
<link>). It also adds the matching `OssieVendor.NEO4J` member, which
`python/tests/test_models.py::test_vendor_enum_matches_the_registry` requires.

The registry is informative, not normative ("Presence in a list does not imply endorsement"),
so this is not a specification change, and it needs no `docs/spec_changes.md` entry.

The converter writes `NEO4J` entries on models, datasets, fields and relationships in
semantic models, and at the root of mapping documents. Its package registers a validator under
`ossie.vendor_validators`, so `validate.py --vendor NEO4J` can check those payloads.

## Related Issues

dev@ thread: <link>. Converter PR: <link>.

## Checks run locally

- `validation/tests` (includes the registry-order test): 1104 passed, 1 skipped
- `python` SDK tests (includes the enum/registry sync test): 345 passed
