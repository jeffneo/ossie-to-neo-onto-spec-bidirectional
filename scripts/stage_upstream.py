"""Stage the upstream contributions as local git worktrees, ready to review and push.

It builds two branches from the research clones, without touching their main checkouts:

- ``research/ossie-converters``, branch ``add-neo4j-converter``, worktree
  ``upstream/work/ossie-converters``. This adds the ``neo4j/`` converter (package, tests,
  README, pyproject, uv.lock) and the repo wiring CONTRIBUTING.md asks for: labeler,
  dependabot, CI workflow, issue-template dropdowns. It also registers the converter in the
  interop matrix (manifest, content inspector, tests, READMEs).
- ``research/ossie``, branch ``register-neo4j-vendor``, worktree ``upstream/work/ossie``. This
  adds the ``NEO4J`` row to ``extensions/vendors.md``.

ASF license headers are added to every new text file that RAT checks. Nothing is pushed.
``--commit`` commits each branch locally, so it can be reviewed with ``git show``.

Usage:  uv run python scripts/stage_upstream.py [--commit]
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONVERTERS = ROOT / "research/ossie-converters"
OSSIE = ROOT / "research/ossie"
WORK = ROOT / "upstream/work"
TEMPLATES = ROOT / "upstream/templates"

HEADER = """Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License."""

COMMIT_TRAILER = "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"


def run(*cmd: str, cwd: Path | None = None) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def worktree(repo: Path, branch: str, path: Path, base: str = "HEAD") -> None:
    if path.exists():
        run("git", "worktree", "remove", "--force", str(path), cwd=repo)
    run("git", "worktree", "prune", cwd=repo)
    run("git", "worktree", "add", "-q", "-B", branch, str(path), base, cwd=repo)


# --- license headers -------------------------------------------------------------------------


def with_header(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "Licensed to the Apache Software Foundation" in text[:2000]:
        return
    if path.suffix in (".py", ".yaml", ".yml", ".toml"):
        block = "\n".join(f"# {line}".rstrip() for line in HEADER.splitlines()) + "\n\n"
        if text.startswith("#!"):
            first, _, rest = text.partition("\n")
            text = f"{first}\n{block}{rest}"
        else:
            text = block + text
    elif path.suffix == ".md":
        indented = "\n".join(f"  {line}".rstrip() for line in HEADER.splitlines())
        text = f"<!--\n{indented}\n-->\n\n{text}"
    else:
        return
    path.write_text(text, encoding="utf-8")


# --- text edits ------------------------------------------------------------------------------


def insert_before(path: Path, anchor: str, new: str, *, check: str | None = None) -> None:
    """Insert ``new`` before the first line that equals ``anchor``. Idempotent via ``check``."""
    text = path.read_text(encoding="utf-8")
    if (check or new) in text:
        return
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.rstrip("\n") == anchor:
            lines.insert(i, new)
            path.write_text("".join(lines), encoding="utf-8")
            return
    raise SystemExit(f"{path}: anchor not found: {anchor!r}")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit(f"{path}: expected one {old!r}, found {text.count(old)}")
    path.write_text(text.replace(old, new), encoding="utf-8")


# --- ossie-converters ------------------------------------------------------------------------


def stage_converter(work: Path) -> None:
    dest = work / "neo4j"
    if dest.exists():
        shutil.rmtree(dest)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
    shutil.copytree(ROOT / "src", dest / "src", ignore=ignore)
    shutil.copytree(ROOT / "tests", dest / "tests", ignore=ignore)
    shutil.copy(TEMPLATES / "README.md", dest / "README.md")
    shutil.copy(TEMPLATES / "pyproject.toml", dest / "pyproject.toml")
    for p in dest.rglob("*"):
        if p.is_file():
            with_header(p)
    run("uv", "lock", "-q", cwd=dest)

    gh = work / ".github"
    shutil.copy(TEMPLATES / "converter-neo4j-ci.yml", gh / "workflows/converter-neo4j-ci.yml")
    insert_before(
        gh / "labeler.yml",
        "converter/nvidia:",
        "converter/neo4j:\n  - changed-files:\n      - any-glob-to-any-file: 'neo4j/**'\n\n",
    )
    insert_before(gh / "dependabot.yml", '      - "/nvidia"', '      - "/neo4j"\n')
    # interop-ci.yml triggers on every converter path, under both push and pull_request.
    interop_ci = gh / "workflows/interop-ci.yml"
    text = interop_ci.read_text(encoding="utf-8")
    if "'neo4j/**'" not in text:
        text = text.replace("      - 'nvidia/**'\n", "      - 'neo4j/**'\n      - 'nvidia/**'\n")
        interop_ci.write_text(text, encoding="utf-8")
    for name in ("bug_report.yml", "feature_request.yml"):
        insert_before(gh / "ISSUE_TEMPLATE" / name, "        - nvidia", "        - neo4j\n")

    insert_before(
        work / "interop/manifest.yaml",
        "  nvidia:",
        "  neo4j:\n"
        "    cwd: neo4j\n"
        "    toolchain: uv\n"
        "    prepare: [uv, sync, --locked, --no-dev]\n"
        "    import: [uv, run, --no-sync, ossie-neo4j, import, -i, '{input}', -o, '{output}']\n"
        "    export: [uv, run, --no-sync, ossie-neo4j, export, -i, '{input}', -o, '{output}']\n"
        "    native_format: neo4j\n"
        "    output: file\n"
        "    fixtures:\n"
        "      movies: neo4j/tests/fixtures/ogs/movies-import.yaml\n",
        check="  neo4j:\n",
    )
    insert_before(
        work / "interop/checks.py",
        '    if native_format == "nvidia":',
        '    if native_format == "neo4j":\n'
        "        # An OGS document: properties on nodes and relationships, plus the columns of\n"
        "        # its data source tables.\n"
        '        ext = data.get("extensions") or {}\n'
        "        return (\n"
        "            sum(\n"
        '                len(n.get("properties") or {})\n'
        '                for n in (data.get("nodes") or {}).values()\n'
        "            )\n"
        "            + sum(\n"
        '                len(r.get("properties") or {})\n'
        '                for r in (data.get("relationships") or {}).values()\n'
        "            )\n"
        "            + sum(\n"
        '                len(t.get("columns") or [])\n'
        '                for t in (ext.get("data_source_tables") or {}).values()\n'
        "            )\n"
        "        )\n",
        check='native_format == "neo4j"',
    )
    insert_before(
        work / "interop/manifest.py",
        '            "nvidia",',
        '            "neo4j",\n',
        check='"neo4j",',
    )
    tests = work / "interop/tests/test_registration.py"
    text = tests.read_text(encoding="utf-8")
    if '"neo4j"' not in text:
        tests.write_text(
            text.replace('        "microsoft",\n', '        "microsoft",\n        "neo4j",\n'),
            encoding="utf-8",
        )

    replace_once(
        work / "README.md",
        "| `SOLID` | Solid semantic model |\n",
        "| `SOLID` | Solid semantic model |\n| `NEO4J` | Neo4j Ontology Graph Spec (OGS) |\n",
    )
    for readme in (work / "README.md", work / "interop/README.md"):
        text = readme.read_text(encoding="utf-8")
        readme.write_text(
            re.sub(r"all 28 converter projects", "all 29 converter projects", text),
            encoding="utf-8",
        )
    replace_once(
        work / "interop/README.md",
        "| Atlan, Honeydew, Lightdash, Microsoft, NVIDIA, Omni, OrionBelt, Sigma, Wisdom |",
        "| Atlan, Honeydew, Lightdash, Microsoft, Neo4j, NVIDIA, Omni, OrionBelt, Sigma, Wisdom |",
    )


# --- ossie -----------------------------------------------------------------------------------


def stage_vendor(work: Path) -> None:
    replace_once(
        work / "extensions/vendors.md",
        "| `HONEYDEW` | Honeydew-specific attributes |\n",
        "| `HONEYDEW` | Honeydew-specific attributes |\n| `NEO4J` | Neo4j-specific attributes |\n",
    )
    # python/tests/test_models.py requires OssieVendor to list exactly the registered tokens.
    replace_once(
        work / "python/src/ossie/models.py",
        '    GASP = "GASP"\n',
        '    GASP = "GASP"\n    NEO4J = "NEO4J"\n',
    )


CONVERTER_MESSAGE = """feat(neo4j): add Neo4j Ontology Graph Spec converter

Adds a bidirectional converter between the Neo4j Ontology Graph Spec (OGS 1.1.0)
and Apache Ossie ontology, semantic model and mapping documents, with a
NEO4J vendor validator, core-only graph derivation, 0.1.1 input migration and
an interop matrix registration."""

VENDOR_MESSAGE = """docs(extensions): register the NEO4J vendor token

Registers NEO4J for nonportable_extensions written by the Neo4j Ontology Graph
Spec converter in apache/ossie-converters."""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--commit", action="store_true", help="commit each branch locally")
    args = p.parse_args()

    conv_work, ossie_work = WORK / "ossie-converters", WORK / "ossie"
    worktree(CONVERTERS, "add-neo4j-converter", conv_work)
    worktree(OSSIE, "register-neo4j-vendor", ossie_work)
    stage_converter(conv_work)
    stage_vendor(ossie_work)
    for path, message in ((conv_work, CONVERTER_MESSAGE), (ossie_work, VENDOR_MESSAGE)):
        run("git", "add", "-A", cwd=path)
        print(f"== {path.relative_to(ROOT)}")
        print(run("git", "diff", "--cached", "--stat", cwd=path).rstrip())
        if args.commit:
            run("git", "commit", "-q", "-m", message + COMMIT_TRAILER, cwd=path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
