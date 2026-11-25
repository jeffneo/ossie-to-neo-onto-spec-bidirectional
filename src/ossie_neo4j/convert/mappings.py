"""The mapping layer: OGS ``extensions.data_source_mappings`` ⇄ an Ossie mapping document.

**OGS ➜ Ossie**, per mapping:

- ``NODE`` (node, table, key, properties): an *object mapping* that identifies the node's
  concept by its key properties, as ``referent_mappings`` with ``expression: TABLE.column``.
  Plus a *link mapping* with the same identity whose children populate the other properties.
- ``RELATIONSHIP`` (relationship, table, from_node, to_node, properties): a link-mapping tree on
  the source concept. Its levels follow the relationship's roles: the source identity, then the
  target identity, then one level per relationship property. The last level names the
  relationship.
- ``QUERY``: no Ossie equivalent. It is kept, as are each mapping's set id, ``id``, ``mode``,
  ``match_label`` and ``depends_on``, in the mapping document's ``nonportable_extensions``
  under ``NEO4J``.

**Ossie ➜ OGS** parses the trees back. Constructs OGS can't express are reported as
approximated and left out of the OGS mappings: filtered or computed expressions, nested
referents (entities identified through other entities), expressions across datasets, and
unary or reified relationships. Whether the original document is kept is decided by the
caller (see ``to_ogs.ossie_to_ogs``).
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any

from ..ogs.models import GraphModelExtensions
from .naming import sql_identifier
from .report import Loss, LossReport
from .semantic import TableIndex

VENDOR = "NEO4J"
_EXTRAS = ("id", "mode", "match_label", "depends_on")


@dataclass
class LinkInfo:
    """How one OGS relationship corresponds to an Ossie relationship."""

    concept: str  # declaring (source) concept
    name: str  # Ossie relationship name
    target: str  # target concept
    role_keys: list[str]  # Ossie role order: "target" or an OGS relationship property id
    role_concepts: list[str]  # the concept of each role, aligned with role_keys


@dataclass
class MapContext:
    concept_of: dict[str, str]  # node id ➜ concept
    label_of: dict[str, str]  # node id ➜ identifying label
    props: dict[str, set[str]]  # node id ➜ property ids
    value_concept: dict[tuple[str, str], str]  # (concept, attribute) ➜ value concept
    identify_by: dict[str, list[str]]  # concept ➜ identify_by
    links: dict[str, LinkInfo]  # OGS relationship id ➜ link info
    tables: TableIndex
    node_of: dict[str, str] = field(default_factory=dict)
    link_by_name: dict[tuple[str, str], str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.node_of = {c: n for n, c in self.concept_of.items()}
        self.link_by_name = {(i.concept, i.name): rid for rid, i in self.links.items()}


def build_context(
    ogs: dict, onto: dict, concept_of: dict[str, str], links: dict, tables: TableIndex
) -> MapContext:
    """Build the context from the OGS document and the Ossie ontology (both as dicts)."""
    nodes = ogs.get("nodes") or {}
    value_concept: dict[tuple[str, str], str] = {}
    identify_by: dict[str, list[str]] = {}
    for c in onto.get("ontology") or []:
        identify_by[c["concept"]] = list(c.get("identify_by") or [])
        for r in c.get("relationships") or []:
            roles = r.get("roles") or []
            if len(roles) == 1:
                value_concept[(c["concept"], r["name"])] = roles[0]["concept"]
    return MapContext(
        concept_of=dict(concept_of),
        label_of={
            nid: ((n.get("labels") or {}).get("identifier") or concept_of.get(nid, nid))
            for nid, n in nodes.items()
        },
        props={nid: set(n.get("properties") or {}) for nid, n in nodes.items()},
        value_concept=value_concept,
        identify_by=identify_by,
        links=links,
        tables=tables,
    )


def normalize_mappings(mappings: dict | None) -> dict:
    """OGS mapping sets in canonical JSON form, for comparisons."""
    if not mappings:
        return {}
    ext = GraphModelExtensions.model_validate({"data_source_mappings": mappings})
    return ext.model_dump(mode="json", by_alias=True, exclude_none=True)["data_source_mappings"]


# --- expressions -----------------------------------------------------------------------------

_PART = r'(?:"((?:[^"]|"")+)"|([A-Za-z_][A-Za-z0-9_$]*))'
_COLUMN_REF = re.compile(rf"^\s*{_PART}\s*\.\s*{_PART}\s*$")


def column_ref(dataset: str, field_name: str) -> str:
    return f"{sql_identifier(dataset)}.{sql_identifier(field_name)}"


def parse_column_ref(expr: str | None) -> tuple[str, str] | None:
    """``DATASET.field`` ➜ ``(dataset, field)``; anything more complex ➜ None."""
    if not expr:
        return None
    m = _COLUMN_REF.match(expr)
    if not m:
        return None
    ds = m.group(1).replace('""', '"') if m.group(1) else m.group(2)
    fld = m.group(3).replace('""', '"') if m.group(3) else m.group(4)
    return ds, fld


# --- OGS ➜ Ossie -----------------------------------------------------------------------------


class _Gen:
    def __init__(self, ctx: MapContext, report: LossReport, stash: bool):
        self.ctx = ctx
        self.r = report
        self.stash = stash

    def expr(self, table: str, column: str) -> str:
        ds = self.ctx.tables.dataset.get(table, table)
        fld = self.ctx.tables.fields.get(table, {}).get(column, column)
        return column_ref(ds, fld)

    def identity(self, table: str, props: dict, key: list[str]) -> dict | None:
        if not key or any(k not in props for k in key):
            return None
        return {
            "referent_mappings": [
                {"relationship": k, "expression": self.expr(table, props[k]["column"])} for k in key
            ]
        }

    def run(self, mappings: dict) -> tuple[list[dict], dict]:
        cms: dict[str, dict] = {}
        sets: dict[str, list[dict]] = {}

        def cm(concept: str) -> dict:
            return cms.setdefault(concept, {"concept": concept})

        for set_id, items in mappings.items():
            entries = sets.setdefault(set_id, [])
            for i, m in enumerate(items):
                path = f"extensions.data_source_mappings.{set_id}.{i}"
                kind = m["type"]
                if kind == "QUERY":
                    entries.append({"raw": m})
                    self._kept(path, "mapping_query", "QUERY mappings have no Ossie equivalent")
                    continue
                built = self.node(m) if kind == "NODE" else self.relationship(m)
                if built is None:
                    entries.append({"raw": m})
                    self._kept(
                        path,
                        "mapping_not_representable",
                        f"{kind} mapping lacks the key or role columns an Ossie mapping needs",
                    )
                    continue
                concept, objs, links, entry = built
                c = cm(concept)
                if objs:
                    c.setdefault("object_mappings", []).extend(objs)
                if links:
                    c.setdefault("link_mappings", []).extend(links)
                entries.append(entry)
        return list(cms.values()), {"sets": sets}

    def _kept(self, path: str, code: str, why: str) -> None:
        if self.stash:
            self.r.add(Loss.STASHED, code, path, why + "; kept in nonportable_extensions[NEO4J]")
        else:
            self.r.add(Loss.DROPPED, code, path, why)

    def node(self, m: dict):
        nid, table = m["node"], m["table"]
        concept = self.ctx.concept_of.get(nid)
        if concept is None:
            return None
        props = m.get("properties") or {}
        key = m.get("key") or [p for p in self.ctx.identify_by.get(concept, []) if p in props]
        ident = self.identity(table, props, key)
        if ident is None:
            return None
        links = []
        others = [p for p in props if p not in key]
        if others:
            children = []
            for p in others:
                om: dict[str, Any] = {}
                vc = self.ctx.value_concept.get((concept, p))
                if vc:
                    om["concept"] = vc
                om["expression"] = self.expr(table, props[p]["column"])
                children.append({"object_mapping": om, "relationship": p})
            links.append({"object_mapping": copy.deepcopy(ident), "children": children})
        entry: dict[str, Any] = {"type": "NODE", "node": nid, "table": table}
        entry.update({k: m[k] for k in _EXTRAS if m.get(k) is not None})
        if m.get("key") is None:
            entry["key_absent"] = True
        return concept, [ident], links, entry

    def relationship(self, m: dict):
        info = self.ctx.links.get(m["relationship"])
        if info is None:
            return None
        table = m["table"]
        src_props = (m.get("from_node") or {}).get("properties") or {}
        tgt_props = (m.get("to_node") or {}).get("properties") or {}
        rel_props = m.get("properties") or {}
        src = self.identity(table, src_props, list(src_props))
        tgt = self.identity(table, tgt_props, list(tgt_props))
        if (
            src is None
            or tgt is None
            or any(k != "target" and k not in rel_props for k in info.role_keys)
        ):
            return None
        levels = []
        for k, concept in zip(info.role_keys, info.role_concepts, strict=True):
            if k == "target":
                levels.append({"object_mapping": {"concept": concept, **tgt}})
            else:
                expr = self.expr(table, rel_props[k]["column"])
                levels.append({"object_mapping": {"concept": concept, "expression": expr}})
        levels[-1]["relationship"] = info.name
        for parent, child in zip(levels, levels[1:], strict=False):
            parent["children"] = [child]
        root = {"object_mapping": src, "children": [levels[0]]}

        entry: dict[str, Any] = {"type": "RELATIONSHIP", "relationship": m["relationship"]}
        entry["table"] = table
        entry.update({k: m[k] for k in _EXTRAS if m.get(k) is not None})
        if m.get("key") is not None:
            entry["key"] = m["key"]
        src_nid = self.ctx.node_of.get(info.concept)
        tgt_nid = self.ctx.node_of.get(info.target)
        default_from = self.ctx.label_of.get(src_nid, info.concept)
        default_to = self.ctx.label_of.get(tgt_nid, info.target)
        if (m.get("from_node") or {}).get("label") != default_from:
            entry["from_label"] = (m.get("from_node") or {}).get("label")
        if (m.get("to_node") or {}).get("label") != default_to:
            entry["to_label"] = (m.get("to_node") or {}).get("label")
        return info.concept, [], [root], entry


def ogs_to_ossie_mappings(
    mappings: dict, ctx: MapContext, report: LossReport, *, stash: bool = True
) -> tuple[list[dict], dict | None]:
    """OGS mapping sets ➜ ``(concept_mappings, NEO4J data or None)``.

    The NEO4J data restores set ids, order and OGS-only details. It is None when parsing the
    concept mappings alone already reproduces the OGS mappings exactly.
    """
    cms, blob = _Gen(ctx, report, stash).run(mappings)
    reparsed = ossie_to_ogs_mappings(cms, None, ctx, LossReport("scratch"))
    if normalize_mappings(reparsed) == normalize_mappings(mappings):
        return cms, None
    return cms, (blob if stash else None)


# --- Ossie ➜ OGS -----------------------------------------------------------------------------


class _Parse:
    def __init__(self, ctx: MapContext, report: LossReport):
        self.ctx = ctx
        self.r = report
        self.out: list[dict] = []
        self.node_maps: dict[tuple, dict] = {}

    def lossy(self, code: str, path: str, why: str) -> None:
        self.r.add(Loss.APPROXIMATED, code, path, why)

    def column(self, expr: str | None, path: str) -> tuple[str, str] | None:
        """``DATASET.field`` ➜ ``(table id, column)``, or report why not."""
        ref = parse_column_ref(expr)
        if ref is None:
            self.lossy(
                "mapping_expression_not_column",
                path,
                f"expression {expr!r} is not a plain DATASET.field reference",
            )
            return None
        table = self.ctx.tables.table_for(ref[0])
        col = self.ctx.tables.column_for(table, ref[1]) if table else None
        if col is None:
            self.lossy("mapping_unknown_field", path, f"{expr!r} names no known table column")
            return None
        return table, col

    def identity(self, concept: str, om: dict, path: str) -> tuple[str, dict] | None:
        """An object mapping that identifies ``concept`` ➜ ``(table, {property: column})``."""
        nid = self.ctx.node_of.get(concept)
        if nid is None:
            self.lossy("mapping_concept_not_node", path, f"{concept!r} is not a node in OGS")
            return None
        if "expression" in om:
            ident = [p for p in self.ctx.identify_by.get(concept, []) if p in self.ctx.props[nid]]
            if len(ident) != 1:
                self.lossy(
                    "mapping_no_simple_identifier",
                    path,
                    f"{concept!r} has no single identifying attribute for a plain expression",
                )
                return None
            hit = self.column(om["expression"], path)
            return (hit[0], {ident[0]: hit[1]}) if hit else None
        table = None
        props: dict[str, str] = {}
        for rm in om.get("referent_mappings") or []:
            rel = rm["relationship"].split(".")[-1]
            rpath = f"{path}.referent_mappings.{rel}"
            if "referent_mappings" in rm:
                self.lossy(
                    "mapping_nested_referent",
                    rpath,
                    f"{concept!r} is identified through another entity; an OGS key can only "
                    "use the node's own properties",
                )
                return None
            if rel not in self.ctx.props[nid]:
                self.lossy("mapping_unknown_relationship", rpath, f"{rel!r} is no property")
                return None
            hit = self.column(rm.get("expression"), rpath)
            if hit is None:
                return None
            if table is not None and hit[0] != table:
                self.lossy("mapping_cross_dataset", rpath, "identity spans several datasets")
                return None
            table = hit[0]
            props[rel] = hit[1]
        return (table, props) if table else None

    def node_mapping(self, nid: str, table: str, key: dict) -> dict:
        k = (nid, table, tuple(key.items()))
        if k not in self.node_maps:
            m = {
                "type": "NODE",
                "node": nid,
                "table": table,
                "properties": {p: {"column": c} for p, c in key.items()},
                "key": list(key),
            }
            self.node_maps[k] = m
            self.out.append(m)
        return self.node_maps[k]

    def run(self, concept_mappings: list[dict]) -> list[dict]:
        for i, cm in enumerate(concept_mappings):
            concept = cm["concept"]
            path = f"concept_mappings.{i}"
            for j, om in enumerate(cm.get("object_mappings") or []):
                hit = self.identity(concept, om, f"{path}.object_mappings.{j}")
                if hit:
                    self.node_mapping(self.ctx.node_of[concept], hit[0], hit[1])
            for j, lm in enumerate(cm.get("link_mappings") or []):
                self.tree(concept, lm, [], f"{path}.link_mappings.{j}")
        return self.out

    def tree(self, concept: str, lm: dict, prefix: list[dict], path: str) -> None:
        tup = [*prefix, lm["object_mapping"]]
        if lm.get("relationship"):
            self.links(concept, lm["relationship"], tup, path)
        for k, child in enumerate(lm.get("children") or []):
            self.tree(concept, child, tup, f"{path}.children.{k}")

    def links(self, concept: str, rel: str, tup: list[dict], path: str) -> None:
        nid = self.ctx.node_of.get(concept)
        if nid is None:
            self.lossy("mapping_concept_not_node", path, f"{concept!r} is not a node in OGS")
            return
        if len(tup) == 1:
            self.lossy(
                "mapping_unary",
                path,
                f"{rel!r} is unary (an optional label in OGS); OGS mappings can't set labels",
            )
            return
        rid = self.ctx.link_by_name.get((concept, rel))
        if rid is None and len(tup) == 2 and rel in self.ctx.props[nid]:
            src = self.identity(concept, tup[0], path)
            val = self.column(tup[1].get("expression"), path) if src else None
            if src and val:
                if val[0] != src[0]:
                    self.lossy("mapping_cross_dataset", path, "value from another dataset")
                    return
                m = self.node_mapping(nid, src[0], src[1])
                m["properties"][rel] = {"column": val[1]}
            return
        info = self.ctx.links.get(rid) if rid else None
        if info is None or len(tup) != 1 + len(info.role_keys):
            self.lossy(
                "mapping_unknown_relationship",
                path,
                f"{concept}.{rel} is not a relationship or property OGS can map",
            )
            return
        src = self.identity(concept, tup[0], path)
        if src is None:
            return
        table = src[0]
        mapping: dict[str, Any] = {"type": "RELATIONSHIP", "relationship": rid, "table": table}
        to_props: dict[str, str] = {}
        rel_props: dict[str, dict] = {}
        for k, om in zip(info.role_keys, tup[1:], strict=True):
            if k == "target":
                hit = self.identity(info.target, om, path)
                if hit is None:
                    return
                if hit[0] != table:
                    self.lossy("mapping_cross_dataset", path, "target from another dataset")
                    return
                to_props = hit[1]
            else:
                hit = self.column(om.get("expression"), path)
                if hit is None:
                    return
                if hit[0] != table:
                    self.lossy("mapping_cross_dataset", path, "value from another dataset")
                    return
                rel_props[k] = {"column": hit[1]}
        tgt_nid = self.ctx.node_of.get(info.target)
        mapping["from_node"] = {
            "label": self.ctx.label_of[nid],
            "properties": {p: {"column": c} for p, c in src[1].items()},
        }
        mapping["to_node"] = {
            "label": self.ctx.label_of.get(tgt_nid, info.target),
            "properties": {p: {"column": c} for p, c in to_props.items()},
        }
        if rel_props:
            mapping["properties"] = rel_props
        self.out.append(mapping)


def _apply_blob(parsed: list[dict], blob: dict | None) -> dict:
    """Restore set ids, order and OGS-only details recorded by ``ogs_to_ossie_mappings``."""
    if not blob:
        return {"default": parsed} if parsed else {}
    used = [False] * len(parsed)
    out: dict[str, list[dict]] = {}
    for set_id, entries in blob.get("sets", {}).items():
        lst = out.setdefault(set_id, [])
        for e in entries:
            if "raw" in e:
                lst.append(copy.deepcopy(e["raw"]))
                continue
            ref = "node" if e["type"] == "NODE" else "relationship"
            idx = next(
                (
                    i
                    for i, m in enumerate(parsed)
                    if not used[i]
                    and m["type"] == e["type"]
                    and m.get(ref) == e[ref]
                    and m["table"] == e["table"]
                ),
                None,
            )
            if idx is None:
                continue  # native wins: the mapping was removed in Ossie
            used[idx] = True
            m = copy.deepcopy(parsed[idx])
            m.update({k: e[k] for k in _EXTRAS if k in e})
            if e.get("key_absent"):
                m.pop("key", None)
            elif "key" in e:
                m["key"] = e["key"]
            for end, k in (("from_node", "from_label"), ("to_node", "to_label")):
                if k in e:
                    if e[k] is None:
                        m[end].pop("label", None)
                    else:
                        m[end]["label"] = e[k]
            lst.append(m)
    leftovers = [m for i, m in enumerate(parsed) if not used[i]]
    if leftovers:
        out.setdefault(next(reversed(out), "default"), []).extend(leftovers)
    return {k: v for k, v in out.items() if v}


def ossie_to_ogs_mappings(
    concept_mappings: list[dict], blob: dict | None, ctx: MapContext, report: LossReport
) -> dict:
    """Ossie concept mappings (+ our NEO4J data, if any) ➜ OGS mapping sets."""
    parsed = _Parse(ctx, report).run(concept_mappings)
    return _apply_blob(parsed, blob)
