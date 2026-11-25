# Upstream contribution (phase 5)

Everything needed to contribute this converter to Apache Ossie is prepared here. **Nothing has
been pushed or sent.** Publishing means forking the apache repos, pushing branches, opening PRs
and posting to the list, so it needs your go-ahead.

| What | Where |
|---|---|
| Converter for apache/ossie-converters | branch `add-neo4j-converter` in `research/ossie-converters`, worktree `upstream/work/ossie-converters` (1 local commit, 74 files) |
| `NEO4J` vendor token for apache/ossie | branch `register-neo4j-vendor` in `research/ossie`, worktree `upstream/work/ossie` (1 local commit: `vendors.md` row + `OssieVendor.NEO4J`) |
| dev@ email draft | [`dev-list-email.md`](dev-list-email.md) |
| PR description drafts | [`pr-ossie-vendor-token.md`](pr-ossie-vendor-token.md), [`pr-ossie-converters.md`](pr-ossie-converters.md) |
| Templates the staging uses | [`templates/`](templates/) (upstream README, pyproject, CI workflow) |

Rebuild both branches from the current code at any time with:

```bash
uv run python scripts/stage_upstream.py --commit
```

## Verified locally on the staged branches

- `neo4j/`: 250 tests pass on Python 3.11 and 3.14, including schema drift against the pinned
  `ossie` submodule (`d27a6be`).
- `interop/tests`: 372 passed (370 baseline + 2 new). Interop matrix with dbt and databricks:
  every neo4j case passes, except two caused by dbt (see the PR draft).
- `dev/check-issue-templates` and Apache RAT (`dev/check-license`) pass.
- apache/ossie: `validation/tests` (1104) and the Python SDK tests (345) pass with the vendor
  change.

## Before publishing: decisions for you

1. **Rights to Neo4j-sourced content.** The OGS 1.1.0 schema and the `movies` fixtures come
   from neo4j.com docs, and the staging script added ASF headers to the fixtures. Confirm
   Neo4j's license for these (e.g. github.com/neo4j/ontology-graph-spec), and that Neo4j is
   happy for them to be contributed under Apache-2.0. If not, swap the fixtures for authored
   ones and add a `NOTICE`/`LICENSE` entry for the schema.
2. **Employer approval.** Ossie requires no CLA or CCLA for contributions. Check Neo4j's own
   open-source contribution policy.
3. **AI-assistance disclosure.** The commits carry a `Co-Authored-By: Claude` trailer, and the
   dev@ draft discloses AI assistance per the
   [ASF Generative Tooling guidance](https://www.apache.org/legal/generative-tooling.html). You
   are responsible for the code you submit, so review it.
4. **PR size.** The converter PR is large (about 15k lines, about half of them tests and
   fixtures). Reviewers may prefer it split, for example ontology layer first, then semantic
   model, then mappings. The tests also copy apache/ossie examples that could instead be read
   from the `ossie` submodule.

## Suggested order

1. Subscribe to `dev@ossie.apache.org` and send the email.
2. Fork apache/ossie, push `register-neo4j-vendor`, and open the vendor PR.
3. Optionally, file a **New converter** issue in apache/ossie-converters.
4. Fork apache/ossie-converters, push `add-neo4j-converter`, and open the converter PR.
