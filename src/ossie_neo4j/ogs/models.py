"""Pydantic models for the Neo4j Ontology Graph Spec 1.1.0.

These mirror ``schemas/ogs-1.1.0/schema.json`` one to one:
- every object forbids extra keys, as the schema does;
- every field the schema allows as ``null`` is ``Optional``.

Python attribute names differ from the JSON keys only where the key is a Python keyword or a
Pydantic reserved name (``from``, ``schema``), or contains dots (index options). Always
serialize with :func:`to_dict`, which writes the JSON keys.

The cross-reference rules are not enforced here; see :mod:`ossie_neo4j.ogs.rules`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Custom = dict[str, Any]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Neo4jType(StrEnum):
    ANY = "ANY"
    LIST_ANY = "LIST<ANY>"
    BOOLEAN = "BOOLEAN"
    LIST_BOOLEAN = "LIST<BOOLEAN>"
    DATE = "DATE"
    LIST_DATE = "LIST<DATE>"
    DURATION = "DURATION"
    LIST_DURATION = "LIST<DURATION>"
    FLOAT32 = "FLOAT32"
    LIST_FLOAT32 = "LIST<FLOAT32>"
    FLOAT = "FLOAT"
    LIST_FLOAT = "LIST<FLOAT>"
    INTEGER8 = "INTEGER8"
    LIST_INTEGER8 = "LIST<INTEGER8>"
    INTEGER16 = "INTEGER16"
    LIST_INTEGER16 = "LIST<INTEGER16>"
    INTEGER32 = "INTEGER32"
    LIST_INTEGER32 = "LIST<INTEGER32>"
    INTEGER = "INTEGER"
    LIST_INTEGER = "LIST<INTEGER>"
    LOCAL_DATETIME = "LOCAL DATETIME"
    LIST_LOCAL_DATETIME = "LIST<LOCAL DATETIME>"
    LOCAL_TIME = "LOCAL TIME"
    LIST_LOCAL_TIME = "LIST<LOCAL TIME>"
    POINT = "POINT"
    LIST_POINT = "LIST<POINT>"
    STRING = "STRING"
    LIST_STRING = "LIST<STRING>"
    VECTOR_FLOAT = "VECTOR<FLOAT>"
    VECTOR_FLOAT32 = "VECTOR<FLOAT32>"
    VECTOR_INTEGER = "VECTOR<INTEGER>"
    VECTOR_INTEGER32 = "VECTOR<INTEGER32>"
    VECTOR_INTEGER16 = "VECTOR<INTEGER16>"
    VECTOR_INTEGER8 = "VECTOR<INTEGER8>"
    ZONED_DATETIME = "ZONED DATETIME"
    LIST_ZONED_DATETIME = "LIST<ZONED DATETIME>"
    ZONED_TIME = "ZONED TIME"
    LIST_ZONED_TIME = "LIST<ZONED TIME>"
    UUID = "UUID"

    @property
    def is_list(self) -> bool:
        return self.value.startswith("LIST<")

    @property
    def is_vector(self) -> bool:
        return self.value.startswith("VECTOR<")

    @property
    def element(self) -> Neo4jType:
        """The scalar element type of a LIST or VECTOR type, or the type itself."""
        if self.is_list or self.is_vector:
            return Neo4jType(self.value[self.value.index("<") + 1 : -1])
        return self


class ConstraintType(StrEnum):
    EXISTS = "EXISTS"
    KEY = "KEY"
    PROPERTY_TYPE = "PROPERTY_TYPE"
    UNIQUE = "UNIQUE"


class IndexType(StrEnum):
    FULLTEXT = "FULLTEXT"
    POINT = "POINT"
    RANGE = "RANGE"
    TEXT = "TEXT"
    VECTOR = "VECTOR"
    LOOKUP = "LOOKUP"


class MappingMode(StrEnum):
    MERGE = "MERGE"
    CREATE = "CREATE"


# --- properties and constraints -------------------------------------------------------------


class CustomExtensions(_Base):
    custom: Custom | None = None


class Property(_Base):
    type: Neo4jType | None = None
    dimension: int | None = None
    must_exist: bool | None = None
    unique: bool | None = None
    key: bool | None = None
    extensions: CustomExtensions | None = None
    name: str | None = None
    description: str | None = None
    aliases: list[str] | None = None
    reference: str | None = None
    pattern: str | None = None
    one_of: list[str] | None = None


class Constraint(_Base):
    """A node constraint or a relationship constraint. The two have the same shape."""

    type: ConstraintType
    properties: list[str]
    extensions: CustomExtensions | None = None
    name: str | None = None


# --- indexes ---------------------------------------------------------------------------------


class FulltextOptions(_Base):
    type: Literal["FULLTEXT"]
    default_analyzer: str | None = Field(None, alias="fulltext.default_analyzer")
    analyzer: str | None = Field(None, alias="fulltext.analyzer")
    eventually_consistent: bool | None = Field(None, alias="fulltext.eventually_consistent")
    eventually_consistent_apply_parallelism: int | None = Field(
        None, alias="fulltext.eventually_consistent_apply_parallelism"
    )
    eventually_consistent_refresh_interval: str | None = Field(
        None, alias="fulltext.eventually_consistent_refresh_interval"
    )
    eventually_consistent_refresh_parallelism: int | None = Field(
        None, alias="fulltext.eventually_consistent_refresh_parallelism"
    )
    eventually_consistent_index_update_queue_max_length: int | None = Field(
        None, alias="fulltext.eventually_consistent_index_update_queue_max_length"
    )


class PointOptions(_Base):
    type: Literal["POINT"]
    cartesian_min: list[float] | None = Field(None, alias="spatial.cartesian.min")
    cartesian_max: list[float] | None = Field(None, alias="spatial.cartesian.max")
    cartesian_3d_min: list[float] | None = Field(None, alias="spatial.cartesian-3d.min")
    cartesian_3d_max: list[float] | None = Field(None, alias="spatial.cartesian-3d.max")
    wgs_84_min: list[float] | None = Field(None, alias="spatial.wgs-84.min")
    wgs_84_max: list[float] | None = Field(None, alias="spatial.wgs-84.max")
    wgs_84_3d_min: list[float] | None = Field(None, alias="spatial.wgs-84-3d.min")
    wgs_84_3d_max: list[float] | None = Field(None, alias="spatial.wgs-84-3d.max")


class VectorOptions(_Base):
    type: Literal["VECTOR"]
    dimensions: int | None = Field(None, alias="vector.dimensions")
    similarity_function: str | None = Field(None, alias="vector.similarity_function")
    default_search_expansion_factor: float | None = Field(
        None, alias="vector.default_search_expansion_factor"
    )
    quantization_enabled: bool | None = Field(None, alias="vector.quantization.enabled")
    quantization_type: str | None = Field(None, alias="vector.quantization.type")
    hnsw_m: int | None = Field(None, alias="vector.hnsw.m")
    hnsw_ef_construction: int | None = Field(None, alias="vector.hnsw.ef_construction")


IndexOptions = Annotated[
    FulltextOptions | PointOptions | VectorOptions, Field(discriminator="type")
]


class NodeIndex(_Base):
    type: IndexType
    labels: list[str]
    properties: list[str]
    options: IndexOptions | None = None
    custom: Custom | None = None
    name: str | None = None


class RelationshipIndex(_Base):
    type: IndexType
    properties: list[str]
    options: IndexOptions | None = None
    custom: Custom | None = None
    name: str | None = None


# --- nodes -----------------------------------------------------------------------------------


class Labels(_Base):
    identifier: str | None = None
    implied: list[str] | None = None
    optional: list[str] | None = None
    extensions: CustomExtensions | None = None


class NodeDisplay(_Base):
    x: float
    y: float
    colour: str | None = None
    custom: Custom | None = None


class NodeExtensions(_Base):
    indexes: dict[str, NodeIndex] | None = None
    display: NodeDisplay | None = None
    custom: Custom | None = None


class Node(_Base):
    label: str | None = None
    labels: Labels | None = None
    properties: dict[str, Property] | None = None
    constraints: dict[str, Constraint] | None = None
    extensions: NodeExtensions | None = None
    description: str | None = None
    aliases: list[str] | None = None
    reference: str | None = None

    @property
    def identifier_label(self) -> str | None:
        """The identifying label, whether it was written as shorthand or longhand."""
        if self.labels and self.labels.identifier:
            return self.labels.identifier
        return self.label


# --- relationships ---------------------------------------------------------------------------


class RelationshipEnd(_Base):
    node: str | None = None
    label: str | None = None
    count: int | None = None
    min_count: int | None = None
    max_count: int | None = None


class RelationshipExtensions(_Base):
    indexes: dict[str, RelationshipIndex] | None = None
    custom: Custom | None = None


class Relationship(_Base):
    type: str
    from_: RelationshipEnd = Field(alias="from")
    to: RelationshipEnd
    properties: dict[str, Property] | None = None
    constraints: dict[str, Constraint] | None = None
    extensions: RelationshipExtensions | None = None
    description: str | None = None
    aliases: list[str] | None = None
    reference: str | None = None


# --- data sources ----------------------------------------------------------------------------


class TableColumn(_Base):
    name: str | None = None
    type: str | None = None
    size: int | None = None
    suggested: Neo4jType | None = None
    supported: list[Neo4jType] | None = None
    nullable: bool | None = None
    dimension: int | None = None
    custom: Custom | None = None


class ForeignKeyReference(_Base):
    table: str
    columns: list[str] | None = None
    custom: Custom | None = None


class ForeignKey(_Base):
    columns: list[str]
    references: ForeignKeyReference
    custom: Custom | None = None


class Table(_Base):
    source: str
    columns: list[TableColumn] | None = None
    primary_keys: list[str] | None = None
    foreign_keys: dict[str, ForeignKey] | None = None
    custom: Custom | None = None


class PropertyMapping(_Base):
    column: str


class TargetMapping(_Base):
    label: str | None = None
    properties: dict[str, PropertyMapping] | None = None


class NodeMapping(_Base):
    type: Literal["NODE"]
    node: str
    table: str
    properties: dict[str, PropertyMapping]
    mode: MappingMode | None = None
    match_label: str | None = None
    key: list[str] | None = None
    depends_on: str | None = None
    id: str | None = None


class RelationshipMapping(_Base):
    type: Literal["RELATIONSHIP"]
    relationship: str
    table: str
    from_node: TargetMapping
    to_node: TargetMapping
    match_label: str | None = None
    properties: dict[str, PropertyMapping] | None = None
    mode: MappingMode | None = None
    key: list[str] | None = None
    depends_on: str | None = None
    id: str | None = None


class QueryMapping(_Base):
    type: Literal["QUERY"]
    table: str
    query: str
    depends_on: str | None = None
    id: str | None = None


Mapping = Annotated[NodeMapping | RelationshipMapping | QueryMapping, Field(discriminator="type")]


class GraphModelExtensions(_Base):
    data_source_tables: dict[str, Table] | None = None
    data_source_mappings: dict[str, list[Mapping]] | None = None
    custom: Custom | None = None


# --- root ------------------------------------------------------------------------------------


class GraphModel(_Base):
    schema_: str = Field(alias="schema")
    version: str
    id: str
    name: str | None = None
    description: str | None = None
    nodes: dict[str, Node] | None = None
    relationships: dict[str, Relationship] | None = None
    extensions: GraphModelExtensions | None = None


def to_dict(model: BaseModel) -> dict[str, Any]:
    """Serialize a model back to its JSON shape: JSON keys, with ``None`` values left out.

    Because nulls are left out, an explicit ``null`` in the input is not written back.
    Normalization treats ``null`` as absent anyway.
    """
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)
