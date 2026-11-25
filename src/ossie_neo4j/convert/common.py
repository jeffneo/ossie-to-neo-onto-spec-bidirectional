"""Conventions shared by both conversion directions.

**Stashing.** When something has no native home in the target, it is kept in the target's
extension point and restored on the way back:
- Ossie ➜ OGS: in ``extensions.custom.ossie`` (or ``custom.ossie``) of the matching element;
- OGS ➜ Ossie: in ``annotations`` keys prefixed ``neo4j:``.

**Native wins.** A stashed value is used only if the native data it depends on is unchanged. If
it changed, someone edited the document in the target format, and the native data takes
precedence. Some stashes therefore record a snapshot of the native value they were taken
against.

**Defaults are left out.** A ``neo4j:*`` annotation is written only if it differs from what the
reverse direction would derive anyway. Converting a document there and back then does not add
noise.
"""

from __future__ import annotations

import re

from ..ogs.models import Constraint, ConstraintType
from .naming import kebab, upper_snake

OSSIE = "ossie"  # key under OGS custom
NEO4J = "neo4j:"  # prefix of Ossie annotation keys

DEFAULT_OGS_VERSION = "1.0.0"


def ann(key: str) -> str:
    return NEO4J + key


def split_annotations(annotations: dict | None) -> tuple[dict, dict]:
    """Split into ``(neo4j annotations without the prefix, all other annotations)``."""
    ours, theirs = {}, {}
    for k, v in (annotations or {}).items():
        if k.startswith(NEO4J):
            ours[k[len(NEO4J) :]] = v
        else:
            theirs[k] = v
    return ours, theirs


def default_node_id(concept: str) -> str:
    return kebab(concept)


def default_link_ids(concept: str, name: str) -> tuple[str, str]:
    """The ids that Ossie ➜ OGS gives a link: the first if it is free, otherwise the second."""
    return kebab(name), kebab(f"{concept}-{name}")


def key_constraint(owner_id: str, label: str, props: list[str]) -> tuple[str, Constraint]:
    joined = "_".join(props)
    return f"key_{owner_id}_{joined}", Constraint(
        type=ConstraintType.KEY, properties=list(props), name=f"{joined}_{label}_key"
    )


def unique_constraint(owner_id: str, label: str, prop: str) -> tuple[str, Constraint]:
    return f"unique_{owner_id}_{prop}", Constraint(
        type=ConstraintType.UNIQUE, properties=[prop], name=f"{prop}_{label}_uniq"
    )


def derive_node_constraints(
    owner_id: str, label: str, key: list[str] | None, one_to_one: list[str]
) -> dict[str, Constraint]:
    """The constraints that Ossie ➜ OGS derives from ``identify_by`` and OneToOne attributes.

    A sole identifying attribute gets only the KEY, which already implies uniqueness.
    """
    out: dict[str, Constraint] = {}
    if key:
        cid, c = key_constraint(owner_id, label, key)
        out[cid] = c
    for p in one_to_one:
        if key == [p]:
            continue
        cid, c = unique_constraint(owner_id, label, p)
        out[cid] = c
    return out


def merge_stashed_constraints(
    derived: dict[str, Constraint], stashed: dict[str, Constraint], props: set[str]
) -> dict[str, Constraint]:
    """Overlay stashed OGS constraints on the derived ones.

    A stashed constraint with the same type and properties as a derived one replaces it, which
    restores the original id and name. Any other stashed constraint is added, provided all its
    properties still exist (native wins).
    """
    out = dict(derived)
    for sid, s in stashed.items():
        twin = next(
            (did for did, d in out.items() if d.type == s.type and d.properties == s.properties),
            None,
        )
        if twin is not None:
            del out[twin]
            out[sid] = s
        elif all(p in props for p in s.properties):
            out[sid] = s
    return out


_PLACEHOLDER = re.compile(r"\{([^}:]+)(?::[^}]*)?\}")


def verbalization_fits(verbalizes: list[str] | None, concepts: set[str]) -> bool:
    """Whether every ``{Concept}`` or ``{Concept:role}`` placeholder names one of ``concepts``."""
    if not verbalizes:
        return False
    return all(m.group(1) in concepts for v in verbalizes for m in _PLACEHOLDER.finditer(v))


def _at_most_one(c: dict) -> bool:
    return c.get("count") == 1 or (c.get("max_count") is not None and c["max_count"] <= 1)


def link_multiplicity(from_counts: dict, to_counts: dict) -> str | None:
    """The Ossie multiplicity that a binary relationship's end counts imply.

    We read a count on an end as bounding how many of these relationships each node at that end
    takes part in. The spec's example supports this reading: ``to: min_count: 1`` is read as
    "every movie has at least one actor". So ``from`` at most 1 means each source node has at
    most one target, which is ManyToOne. Adding ``to`` at most 1 makes it OneToOne. This
    interpretation is an open question in the README (§6).
    """
    if not _at_most_one(from_counts):
        return None
    return "OneToOne" if _at_most_one(to_counts) else "ManyToOne"


def regenerated_counts(multiplicity: str | None) -> tuple[dict, dict]:
    """The end counts that Ossie ➜ OGS writes for a binary relationship's multiplicity."""
    if multiplicity == "OneToOne":
        return {"max_count": 1}, {"max_count": 1}
    if multiplicity == "ManyToOne":
        return {"max_count": 1}, {}
    return {}, {}


# --- default verbalizations (both directions use these, so defaults are never stashed) -------


def attribute_verbalization(concept: str, prop: str, value_concept: str) -> list[str]:
    from .naming import phrase

    return [f"{{{concept}}} has {phrase(prop)} {{{value_concept}}}"]


def unary_verbalization(concept: str, label: str) -> list[str]:
    from .naming import phrase

    return [f"{{{concept}}} is {phrase(label)}"]


def link_verbalization(src: str, rel_type: str, target_role: dict, prop_roles: dict) -> list[str]:
    from .naming import phrase

    tname = f":{target_role['name']}" if target_role.get("name") else ""
    text = f"{{{src}}} {phrase(rel_type)} {{{target_role['concept']}{tname}}}"
    if prop_roles:
        parts = [
            f"{phrase(pid)} {{{r['concept']}:{r.get('name', pid)}}}"
            for pid, r in prop_roles.items()
        ]
        text += " with " + " and ".join(parts)
    return [text]


def default_target_role(src: str, tgt: str) -> dict:
    return {"concept": tgt, "name": "target"} if src == tgt else {"concept": tgt}


_VERB = re.compile(r"^\{([^}:]+)(?::[^}]*)?\}\s+([^{}]+?)\s+\{")


def link_type(src: str, name: str, verbalizes: list[str]) -> str:
    """A relationship type for a link: the verb phrase of its first verbalization, if any.

    ``{City} is located in {State}`` gives ``IS_LOCATED_IN``, which is much better than
    ``STATE`` from the relationship name. If the first verbalization doesn't start with the
    declaring concept and a phrase, the relationship name is used instead.
    """
    for v in verbalizes[:1]:
        m = _VERB.match(v)
        if m and m.group(1) == src and upper_snake(m.group(2)):
            return upper_snake(m.group(2))
    return upper_snake(name)


def predicted_link_name(rel_type: str, target: str, taken: set[str]) -> str:
    """The relationship name OGS ➜ Ossie gives a link, and marks it taken.

    The name is the snake_case type, or ``<type>_<target>`` if that is already taken, made
    unique. ``taken`` must hold the names already used on the declaring concept, in the order
    OGS ➜ Ossie assigns them: attributes, then unary relationships, then earlier links.
    """
    from .naming import snake, unique

    name = snake(rel_type)
    if name in taken:
        name = f"{name}_{snake(target)}"
    name = unique(name, taken)
    taken.add(name)
    return name
