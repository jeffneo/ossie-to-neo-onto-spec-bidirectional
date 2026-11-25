"""Ossie ontology ➜ OGS.

The input is a *normalized* Ossie ontology: full IRIs, and ``ai_context`` as an object. The
output is a plain dict in the shape of a longhand OGS document. See the README, section 3.1,
for the mapping rules.

Each relationship declared on an EntityType concept is classified by its roles:

============================================  ===============================================
roles                                         OGS
============================================  ===============================================
none (unary: "{Flight} was canceled")         an optional label on the node
one value concept                             a property (no multiplicity means LIST<...>)
one entity concept (or ``Any``)               a relationship
one entity concept plus value concepts        a relationship with properties
anything else (n-ary)                         reified as an intermediate node (approximated)
============================================  ===============================================
"""

from __future__ import annotations

from typing import Any

from ..ogs import SCHEMA_URL
from ..ogs.models import Constraint, Neo4jType
from ..ogs.models import to_dict as ogs_dict
from ..ossie.models import BUILTIN_CONCEPTS, ConceptType, Multiplicity, Ontology
from ..ossie.models import to_dict as ossie_dict
from .common import (
    DEFAULT_OGS_VERSION,
    OSSIE,
    attribute_verbalization,
    default_link_ids,
    default_node_id,
    default_target_role,
    derive_node_constraints,
    link_multiplicity,
    link_type,
    link_verbalization,
    merge_stashed_constraints,
    predicted_link_name,
    regenerated_counts,
    split_annotations,
    unary_verbalization,
)
from .naming import kebab, pascal, snake, unique, upper_snake
from .report import Loss, LossReport
from .types import element, element_concept, is_generated, list_of, resolve

_RELATIONSHIP_NATIVE = {"name", "description", "iri", "roles", "multiplicity", "verbalizes"}


class _ToOgs:
    def __init__(self, onto: Ontology, stash: bool):
        self.o = onto
        self.stash = stash
        self.r = LossReport("ossie->ogs")
        self.defs = {c.concept: ossie_dict(c) for c in onto.ontology}
        self.value_types = {
            n: d for n, d in self.defs.items() if d["type"] == ConceptType.VALUE_TYPE.value
        }
        self.stubs = {
            n
            for n, d in self.defs.items()
            if split_annotations(d.get("annotations"))[0].get("stub")
        }
        self.entities = [
            c
            for c in onto.ontology
            if c.type == ConceptType.ENTITY_TYPE and c.concept not in self.stubs
        ]
        self.node_id: dict[str, str] = {}
        taken: set[str] = set()
        for c in self.entities:
            ours, _ = split_annotations(c.annotations)
            nid = ours.get("node_id") or unique(default_node_id(c.concept), taken)
            taken.add(nid)
            self.node_id[c.concept] = nid
        self.nodes: dict[str, dict] = {}
        self.rels: dict[str, dict] = {}
        self.one_to_one: dict[str, list[str]] = {}

    # --- helpers -------------------------------------------------------------------------------

    def keep(self, target: dict, key: str, value: Any, path: str, code: str, why: str) -> None:
        """Stash an Ossie-only value in ``target``, the ``custom.ossie`` map of an OGS element."""
        if value is None or value == {} or value == []:
            return
        if not self.stash:
            self.r.add(Loss.DROPPED, code, path, why)
            return
        target[key] = value
        self.r.add(Loss.STASHED, code, path, why)

    def is_entity(self, concept: str) -> bool:
        if concept in BUILTIN_CONCEPTS:
            return BUILTIN_CONCEPTS[concept] == ConceptType.ENTITY_TYPE
        d = self.defs.get(concept)
        return d is not None and d["type"] == ConceptType.ENTITY_TYPE.value

    def value_type(self, concept: str, path: str) -> Neo4jType:
        t, approx = resolve(concept, self.value_types)
        if t is None:
            self.r.add(
                Loss.APPROXIMATED,
                "unresolved_value_type",
                path,
                f"value concept {concept!r} doesn't reach a built-in type; using STRING",
            )
            return Neo4jType.STRING
        if approx:
            self.r.add(
                Loss.APPROXIMATED,
                "decimal_as_float",
                path,
                f"{concept!r} is an exact decimal; Neo4j stores it as FLOAT",
            )
        return t

    def end(self, concept: str, ours: dict, which: str) -> dict:
        """The OGS relationship end for a role concept."""
        if concept == "Any" or split_annotations(self.defs.get(concept, {}).get("annotations"))[
            0
        ].get("unlabelled"):
            return {}
        if concept in self.node_id and not ours.get(f"{which}_label_only"):
            return {"node": self.node_id[concept]}
        return {"label": concept}

    def implied(self, concept: str) -> list[str]:
        """All transitive EntityType supertypes, breadth-first, excluding ``Any``."""
        out: list[str] = []
        todo = list(self.defs[concept].get("extends") or [])
        while todo:
            p = todo.pop(0)
            if p in out or p == "Any" or p == concept:
                continue
            out.append(p)
            todo.extend((self.defs.get(p) or {}).get("extends") or [])
        return out

    def ancestors_props(self, concept: str) -> dict[str, tuple[str, dict]]:
        """Attributes declared on supertypes: ``{prop_id: (owner_concept, property_dict)}``."""
        out: dict[str, tuple[str, dict]] = {}
        for anc in self.implied(concept):
            nid = self.node_id.get(anc)
            for pid, p in ((self.nodes.get(nid) or {}).get("properties") or {}).items():
                if not (
                    p.get("extensions", {}).get("custom", {}).get(OSSIE, {}).get("inherited_from")
                ):
                    out.setdefault(pid, (anc, p))
        return out

    # --- build ---------------------------------------------------------------------------------

    def run(self) -> dict:
        for c in self.entities:
            self.node(c)
        for c in self.entities:
            self.keys(c)

        o = self.o
        ours, theirs = split_annotations(o.annotations)
        out: dict[str, Any] = {
            "schema": SCHEMA_URL,
            "version": ours.get("version", DEFAULT_OGS_VERSION),
            "id": ours.get("id") or kebab(o.name),
        }
        if not ours.get("unnamed"):
            out["name"] = o.name
        if o.description:
            out["description"] = o.description
        if self.nodes:
            out["nodes"] = self.nodes
        if self.rels:
            out["relationships"] = self.rels

        ext: dict[str, Any] = {}
        for k in ("data_source_tables", "data_source_mappings"):
            if k in ours:
                ext[k] = ours[k]
        custom = dict(ours.get("custom") or {})
        root: dict[str, Any] = {}
        if o.ai_context is not None:
            self.keep(
                root,
                "ai_context",
                ossie_dict(o).get("ai_context"),
                "ai_context",
                "ontology_ai_context",
                "Neo4j has no AI context; kept in custom.ossie",
            )
        self.keep(
            root,
            "requires",
            o.requires,
            "requires",
            "ontology_requires",
            "ontology-level constraints are not enforceable in Neo4j; kept in custom.ossie",
        )
        self.keep(
            root,
            "annotations",
            theirs,
            "annotations",
            "annotations",
            "annotations kept in custom.ossie",
        )
        self.keep(
            root,
            "prefixes",
            o.prefixes,
            "prefixes",
            "prefixes",
            "IRI prefixes kept in custom.ossie, so IRIs can be compacted again",
        )
        if o.ontology_mappings:
            self.keep(
                root,
                "ontology_mappings",
                [ossie_dict(m) for m in o.ontology_mappings],
                "ontology_mappings",
                "embedded_ontology_mappings",
                "deprecated embedded mappings; converted natively in phase 3",
            )
        vts = [d for d in self.value_types.values() if not is_generated(d)]
        if vts:
            self.keep(
                root,
                "value_types",
                vts,
                "ontology",
                "value_types_flattened",
                f"{len(vts)} value type(s) flattened to Neo4j property types; "
                "definitions kept in custom.ossie",
            )
        # The order OGS ➜ Ossie would produce on its own: generated value types, entities,
        # stubs, then stashed value types. Record the real order only if it differs.
        actual = [c.concept for c in o.ontology]
        predicted = (
            [n for n in actual if n in self.value_types and is_generated(self.value_types[n])]
            + [c.concept for c in self.entities]
            + [n for n in actual if n in self.stubs]
            + [d["concept"] for d in vts]
        )
        if self.stash and actual != predicted:
            root["concept_order"] = actual
        if root:
            custom[OSSIE] = root
        if custom:
            ext["custom"] = custom
        if ext:
            out["extensions"] = ext
        return out

    def node(self, c) -> None:
        nid = self.node_id[c.concept]
        path = f"ontology.{c.concept}"
        ours, theirs = split_annotations(c.annotations)
        n: dict[str, Any] = {}
        labels: dict[str, Any] = {"identifier": c.concept}
        implied = self.implied(c.concept)
        if implied:
            labels["implied"] = implied
        if ours.get("labels_custom"):
            labels["extensions"] = {"custom": ours["labels_custom"]}
        n["labels"] = labels
        if c.description:
            n["description"] = c.description
        if ours.get("aliases"):
            n["aliases"] = ours["aliases"]
        if c.iri:
            n["reference"] = c.iri

        os: dict[str, Any] = {}
        props: dict[str, dict] = {}
        optional: list[str] = []
        unary_stash: dict[str, dict] = {}
        one_to_one: list[str] = []
        order: list[str] = []
        kinds: dict[str, list[str]] = {"attr": [], "unary": [], "link": [], "reified": []}
        deferred: list[tuple] = []
        for i, r in enumerate(c.relationships or []):
            rd = ossie_dict(r)
            order.append(r.name)
            rpath = f"{path}.relationships.{r.name}"
            roles = r.roles or []
            entity_roles = [x for x in roles if self.is_entity(x.concept)]
            if not roles:
                label = split_annotations(r.annotations)[0].get("optional_label") or pascal(r.name)
                optional.append(label)
                kinds["unary"].append(r.name)
                st = {k: v for k, v in rd.items() if k not in ("annotations",)}
                st.pop("name")
                if st.get("verbalizes") == unary_verbalization(c.concept, label):
                    del st["verbalizes"]
                if snake(label) != r.name:
                    st["name"] = r.name
                if split_annotations(r.annotations)[1]:
                    st["annotations"] = split_annotations(r.annotations)[1]
                if st:
                    unary_stash[label] = st
            elif len(roles) == 1 and not entity_roles:
                pid = r.name
                kinds["attr"].append(pid)
                props[pid] = self.attribute(c.concept, c.identify_by, r, rd, rpath)
                if r.multiplicity == "OneToOne":
                    one_to_one.append(pid)
            elif len(entity_roles) == 1:
                kinds["link"].append(r.name)
                deferred.append(("link", i, r, rd, rpath))
            else:
                kinds["reified"].append(r.name)
                deferred.append(("reify", i, r, rd, rpath))

        # Links are built after attributes and unary relationships, so that the link names
        # OGS ➜ Ossie would derive (which avoid names already taken) can be predicted.
        taken = set(kinds["attr"]) | set(kinds["unary"])
        for kind, i, r, rd, rpath in deferred:
            if kind == "link":
                self.link(c.concept, r, rd, rpath, taken)
            else:
                self.reify(c.concept, nid, i, r, rd, rpath)

        if optional:
            labels["optional"] = optional
        if props:
            n["properties"] = props
        if unary_stash:
            self.keep(
                os,
                "unary",
                unary_stash,
                f"{path}.relationships",
                "unary_details",
                "unary relationships became optional labels; their details are kept",
            )
        if self.stash and c.extends and c.extends != implied:
            os["extends"] = c.extends
            os["implied_snapshot"] = implied
        for k in ("derived_by", "requires"):
            v = getattr(c, k)
            self.keep(
                os,
                k,
                v,
                f"{path}.{k}",
                f"concept_{k}",
                f"{k} has no Neo4j equivalent; kept in custom.ossie",
            )
        self.keep(
            os,
            "annotations",
            theirs,
            f"{path}.annotations",
            "annotations",
            "annotations kept in custom.ossie",
        )
        predicted = kinds["attr"] + kinds["unary"] + kinds["link"] + kinds["reified"]
        if self.stash and order != predicted:
            os["relationship_order"] = order
        self.one_to_one[c.concept] = one_to_one

        ext: dict[str, Any] = {}
        if ours.get("indexes"):
            ext["indexes"] = ours["indexes"]
        if ours.get("display"):
            ext["display"] = ours["display"]
        custom = dict(ours.get("custom") or {})
        if os and self.stash:
            custom[OSSIE] = os
        if custom:
            ext["custom"] = custom
        if ext:
            n["extensions"] = ext
        n["_stashed_constraints"] = ours.get("constraints") or {}
        self.nodes[nid] = n

    def keys(self, c) -> None:
        """Turn ``identify_by`` into a KEY constraint, and derive UNIQUE from OneToOne attributes.

        This runs after every node exists, because an identifying attribute may be inherited
        from a supertype. In that case it is copied down to this node so the KEY can cover it.
        """
        nid = self.node_id[c.concept]
        n = self.nodes[nid]
        path = f"ontology.{c.concept}.identify_by"
        props = n.setdefault("properties", {})
        key: list[str] | None = None
        if c.identify_by:
            inherited = self.ancestors_props(c.concept)
            if all(m in props or m in inherited for m in c.identify_by):
                for m in c.identify_by:
                    if m not in props:
                        owner, p = inherited[m]
                        copy = {k: v for k, v in p.items() if k != "extensions"}
                        if self.stash:
                            copy["extensions"] = {"custom": {OSSIE: {"inherited_from": owner}}}
                        props[m] = copy
                key = list(c.identify_by)
            else:
                self.r.add(
                    Loss.APPROXIMATED,
                    "identify_by_not_key",
                    path,
                    "identify_by includes relationships to other concepts, which a Neo4j KEY "
                    "can't cover; no KEY constraint written",
                )
            os = n.setdefault("extensions", {}).setdefault("custom", {}).setdefault(OSSIE, {})
            if self.stash and key != list(c.identify_by):
                os["identify_by"] = c.identify_by
                os["key_snapshot"] = key
        if not props:
            n.pop("properties")
        derived = derive_node_constraints(nid, c.concept, key, self.one_to_one[c.concept])
        stashed = {
            k: Constraint.model_validate(v) for k, v in n.pop("_stashed_constraints").items()
        }
        merged = merge_stashed_constraints(derived, stashed, set(props))
        if merged:
            n["constraints"] = {k: ogs_dict(v) for k, v in merged.items()}
        ext = n.get("extensions")
        if ext is not None:
            if ext.get("custom", {}).get(OSSIE) == {}:
                del ext["custom"][OSSIE]
            if ext.get("custom") == {}:
                del ext["custom"]
            if not ext:
                del n["extensions"]

    def attribute(self, concept: str, identify_by, r, rd: dict, path: str) -> dict:
        ours, theirs = split_annotations(r.annotations)
        role = r.roles[0]
        elem = self.value_type(role.concept, path)
        if r.multiplicity is None:
            t = list_of(elem)
            if t is None:
                self.r.add(
                    Loss.APPROXIMATED,
                    "no_list_type",
                    path,
                    f"Neo4j has no LIST<{elem.value}>; using LIST<STRING>",
                )
                t = Neo4jType.LIST_STRING
        else:
            t = elem
        p: dict[str, Any] = {}
        if not ours.get("untyped"):
            p["type"] = t.value
        for k in ("name", "dimension", "pattern", "one_of", "aliases"):
            if k in ours:
                p[k] = ours[k]
        if r.description:
            p["description"] = r.description
        if r.iri:
            p["reference"] = r.iri
        os = self.relationship_stash(
            r,
            rd,
            theirs,
            path,
            verbal_default=attribute_verbalization(concept, r.name, role.concept),
            roles_default=[{"concept": element_concept(element(t))[0]}],
            # OGS ➜ Ossie derives every multiplicity except a sole identifying ManyToOne.
            keep_multiplicity=list(identify_by or []) == [r.name]
            and r.multiplicity == Multiplicity.MANY_TO_ONE,
        )
        custom = dict(ours.get("custom") or {})
        if os and self.stash:
            custom[OSSIE] = os
        if custom:
            p["extensions"] = {"custom": custom}
        return p

    def relationship_stash(
        self,
        r,
        rd: dict,
        theirs: dict,
        path: str,
        *,
        verbal_default: list[str],
        roles_default: list[dict] | None,
        keep_multiplicity: bool,
    ) -> dict:
        """The Ossie-only parts of a relationship: soft fields that differ from what OGS ➜ Ossie
        would regenerate, plus anything without a native home."""
        os: dict[str, Any] = {}
        if not self.stash:
            return os
        if r.verbalizes != verbal_default:
            os["verbalizes"] = r.verbalizes
        if roles_default is not None and rd.get("roles") != roles_default:
            os["roles"] = rd["roles"]
        if keep_multiplicity and r.multiplicity:
            os["multiplicity"] = r.multiplicity.value
        for k in ("derived_by", "requires"):
            v = getattr(r, k)
            self.keep(
                os,
                k,
                v,
                f"{path}.{k}",
                f"relationship_{k}",
                f"{k} has no Neo4j equivalent; kept in custom.ossie",
            )
        if theirs:
            os["annotations"] = theirs
        return os

    def link(self, src: str, r, rd: dict, path: str, taken: set[str]) -> None:
        ours, theirs = split_annotations(r.annotations)
        roles = r.roles or []
        target_idx = next(i for i, x in enumerate(roles) if self.is_entity(x.concept))
        target = roles[target_idx]
        rid = ours.get("id")
        if rid is None:
            first, second = default_link_ids(src, r.name)
            rid = first if first not in self.rels else unique(second, self.rels)
        rel: dict[str, Any] = {
            "type": ours.get("type") or link_type(src, r.name, r.verbalizes),
            "from": self.end(src, ours, "from"),
            "to": self.end(target.concept, ours, "to"),
        }
        value_roles = [(i, x) for i, x in enumerate(roles) if i != target_idx]
        if not value_roles:
            from_c, to_c = ours.get("from") or {}, ours.get("to") or {}
            mult = r.multiplicity.value if r.multiplicity else None
            if (not from_c and not to_c) or link_multiplicity(from_c, to_c) != mult:
                from_c, to_c = regenerated_counts(mult)  # native wins over stale stashed counts
            rel["from"].update(from_c or {})
            rel["to"].update(to_c or {})
        else:
            rel["from"].update(ours.get("from") or {})
            rel["to"].update(ours.get("to") or {})

        props: dict[str, dict] = {}
        prop_roles: dict[str, dict] = {}
        key_at: dict[int, str] = {target_idx: "target"}
        for i, x in value_roles:
            pid = unique(x.name or snake(x.concept), props)
            key_at[i] = pid
            rpath = f"{path}.roles.{pid}"
            r_ours, _ = split_annotations(x.annotations)
            meta = dict(r_ours.get("property") or {})
            elem = self.value_type(x.concept, rpath)
            if "type" not in meta:
                meta["type"] = elem.value
            elif element(Neo4jType(meta["type"])) != elem:
                meta["type"] = elem.value  # native wins: the role concept changed
            role_st = _strip_neo4j(ossie_dict(x))
            role_default = {
                "concept": element_concept(element(Neo4jType(meta["type"])))[0],
                "name": pid,
            }
            if self.stash and role_st != role_default:
                meta.setdefault("extensions", {}).setdefault("custom", {})[OSSIE] = {
                    "role": role_st
                }
            prop_roles[pid] = role_st
            props[pid] = meta
        if props:
            rel["properties"] = props
        if r.description:
            rel["description"] = r.description
        if ours.get("aliases"):
            rel["aliases"] = ours["aliases"]
        if r.iri:
            rel["reference"] = r.iri
        if ours.get("constraints"):
            rel["constraints"] = ours["constraints"]

        target_st = _strip_neo4j(ossie_dict(target))
        os = self.relationship_stash(
            r,
            rd,
            theirs,
            path,
            verbal_default=link_verbalization(src, rel["type"], target_st, prop_roles),
            roles_default=None,
            keep_multiplicity=bool(value_roles),
        )
        if self.stash:
            if target_st != default_target_role(src, target.concept):
                os["roles"] = {"target": target_st}
            if target_idx != 0:
                os["role_order"] = [key_at[i] for i in range(len(roles))]
            if r.name != predicted_link_name(rel["type"], target.concept, taken):
                os["name"] = r.name
        ext: dict[str, Any] = {}
        if ours.get("indexes"):
            ext["indexes"] = ours["indexes"]
        custom = dict(ours.get("custom") or {})
        if os and self.stash:
            custom[OSSIE] = os
        if custom:
            ext["custom"] = custom
        if ext:
            rel["extensions"] = ext
        self.rels[rid] = rel

    def reify(self, owner: str, owner_nid: str, index: int, r, rd: dict, path: str) -> None:
        """Turn an n-ary relationship into an intermediate node with one relationship per role."""
        label = unique(pascal(owner) + pascal(r.name), set(self.defs) | set(BUILTIN_CONCEPTS))
        nid = unique(kebab(f"{owner}-{r.name}"), set(self.node_id.values()) | set(self.nodes))
        self.r.add(
            Loss.APPROXIMATED,
            "reified_n_ary",
            path,
            f"{len(r.roles or []) + 1}-ary relationship reified as node {label!r}; the original "
            "is kept in custom.ossie and restored on the way back",
        )
        props: dict[str, dict] = {}
        links: list[tuple[str, dict]] = []
        mark = {"custom": {OSSIE: {"reified_link": True}}} if self.stash else None
        rel_id = unique(kebab(f"{owner}-{r.name}"), self.rels)
        links.append(
            (
                rel_id,
                {"type": upper_snake(r.name), "from": {"node": owner_nid}, "to": {"node": nid}},
            )
        )
        for x in r.roles or []:
            key = x.name or snake(x.concept)
            if self.is_entity(x.concept):
                lid = unique(kebab(f"{nid}-{key}"), self.rels)
                links.append(
                    (
                        lid,
                        {
                            "type": upper_snake(key),
                            "from": {"node": nid},
                            "to": self.end(x.concept, {}, "to"),
                        },
                    )
                )
            else:
                pid = unique(key, props)
                props[pid] = {"type": self.value_type(x.concept, f"{path}.roles.{pid}").value}
        node: dict[str, Any] = {"labels": {"identifier": label}}
        if r.description:
            node["description"] = r.description
        if props:
            node["properties"] = props
        if self.stash:
            node["extensions"] = {
                "custom": {OSSIE: {"reified_from": {"owner": owner_nid, "relationship": rd}}}
            }
        node["_stashed_constraints"] = {}
        self.nodes[nid] = node
        for lid, link in links:
            if mark:
                link["extensions"] = mark
            self.rels[lid] = link


def _strip_neo4j(role: dict) -> dict:
    """A role dict without its ``neo4j:*`` annotations (those are restored natively)."""
    out = dict(role)
    _, theirs = split_annotations(out.pop("annotations", None))
    if theirs:
        out["annotations"] = theirs
    return out


def ossie_to_ogs(onto: Ontology, *, stash: bool = True) -> tuple[dict, LossReport]:
    """Convert a normalized Ossie ontology to a longhand OGS document (as a dict)."""
    t = _ToOgs(onto, stash)
    out = t.run()
    for n in out.get("nodes", {}).values():
        n.pop("_stashed_constraints", None)
    return out, t.r
