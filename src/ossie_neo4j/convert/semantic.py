"""The physical layer: OGS ``extensions.data_source_tables`` ⇄ an Ossie semantic model.

| OGS                                | Ossie semantic model                                     |
|------------------------------------|----------------------------------------------------------|
| table id, ``source``               | dataset ``name``, ``source``                             |
| column                             | field: ``name`` = column name, ``expression.sql`` = the  |
|                                    | column as an SQL identifier                              |
| column ``suggested`` Neo4j type    | field ``datatype`` (via ``types.DATATYPE_FOR``)          |
| ``primary_keys`` (column names)    | ``primary_key`` (field names)                            |
| foreign key on a table             | relationship: ``from`` = that table (the many side),     |
|                                    | ``to`` = the referenced table                            |

Where things are kept that have no native home:
- OGS-only column, table and foreign-key details (source ``type``, ``size``, ``nullable``,
  ``supported``, ``custom``) go in ``nonportable_extensions`` with ``vendor_name: NEO4J``.
- Ossie-only data goes in ``custom.ossie`` of the matching table, column or foreign key. That
  covers descriptions, ``ai_context``, labels, dimensions, unique keys and metrics. It also
  covers fields whose expression is not a plain column reference ("derived fields").

As in the ontology layer, defaults are never stashed. Stashed values are used only while the
native data they depend on is unchanged.
"""

from __future__ import annotations

from typing import Any

from ..ogs.models import GraphModel, Table
from ..ogs.models import to_dict as ogs_dict
from ..ossie.models import SPEC_VERSION, SemanticModel
from ..ossie.models import to_dict as ossie_dict
from .common import OSSIE
from .naming import parse_identifier, sql_identifier, unique
from .report import Loss, LossReport
from .types import NEO4J_FOR_DATATYPE, datatype_for, datatype_from_source

VENDOR = "NEO4J"


def _fold(s: str) -> str:
    return s.casefold()


def split_nonportable(exts: list | None) -> tuple[dict, list]:
    """Split into ``(our NEO4J data, every other vendor's extensions)``."""
    ours: dict = {}
    theirs: list = []
    for e in exts or []:
        e = e if isinstance(e, dict) else ossie_dict(e)
        if e["vendor_name"] == VENDOR:
            ours.update(e["data"])
        else:
            theirs.append(e)
    return ours, theirs


def _without_ossie(custom: dict | None) -> dict:
    return {k: v for k, v in (custom or {}).items() if k != OSSIE}


def _derived_datatype(suggested: str | None, source_type: str | None) -> str | None:
    """The field datatype OGS ➜ Ossie derives from a column."""
    return datatype_for(suggested) if suggested else datatype_from_source(source_type)


def _default_suggested(datatype: str | None) -> str | None:
    t = NEO4J_FOR_DATATYPE.get(datatype) if datatype else None
    return t.value if t else None


# --- OGS ➜ Ossie -----------------------------------------------------------------------------


class _TablesToOssie:
    def __init__(self, model: GraphModel, stash: bool, report: LossReport):
        self.m = model
        self.stash = stash
        self.r = report
        self.tables: dict[str, Table] = model.extensions.data_source_tables or {}
        custom = model.extensions.custom or {}
        self.root_os: dict = (custom.get(OSSIE) or {}).get("semantic_model") or {}
        self._pending_fk_data: dict[str, tuple[dict, dict]] = {}

    def vendor(self, target: dict, data: dict, path: str, code: str, what: str) -> None:
        """Attach our NEO4J nonportable extension to ``target`` (or report it dropped)."""
        if not data:
            return
        if not self.stash:
            self.r.add(Loss.DROPPED, code, path, f"{what} has no Ossie equivalent")
            return
        target.setdefault("nonportable_extensions", []).append(
            {"vendor_name": VENDOR, "data": data}
        )
        self.r.add(Loss.STASHED, code, path, f"{what} kept in nonportable_extensions[NEO4J]")

    def run(self, name: str, description: str | None, root_neo4j: dict) -> dict:
        datasets: list[dict] = []
        self.dataset_name: dict[str, str] = {}
        self.field_of: dict[str, dict[str, str]] = {}  # table id ➜ column name ➜ field name
        taken: set[str] = set()
        for tid, t in self.tables.items():
            dname = tid
            if _fold(dname) in taken:
                dname = unique(dname, {d for d in self.dataset_name.values()} | taken)
                self.r.add(
                    Loss.APPROXIMATED,
                    "duplicate_dataset_name",
                    f"extensions.data_source_tables.{tid}",
                    f"table id {tid!r} clashes case-insensitively; dataset renamed {dname!r}",
                )
            taken.add(_fold(dname))
            self.dataset_name[tid] = dname
            datasets.append(self.dataset(tid, t, dname))

        relationships = self.relationships(datasets)

        sm: dict[str, Any] = {"version": SPEC_VERSION}
        sm["name"] = self.root_os["name"] if "name" in self.root_os else name
        desc = self.root_os["description"] if "description" in self.root_os else description
        if desc is not None:
            sm["description"] = desc
        if "ai_context" in self.root_os:
            sm["ai_context"] = self.root_os["ai_context"]
        sm["datasets"] = datasets
        if relationships:
            sm["relationships"] = relationships
        if self.root_os.get("metrics"):
            sm["metrics"] = self.root_os["metrics"]
        if self.root_os.get("nonportable_extensions"):
            sm["nonportable_extensions"] = list(self.root_os["nonportable_extensions"])
        self.vendor(sm, root_neo4j, "", "neo4j_model", "OGS model metadata")
        return sm

    def dataset(self, tid: str, t: Table, dname: str) -> dict:
        path = f"extensions.data_source_tables.{tid}"
        tos = (t.custom or {}).get(OSSIE) or {}
        d: dict[str, Any] = {"name": dname, "source": t.source}
        if "description" in tos:
            d["description"] = tos["description"]

        fields: list[dict] = []
        col_field: dict[str, str] = {}
        names: set[str] = set()
        for i, col in enumerate(t.columns or []):
            cpath = f"{path}.columns.{i}"
            cos = (col.custom or {}).get(OSSIE) or {}
            cname = col.name or f"column_{i + 1}"
            fname = cos.get("name", cname)
            if _fold(fname) in names:
                new = unique(fname, names)
                self.r.add(
                    Loss.APPROXIMATED,
                    "duplicate_field_name",
                    cpath,
                    f"column {fname!r} clashes case-insensitively; field renamed {new!r}",
                )
                fname = new
            names.add(_fold(fname))
            col_field[cname] = fname
            suggested = col.suggested.value if col.suggested else None

            f: dict[str, Any] = {"name": fname, "expression": {"sql": sql_identifier(cname)}}
            for k in ("dimension", "label", "description"):
                if k in cos:
                    f[k] = cos[k]
            derived = _derived_datatype(suggested, col.type)
            if "datatype" in cos and (
                cos["datatype"] is None or _default_suggested(cos["datatype"]) == suggested
            ):
                datatype = cos["datatype"]
            else:
                datatype = derived
            if datatype is not None:
                f["datatype"] = datatype
            if "ai_context" in cos:
                f["ai_context"] = cos["ai_context"]
            if cos.get("nonportable_extensions"):
                f["nonportable_extensions"] = list(cos["nonportable_extensions"])

            data: dict[str, Any] = {}
            for k in ("type", "size", "nullable", "dimension"):
                v = getattr(col, k)
                if v is not None:
                    data[k] = v
            if col.supported:
                data["supported"] = [s.value for s in col.supported]
            if suggested != _default_suggested(datatype):
                data["suggested"] = suggested  # None records "no suggested type"
            if not col.name:
                data["unnamed"] = True
            if _without_ossie(col.custom):
                data["custom"] = _without_ossie(col.custom)
            self.vendor(f, data, cpath, "neo4j_column", "column details")
            fields.append(f)

        for v in tos.get("derived_fields") or []:
            fields.insert(min(v["index"], len(fields)), v["field"])
            names.add(_fold(v["field"]["name"]))
        if fields:
            d["fields"] = fields
        self.field_of[tid] = col_field

        pk_cols = list(t.primary_keys or [])
        data: dict[str, Any] = {}
        if "primary_key" in tos and tos.get("primary_keys_snapshot") == pk_cols:
            d["primary_key"] = tos["primary_key"]
        elif pk_cols:
            if all(c in col_field for c in pk_cols):
                d["primary_key"] = [col_field[c] for c in pk_cols]
            else:
                data["primary_keys"] = pk_cols
                self.r.add(
                    Loss.APPROXIMATED,
                    "primary_key_not_in_columns",
                    f"{path}.primary_keys",
                    "primary key names columns the table doesn't list; kept in NEO4J data",
                )
        for k in ("unique_keys", "ai_context", "metrics"):
            if k in tos:
                d[k] = tos[k]
        if tos.get("nonportable_extensions"):
            d["nonportable_extensions"] = list(tos["nonportable_extensions"])
        if dname != tid:
            data["table_id"] = tid
        if _without_ossie(t.custom):
            data["custom"] = _without_ossie(t.custom)
        self._pending_fk_data[tid] = (d, data)
        return d

    def relationships(self, datasets: list[dict]) -> list[dict]:
        rels: list[dict] = []
        taken: set[str] = set()
        for tid, t in self.tables.items():
            d, data = self._pending_fk_data[tid]
            for fid, fk in (t.foreign_keys or {}).items():
                path = f"extensions.data_source_tables.{tid}.foreign_keys.{fid}"
                fos = (fk.custom or {}).get(OSSIE) or {}
                ref = self.tables.get(fk.references.table)
                to_cols = fk.references.columns or (ref.primary_keys if ref else None)
                from_map = self.field_of[tid]
                to_map = self.field_of.get(fk.references.table, {})
                ok = (
                    ref is not None
                    and to_cols
                    and len(to_cols) == len(fk.columns)
                    and all(c in from_map for c in fk.columns)
                    and all(c in to_map for c in to_cols)
                )
                if not ok:
                    data.setdefault("foreign_keys", {})[fid] = ogs_dict(fk)
                    self.r.add(
                        Loss.APPROXIMATED,
                        "foreign_key_not_representable",
                        path,
                        "foreign key refers to an unknown table or undeclared columns; "
                        "kept in NEO4J data",
                    )
                    continue
                name = fos.get("name", fid)
                if _fold(name) in taken:
                    name = unique(f"{tid}_{fid}", taken)
                taken.add(_fold(name))
                spelling = fos.get("spelling") or {}
                rel: dict[str, Any] = {
                    "name": name,
                    "from": spelling.get("from", self.dataset_name[tid]),
                    "to": spelling.get("to", self.dataset_name[fk.references.table]),
                    "from_columns": spelling.get("from_columns", [from_map[c] for c in fk.columns]),
                    "to_columns": spelling.get("to_columns", [to_map[c] for c in to_cols]),
                }
                if "ai_context" in fos:
                    rel["ai_context"] = fos["ai_context"]
                if fos.get("nonportable_extensions"):
                    rel["nonportable_extensions"] = list(fos["nonportable_extensions"])
                rdata: dict[str, Any] = {}
                if name != fid:
                    rdata["id"] = fid
                if not fk.references.columns:
                    rdata["implicit_columns"] = True
                if _without_ossie(fk.custom):
                    rdata["custom"] = _without_ossie(fk.custom)
                if fk.references.custom:
                    rdata["references_custom"] = fk.references.custom
                self.vendor(rel, rdata, path, "neo4j_foreign_key", "foreign key details")
                rels.append(rel)
            self.vendor(
                d, data, f"extensions.data_source_tables.{tid}", "neo4j_table", "table details"
            )

        for v in self.root_os.get("unmapped_relationships") or []:
            rels.insert(min(v["index"], len(rels)), v["relationship"])
        order = self.root_os.get("relationship_order")
        if order:
            pos = {n: i for i, n in enumerate(order)}
            rels.sort(key=lambda r: pos.get(r["name"], len(pos)))
        return rels


def tables_to_semantic_model(
    model: GraphModel,
    *,
    name: str,
    description: str | None,
    root_neo4j: dict,
    stash: bool,
    report: LossReport,
) -> dict | None:
    """OGS tables ➜ an Ossie semantic model dict, or None if the model has no tables."""
    if model.extensions is None or not model.extensions.data_source_tables:
        return None
    return _TablesToOssie(model, stash, report).run(name, description, root_neo4j)


# --- Ossie ➜ OGS -----------------------------------------------------------------------------


class _SemanticToTables:
    def __init__(self, sm: SemanticModel, stash: bool, report: LossReport):
        self.sm = sm
        self.stash = stash
        self.r = report

    def keep(self, target: dict, key: str, value: Any, path: str, code: str, why: str) -> None:
        if value is None or value == [] or value == {}:
            return
        if not self.stash:
            self.r.add(Loss.DROPPED, code, path, why)
            return
        target[key] = value
        self.r.add(Loss.STASHED, code, path, why)

    def run(self, model_name: str | None, model_description: str | None) -> tuple[dict, dict, dict]:
        """Returns ``(data_source_tables, semantic model stash, root NEO4J data)``."""
        self.table_of: dict[str, str] = {}  # folded dataset name ➜ table id
        self.col_of: dict[str, dict[str, str]] = {}  # table id ➜ folded field name ➜ column
        tables: dict[str, dict] = {}
        for ds in self.sm.datasets:
            tid, table = self.table(ds)
            tables[tid] = table

        root: dict[str, Any] = {}
        unmapped: list[dict] = []
        emitted: list[str] = []
        per_table: dict[str, list[str]] = {}
        for i, r in enumerate(self.sm.relationships or []):
            rd = ossie_dict(r)
            path = f"relationships.{r.name}"
            from_tid = self.table_of.get(_fold(r.from_))
            to_tid = self.table_of.get(_fold(r.to))
            from_cols = [self.col_of.get(from_tid, {}).get(_fold(c)) for c in r.from_columns]
            to_cols = [self.col_of.get(to_tid, {}).get(_fold(c)) for c in r.to_columns]
            if from_tid is None or to_tid is None or None in from_cols or None in to_cols:
                unmapped.append({"index": i, "relationship": rd})
                self.r.add(
                    Loss.STASHED if self.stash else Loss.DROPPED,
                    "relationship_not_foreign_key",
                    path,
                    "relationship uses derived fields, so it can't be an OGS foreign key",
                )
                continue
            ours, theirs = split_nonportable(rd.get("nonportable_extensions"))
            fid = ours.get("id") or r.name
            fk: dict[str, Any] = {
                "columns": from_cols,
                "references": {"table": to_tid, "columns": to_cols},
            }
            if ours.get("implicit_columns") and to_cols == tables[to_tid].get("primary_keys"):
                del fk["references"]["columns"]
            if ours.get("references_custom"):
                fk["references"]["custom"] = ours["references_custom"]
            fos: dict[str, Any] = {}
            if r.ai_context is not None:
                self.keep(
                    fos,
                    "ai_context",
                    rd["ai_context"],
                    path,
                    "relationship_ai_context",
                    "Neo4j foreign keys have no AI context; kept in custom.ossie",
                )
            self.keep(
                fos,
                "nonportable_extensions",
                theirs,
                path,
                "nonportable_extensions",
                "other vendors' extensions kept in custom.ossie",
            )
            spelled = {
                "from": tables[from_tid]["_dataset_name"],
                "to": tables[to_tid]["_dataset_name"],
                "from_columns": [tables[from_tid]["_field_name"][_fold(c)] for c in r.from_columns],
                "to_columns": [tables[to_tid]["_field_name"][_fold(c)] for c in r.to_columns],
            }
            actual = {
                "from": r.from_,
                "to": r.to,
                "from_columns": r.from_columns,
                "to_columns": r.to_columns,
            }
            spelling = {k: v for k, v in actual.items() if v != spelled[k]}
            if spelling and self.stash:
                fos["spelling"] = spelling
            if r.name != fid and self.stash:
                fos["name"] = r.name
            custom = dict(ours.get("custom") or {})
            if fos and self.stash:
                custom[OSSIE] = fos
            if custom:
                fk["custom"] = custom
            fks = tables[from_tid].setdefault("foreign_keys", {})
            fks[unique(fid, fks)] = fk
            per_table.setdefault(from_tid, []).append(r.name)
            emitted.append(r.name)

        # Relationships come back table by table; record the real order if that differs.
        predicted = [n for tid in tables for n in per_table.get(tid, [])]
        if self.stash and emitted != predicted:
            root["relationship_order"] = [r.name for r in self.sm.relationships or []]
        self.keep(
            root,
            "unmapped_relationships",
            unmapped,
            "relationships",
            "unmapped_relationships",
            "kept in custom.ossie",
        )

        rd_root = ossie_dict(self.sm)
        ours, theirs = split_nonportable(rd_root.get("nonportable_extensions"))
        if self.stash and self.sm.name != model_name:
            root["name"] = self.sm.name
        if self.stash and self.sm.description != model_description:
            root["description"] = self.sm.description
        if self.sm.ai_context is not None:
            self.keep(
                root,
                "ai_context",
                rd_root["ai_context"],
                "ai_context",
                "model_ai_context",
                "Neo4j has no AI context; kept in custom.ossie",
            )
        self.keep(
            root,
            "metrics",
            rd_root.get("metrics"),
            "metrics",
            "metrics",
            "Neo4j has no metrics; kept in custom.ossie",
        )
        self.keep(
            root,
            "nonportable_extensions",
            theirs,
            "nonportable_extensions",
            "nonportable_extensions",
            "other vendors' extensions kept in custom.ossie",
        )
        for t in tables.values():
            t.pop("_dataset_name")
            t.pop("_field_name")
            extra = t.pop("_unrepresentable_fks", None)
            if extra:
                t.setdefault("foreign_keys", {}).update(extra)
        return tables, root, ours

    def table(self, ds) -> tuple[str, dict]:
        path = f"datasets.{ds.name}"
        dd = ossie_dict(ds)
        ours, theirs = split_nonportable(dd.get("nonportable_extensions"))
        tid = ours.get("table_id") or ds.name
        self.table_of[_fold(ds.name)] = tid
        t: dict[str, Any] = {"source": ds.source}
        tos: dict[str, Any] = {}

        columns: list[dict] = []
        col_of: dict[str, str] = {}
        field_name: dict[str, str] = {}
        derived: list[dict] = []
        used: set[str] = set()
        for i, f in enumerate(ds.fields or []):
            fd = dd["fields"][i]
            fpath = f"{path}.fields.{f.name}"
            field_name[_fold(f.name)] = f.name
            col_name = None if f.expression.dialects else parse_identifier(f.expression.sql)
            if col_name is None or _fold(col_name) in used:
                derived.append({"index": i, "field": fd})
                self.r.add(
                    Loss.STASHED if self.stash else Loss.DROPPED,
                    "derived_field",
                    fpath,
                    "field is an expression, not a column; kept in custom.ossie",
                )
                continue
            used.add(_fold(col_name))
            col_of[_fold(f.name)] = col_name
            fours, ftheirs = split_nonportable(fd.get("nonportable_extensions"))
            col: dict[str, Any] = {}
            if not fours.get("unnamed"):
                col["name"] = col_name
            for k in ("type", "size"):
                if k in fours:
                    col[k] = fours[k]
            default = _default_suggested(f.datatype.value if f.datatype else None)
            if "suggested" in fours:
                st = fours["suggested"]
                actual = f.datatype.value if f.datatype else None
                # A stashed "no suggestion" fits if the datatype is what the source type implies.
                fits = (st is None and actual == datatype_from_source(fours.get("type"))) or (
                    st is not None and datatype_for(st) == actual
                )
                suggested = st if fits else default
            else:
                suggested = default
            if suggested:
                col["suggested"] = suggested
            if f.datatype is not None and f.datatype.value == "Decimal" and suggested == "FLOAT":
                self.r.add(
                    Loss.APPROXIMATED,
                    "decimal_as_float",
                    fpath,
                    "Decimal is exact; the column's suggested Neo4j type is FLOAT",
                )
            for k in ("supported", "nullable", "dimension"):
                if k in fours:
                    col[k] = fours[k]

            cos: dict[str, Any] = {}
            if f.name != col_name:
                cos["name"] = f.name
            for k in ("description", "label", "dimension", "ai_context"):
                if k in fd:
                    cos[k] = fd[k]
            derived_dt = _derived_datatype(suggested, col.get("type"))
            actual_dt = f.datatype.value if f.datatype else None
            if derived_dt != actual_dt:
                cos["datatype"] = actual_dt
            if ftheirs:
                cos["nonportable_extensions"] = ftheirs
            custom = dict(fours.get("custom") or {})
            if cos:
                self.keep(
                    custom,
                    OSSIE,
                    cos,
                    fpath,
                    "field_details",
                    "field description / ai_context / label kept in custom.ossie",
                )
            if custom:
                col["custom"] = custom
            columns.append(col)

        if columns:
            t["columns"] = columns
        if ds.primary_key:
            pk = [col_of.get(_fold(k)) for k in ds.primary_key]
            if None not in pk:
                t["primary_keys"] = pk
                spelled = [field_name[_fold(k)] for k in ds.primary_key]
                if spelled != ds.primary_key and self.stash:
                    tos["primary_key"] = ds.primary_key
                    tos["primary_keys_snapshot"] = pk
            else:
                if self.stash:
                    tos["primary_key"] = ds.primary_key
                    tos["primary_keys_snapshot"] = []
                self.r.add(
                    Loss.APPROXIMATED,
                    "primary_key_uses_derived_field",
                    f"{path}.primary_key",
                    "primary key includes a derived field; no OGS primary key written",
                )
        elif "primary_keys" in ours:
            t["primary_keys"] = ours["primary_keys"]
        if "foreign_keys" in ours:
            t["_unrepresentable_fks"] = dict(ours["foreign_keys"])

        if ds.description is not None and self.stash:
            tos["description"] = ds.description
        self.keep(
            tos,
            "unique_keys",
            dd.get("unique_keys"),
            f"{path}.unique_keys",
            "unique_keys",
            "OGS tables have no unique keys; kept in custom.ossie",
        )
        if ds.ai_context is not None:
            self.keep(
                tos,
                "ai_context",
                dd["ai_context"],
                f"{path}.ai_context",
                "dataset_ai_context",
                "Neo4j has no AI context; kept in custom.ossie",
            )
        self.keep(
            tos,
            "metrics",
            dd.get("metrics"),
            f"{path}.metrics",
            "dataset_metrics",
            "Neo4j has no metrics; kept in custom.ossie",
        )
        self.keep(
            tos,
            "nonportable_extensions",
            theirs,
            path,
            "nonportable_extensions",
            "other vendors' extensions kept in custom.ossie",
        )
        self.keep(
            tos,
            "derived_fields",
            derived,
            path,
            "derived_fields",
            "derived fields kept in custom.ossie",
        )
        custom = dict(ours.get("custom") or {})
        if tos and self.stash:
            custom[OSSIE] = tos
        if custom:
            t["custom"] = custom
        t["_dataset_name"] = ds.name
        t["_field_name"] = field_name
        self.col_of[tid] = col_of
        return tid, t


def semantic_model_to_tables(
    sm: SemanticModel,
    *,
    model_name: str | None,
    model_description: str | None,
    stash: bool,
    report: LossReport,
) -> tuple[dict, dict, dict]:
    """An Ossie semantic model ➜ ``(data_source_tables, custom.ossie.semantic_model, NEO4J data)``.

    ``model_name`` and ``model_description`` are what the OGS model already has, from the
    ontology if there is one. The semantic model's own name and description are stashed only if
    they differ.
    """
    return _SemanticToTables(sm, stash, report).run(model_name, model_description)
