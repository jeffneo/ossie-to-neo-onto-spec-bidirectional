"""OGS ➜ Ossie ontology.

The input is a *normalized* (longhand) OGS model. The output is a plain dict in the shape of an
Ossie ontology document. See the README, section 3.1, for the mapping rules; the parts that need
care are noted inline.
"""

from __future__ import annotations

from typing import Any

from ..ogs.models import (
    ConstraintType,
    GraphModel,
    Neo4jType,
    Node,
    Property,
    Relationship,
    RelationshipEnd,
)
from ..ogs.models import to_dict as ogs_dict
from ..ossie.models import BUILTIN_CONCEPTS, SPEC_VERSION
from ..ossie.normalize import compact_iri
from .common import (
    DEFAULT_OGS_VERSION,
    OSSIE,
    ann,
    attribute_verbalization,
    default_link_ids,
    default_node_id,
    default_target_role,
    derive_node_constraints,
    link_multiplicity,
    link_type,
    link_verbalization,
    predicted_link_name,
    regenerated_counts,
    split_annotations,
    unary_verbalization,
    verbalization_fits,
)
from .naming import kebab, pascal, snake, unique
from .report import Loss, LossReport
from .semantic import tables_to_semantic_model
from .types import element, element_concept, resolve


def _custom(obj: Any) -> dict:
    """The ``custom`` map of an OGS element, wherever that element keeps it."""
    ext = getattr(obj, "extensions", None)
    return dict((ext.custom if ext is not None else None) or {})


def _ossie(obj: Any) -> dict:
    return _custom(obj).get(OSSIE) or {}


def _without_ossie(custom: dict) -> dict:
    return {k: v for k, v in custom.items() if k != OSSIE}


def _counts(end: RelationshipEnd) -> dict:
    return {k: v for k in ("count", "min_count", "max_count") if (v := getattr(end, k)) is not None}


ANY_NODE = "Neo4jNode"  # stub source concept for relationships with an unconstrained source


class _ToOssie:
    def __init__(self, model: GraphModel, stash: bool):
        self.m = model
        self.stash = stash
        self.r = LossReport("ogs->ossie")
        self.nodes = model.nodes or {}
        self.rels = model.relationships or {}
        self.root_os = _ossie(model)
        self.value_types = {vt["concept"]: vt for vt in self.root_os.get("value_types", [])}
        self.generated: dict[str, dict] = {}
        self.stubs: dict[str, dict] = {}
        self._assign_concepts()

    # --- helpers ---------------------------------------------------------------------------

    def put(self, target: dict, key: str, value: Any, path: str) -> None:
        """Stash an OGS-only value as an annotation (or report it dropped if stashing is off)."""
        if value is None or value == {} or value == []:
            return
        if not self.stash:
            self.r.add(Loss.DROPPED, f"neo4j_{key}", path, f"{key} has no Ossie equivalent")
            return
        target.setdefault("annotations", {})[ann(key)] = value
        self.r.add(Loss.STASHED, f"neo4j_{key}", path, f"{key} kept as annotation neo4j:{key}")

    def use(self, generated: dict | None) -> None:
        if generated is None:
            return
        if not self.stash:
            # The generated name alone identifies the Neo4j type (see types.ALL_GENERATED).
            generated = {k: v for k, v in generated.items() if k != "annotations"}
        self.generated.setdefault(generated["concept"], generated)

    def resolves_to(self, concept: str, t: Neo4jType) -> bool:
        return resolve(concept, self.value_types)[0] == t

    def _assign_concepts(self) -> None:
        self.reified = {
            nid
            for nid, n in self.nodes.items()
            if (_ossie(n).get("reified_from") or {}).get("owner") in self.nodes
        }
        self.concept_of: dict[str, str] = {}
        taken = set(BUILTIN_CONCEPTS)
        for nid, node in self.nodes.items():
            if nid in self.reified:
                continue
            label = node.labels.identifier if node.labels else None
            path = f"nodes.{nid}"
            if not label:
                label = pascal(nid)
                self.r.add(
                    Loss.APPROXIMATED,
                    "missing_identifier_label",
                    path,
                    f"node has no identifying label; concept named {label!r} from its id",
                )
            if label in taken:
                new = unique(label, taken)
                self.r.add(
                    Loss.APPROXIMATED,
                    "duplicate_label",
                    path,
                    f"label {label!r} is already a concept name; renamed to {new!r}",
                )
                label = new
            taken.add(label)
            self.concept_of[nid] = label
        self.node_by_label = {
            n.labels.identifier: nid
            for nid, n in self.nodes.items()
            if n.labels and n.labels.identifier and nid in self.concept_of
        }

    def label_concept(self, label: str, path: str) -> str:
        """The concept for a label: a node's concept if one has that label, else a stub."""
        if label in self.node_by_label:
            return self.concept_of[self.node_by_label[label]]
        if label not in self.stubs:
            stub = {"concept": label, "type": "EntityType"}
            self.put(stub, "stub", True, path)
            self.stubs[label] = stub
        return label

    def end_concept(self, end: RelationshipEnd, path: str) -> str | None:
        if end.node is not None:
            if end.node in self.concept_of:
                return self.concept_of[end.node]
            label = self.nodes[end.node].labels.identifier if self.nodes[end.node].labels else None
            return self.label_concept(label or pascal(end.node), path)
        if end.label is not None:
            if end.label not in self.node_by_label:
                self.r.add(
                    Loss.APPROXIMATED,
                    "label_only_end",
                    path,
                    f"end names label {end.label!r}, which no node has; using a stub concept",
                )
            return self.label_concept(end.label, path)
        return None

    # --- build ---------------------------------------------------------------------------------

    def _group_links(self) -> None:
        """Assign every link to the concept it is declared on: its source end's concept."""
        self.links_by_src: dict[str, list[str]] = {}
        for rid, rel in self.rels.items():
            if _ossie(rel).get("reified_link"):
                continue
            src = self.end_concept(rel.from_, f"relationships.{rid}.from")
            if src is None:
                src = ANY_NODE
                if src not in self.stubs:
                    stub = {"concept": src, "type": "EntityType"}
                    self.put(stub, "stub", True, f"relationships.{rid}.from")
                    self.put(stub, "unlabelled", True, f"relationships.{rid}.from")
                    self.stubs[src] = stub
                self.r.add(
                    Loss.APPROXIMATED,
                    "unconstrained_source",
                    f"relationships.{rid}.from",
                    f"relationship has no source node or label; declared on stub {src!r}",
                )
            self.links_by_src.setdefault(src, []).append(rid)

    def run(self) -> dict:
        self._group_links()
        concepts = [self.concept(nid, n) for nid, n in self.nodes.items() if nid in self.concept_of]
        for label, stub in list(self.stubs.items()):
            names: set[str] = set()
            rels = [
                self.link(rid, self.rels[rid], label, names)
                for rid in self.links_by_src.get(label, [])
            ]
            if rels:
                stub["relationships"] = rels
        all_concepts = (
            list(self.generated.values())
            + concepts
            + list(self.stubs.values())
            + list(self.value_types.values())
        )
        order = self.root_os.get("concept_order")
        if order:
            pos = {name: i for i, name in enumerate(order)}
            all_concepts.sort(key=lambda c: pos.get(c["concept"], len(pos)))

        out: dict[str, Any] = {"version": SPEC_VERSION, "name": self.m.name or self.m.id}
        if self.m.description:
            out["description"] = self.m.description
        for k in ("ai_context", "requires"):
            if k in self.root_os:
                out[k] = self.root_os[k]
        out["ontology"] = all_concepts
        if "ontology_mappings" in self.root_os:
            out["ontology_mappings"] = self.root_os["ontology_mappings"]
        if self.root_os.get("annotations"):
            out["annotations"] = dict(self.root_os["annotations"])
        if self.m.name is None:
            self.put(out, "unnamed", True, "name")
        if self.m.id != kebab(out["name"]):
            self.put(out, "id", self.m.id, "id")
        if self.m.version != DEFAULT_OGS_VERSION:
            self.put(out, "version", self.m.version, "version")
        self.put(out, "custom", _without_ossie(_custom(self.m)), "extensions.custom")
        ext = self.m.extensions
        if ext is not None:
            # Tables become the semantic model (convert/semantic.py). Mappings become the
            # mapping document in phase 3; until then they ride along in annotations.
            if ext.data_source_mappings:
                self.put(
                    out,
                    "data_source_mappings",
                    {k: [ogs_dict(x) for x in v] for k, v in ext.data_source_mappings.items()},
                    "extensions.data_source_mappings",
                )
        if self.root_os.get("prefixes"):
            out["prefixes"] = self.root_os["prefixes"]
            _compact_iris(out)
        return out

    def concept(self, nid: str, node: Node) -> dict:
        label = self.concept_of[nid]
        path = f"nodes.{nid}"
        os = _ossie(node)
        c: dict[str, Any] = {"concept": label, "type": "EntityType"}
        if node.description:
            c["description"] = node.description

        implied = list((node.labels.implied if node.labels else None) or [])
        implied_concepts = [self.label_concept(lb, f"{path}.labels.implied") for lb in implied]
        if "extends" in os and set(os.get("implied_snapshot") or []) == set(implied):
            c["extends"] = os["extends"]
        elif implied_concepts:
            c["extends"] = implied_concepts

        constraints = node.constraints or {}
        keys = [k for k in constraints.values() if k.type == ConstraintType.KEY]
        key_props = list(keys[0].properties) if keys else None
        if "identify_by" in os and os.get("key_snapshot") == key_props:
            c["identify_by"] = os["identify_by"]
        elif key_props:
            c["identify_by"] = key_props

        if node.reference:
            c["iri"] = node.reference
        for k in ("derived_by", "requires"):
            if k in os:
                c[k] = os[k]
        if os.get("annotations"):
            c["annotations"] = dict(os["annotations"])

        if nid != default_node_id(label):
            self.put(c, "node_id", nid, path)
        self.put(c, "aliases", node.aliases, f"{path}.aliases")
        labels_ext = node.labels.extensions if node.labels else None
        self.put(c, "labels_custom", labels_ext.custom if labels_ext else None, f"{path}.labels")
        if node.extensions is not None:
            self.put(
                c,
                "indexes",
                {k: ogs_dict(v) for k, v in (node.extensions.indexes or {}).items()},
                f"{path}.extensions.indexes",
            )
            display = node.extensions.display
            self.put(
                c, "display", ogs_dict(display) if display else None, f"{path}.extensions.display"
            )
            self.put(c, "custom", _without_ossie(_custom(node)), f"{path}.extensions.custom")

        # Relationships declared on this concept: properties, then optional labels as unary
        # relationships, then links whose source is this node, then reified relationships.
        rels: list[dict] = []
        names: set[str] = set()
        one_to_one: list[str] = []
        props = node.properties or {}
        for pid, p in props.items():
            if _ossie(p).get("inherited_from"):
                continue  # a copy made only to carry an inherited key; the supertype declares it
            d = self.attribute(label, pid, p, constraints, key_props, f"{path}.properties.{pid}")
            names.add(pid)
            if d.get("multiplicity") == "OneToOne":
                one_to_one.append(pid)
            rels.append(d)
        for opt in (node.labels.optional if node.labels else None) or []:
            rels.append(self.unary(label, opt, os, names, f"{path}.labels.optional"))
        for rid in self.links_by_src.get(label, []):
            rels.append(self.link(rid, self.rels[rid], label, names))
        for rnid in self.reified:
            st = _ossie(self.nodes[rnid])["reified_from"]
            if st["owner"] == nid:
                names.add(st["relationship"]["name"])
                rels.append(st["relationship"])
        order = os.get("relationship_order")
        if order:
            pos = {n: i for i, n in enumerate(order)}
            rels.sort(key=lambda d: pos.get(d["name"], len(pos)))
        if rels:
            c["relationships"] = rels

        # Stash only the constraints that Ossie ➜ OGS would not derive identically.
        ident = c.get("identify_by")
        derived_key = ident if ident and all(m in props for m in ident) else None
        derived = derive_node_constraints(nid, label, derived_key, one_to_one)
        extra = {cid: ogs_dict(k) for cid, k in constraints.items() if derived.get(cid) != k}
        self.put(c, "constraints", extra, f"{path}.constraints")
        return c

    def attribute(
        self,
        label: str,
        pid: str,
        p: Property,
        constraints: dict,
        key_props: list[str] | None,
        path: str,
    ) -> dict:
        os = _ossie(p)
        elem = element(p.type)
        concept, gen = element_concept(elem)
        role: dict[str, Any] = {"concept": concept}
        st_roles = os.get("roles") or []
        if len(st_roles) == 1 and self.resolves_to(st_roles[0]["concept"], elem):
            role = dict(st_roles[0])  # the original Ossie value concept, e.g. RunwayLength
        else:
            self.use(gen)

        many = p.type is not None and p.type.is_list
        unique_single = any(
            k.type == ConstraintType.UNIQUE and k.properties == [pid] for k in constraints.values()
        )
        if many:
            mult = None
        elif unique_single:
            mult = "OneToOne"
        elif key_props == [pid]:
            mult = os.get("multiplicity") if os.get("multiplicity") else "OneToOne"
        else:
            mult = "ManyToOne"

        d: dict[str, Any] = {"name": pid}
        if p.description:
            d["description"] = p.description
        d["roles"] = [role]
        if mult:
            d["multiplicity"] = mult
        verbal = os.get("verbalizes")
        d["verbalizes"] = (
            verbal
            if verbalization_fits(verbal, {label, role["concept"]})
            else attribute_verbalization(label, pid, role["concept"])
        )
        if p.reference:
            d["iri"] = p.reference
        for k in ("derived_by", "requires"):
            if k in os:
                d[k] = os[k]
        if os.get("annotations"):
            d["annotations"] = dict(os["annotations"])
        if p.type is None:
            self.put(d, "untyped", True, path)
        self.put(d, "name", p.name, f"{path}.name")
        self.put(d, "aliases", p.aliases, f"{path}.aliases")
        self.put(d, "dimension", p.dimension, f"{path}.dimension")
        self.put(d, "pattern", p.pattern, f"{path}.pattern")
        self.put(d, "one_of", p.one_of, f"{path}.one_of")
        self.put(d, "custom", _without_ossie(_custom(p)), f"{path}.extensions.custom")
        return d

    def unary(self, label: str, opt: str, node_os: dict, names: set[str], path: str) -> dict:
        st = (node_os.get("unary") or {}).get(opt, {})
        name = unique(st.get("name") or snake(opt), names)
        names.add(name)
        d: dict[str, Any] = {"name": name}
        if "description" in st:
            d["description"] = st["description"]
        verbal = st.get("verbalizes")
        d["verbalizes"] = (
            verbal if verbalization_fits(verbal, {label}) else unary_verbalization(label, opt)
        )
        for k in ("iri", "derived_by", "requires"):
            if k in st:
                d[k] = st[k]
        if st.get("annotations"):
            d["annotations"] = dict(st["annotations"])
        if pascal(name) != opt:
            self.put(d, "optional_label", opt, path)
        return d

    def link(self, rid: str, rel: Relationship, src: str, names: set[str]) -> dict:
        path = f"relationships.{rid}"
        os = _ossie(rel)
        tgt = self.end_concept(rel.to, f"{path}.to") or "Any"
        props = rel.properties or {}

        if os.get("name"):
            name = unique(os["name"], names)
            names.add(name)
        else:
            name = predicted_link_name(rel.type, tgt, names)

        st_roles = os.get("roles") or {}
        target_role: dict[str, Any] = default_target_role(src, tgt)
        if "target" in st_roles and st_roles["target"].get("concept") == tgt:
            target_role = dict(st_roles["target"])

        prop_roles: dict[str, dict] = {}
        for pid, p in props.items():
            ppath = f"{path}.properties.{pid}"
            elem = element(p.type)
            concept, gen = element_concept(elem)
            role: dict[str, Any] = {"concept": concept, "name": pid}
            st = _ossie(p).get("role")
            if st and self.resolves_to(st["concept"], elem):
                role = dict(st)
            else:
                self.use(gen)
            meta = {k: v for k, v in ogs_dict(p).items() if k != "extensions"}
            custom = _without_ossie(_custom(p))
            if custom:
                meta["extensions"] = {"custom": custom}
            canonical = p.type is not None and not p.type.is_list
            if canonical and resolve(role["concept"], self.value_types)[0] == p.type:
                meta.pop("type", None)
            if p.type is not None and p.type.is_list:
                self.r.add(
                    Loss.APPROXIMATED,
                    "list_in_role",
                    ppath,
                    "a list-valued relationship property becomes a single-valued role in Ossie; "
                    "the exact type is kept in neo4j:property",
                )
            self.put(role, "property", meta, ppath)
            prop_roles[pid] = role

        order = os.get("role_order") or ["target", *props]
        keys = [k for k in order if k == "target" or k in prop_roles]
        keys += [k for k in prop_roles if k not in keys]
        if "target" not in keys:
            keys.insert(0, "target")
        roles = [target_role if k == "target" else prop_roles[k] for k in keys]

        from_c, to_c = _counts(rel.from_), _counts(rel.to)
        if props:
            mult = os.get("multiplicity")
            regen: tuple[dict, dict] = ({}, {})
        else:
            mult = link_multiplicity(from_c, to_c)
            regen = regenerated_counts(mult)

        d: dict[str, Any] = {"name": name}
        if rel.description:
            d["description"] = rel.description
        d["roles"] = roles
        if mult:
            d["multiplicity"] = mult
        verbal = os.get("verbalizes")
        role_concepts = {src} | {r["concept"] for r in roles}
        if verbalization_fits(verbal, role_concepts):
            d["verbalizes"] = verbal
        else:
            d["verbalizes"] = link_verbalization(src, rel.type, target_role, prop_roles)
        if rel.reference:
            d["iri"] = rel.reference
        for k in ("derived_by", "requires"):
            if k in os:
                d[k] = os[k]
        if os.get("annotations"):
            d["annotations"] = dict(os["annotations"])

        if rid not in default_link_ids(src, name):
            self.put(d, "id", rid, path)
        if rel.type != link_type(src, name, d["verbalizes"]):
            self.put(d, "type", rel.type, f"{path}.type")
        if (from_c, to_c) != regen:
            self.put(d, "from", from_c, f"{path}.from")
            self.put(d, "to", to_c, f"{path}.to")
            if not props and (from_c or to_c):
                self.r.add(
                    Loss.APPROXIMATED,
                    "counts_beyond_multiplicity",
                    path,
                    "Ossie multiplicity can't express these end counts exactly; kept in "
                    "neo4j:from / neo4j:to",
                )
        # An end written by label (not node id) that matches a node would come back as a node
        # reference. Record that it was label-only.
        for end_name, end in (("from", rel.from_), ("to", rel.to)):
            if end.node is None and end.label is not None and end.label in self.node_by_label:
                self.put(d, f"{end_name}_label_only", True, f"{path}.{end_name}")
        self.put(d, "aliases", rel.aliases, f"{path}.aliases")
        if rel.extensions is not None:
            self.put(
                d,
                "indexes",
                {k: ogs_dict(v) for k, v in (rel.extensions.indexes or {}).items()},
                f"{path}.extensions.indexes",
            )
            self.put(d, "custom", _without_ossie(_custom(rel)), f"{path}.extensions.custom")
        self.put(
            d,
            "constraints",
            {k: ogs_dict(v) for k, v in (rel.constraints or {}).items()},
            f"{path}.constraints",
        )
        return d


def _compact_iris(doc: dict) -> None:
    prefixes = doc["prefixes"]
    for c in doc["ontology"]:
        if "iri" in c:
            c["iri"] = compact_iri(c["iri"], prefixes)
        for r in c.get("relationships") or []:
            if "iri" in r:
                r["iri"] = compact_iri(r["iri"], prefixes)


def ogs_to_ossie(
    model: GraphModel, *, stash: bool = True
) -> tuple[dict | None, dict | None, LossReport]:
    """Convert a normalized OGS model to Ossie documents (as dicts).

    Returns ``(ontology, semantic_model, report)``:
    - the ontology is None if the model has no nodes;
    - the semantic model is None if the model has no ``data_source_tables``.

    When there is no ontology, model-level OGS metadata such as ``id``, ``version`` and
    ``custom`` rides on the semantic model's NEO4J extension instead.
    """
    t = _ToOssie(model, stash)
    onto = t.run()
    root_neo4j: dict = {}
    if not onto["ontology"]:
        ours, _ = split_annotations(onto.get("annotations"))
        root_neo4j = ours
        onto = None
    sm = tables_to_semantic_model(
        model,
        name=model.name or model.id,
        description=model.description,
        root_neo4j=root_neo4j,
        stash=stash,
        report=t.r,
    )
    return onto, sm, t.r
