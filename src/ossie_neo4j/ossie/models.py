"""Pydantic models for Apache Ossie 0.3.0.dev0, pinned to a single upstream commit.

There are three document kinds, each mirroring one pinned schema:
- :class:`SemanticModel` mirrors ``core-spec/ossie-schema.json``;
- :class:`Ontology` mirrors ``ontology/ontology.json``;
- :class:`MappingDocument` mirrors ``ontology/mapping.json``.

Ossie is changing quickly, so this module is the only place the rest of the converter depends
on Ossie's shape. When the pinned commit moves, update this module and the schemas together.

Serialize with :func:`to_dict`, which writes the JSON keys (for example ``from``). The
cross-reference rules are not enforced here; see :mod:`ossie_neo4j.ossie.rules`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SPEC_VERSION = "0.3.0.dev0"
SpecVersion = Literal["0.3.0.dev0"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --- shared ----------------------------------------------------------------------------------


class AIContextObject(BaseModel):
    """The object form of ``ai_context``. Extra keys are allowed, as the schema allows them."""

    model_config = ConfigDict(extra="allow")

    instructions: str | None = None
    synonyms: list[str] | None = None
    examples: list[str] | None = None


AIContext = str | AIContextObject


class NonportableExtension(_Base):
    vendor_name: str = Field(min_length=1)
    data: dict[str, Any]


# --- core semantic model ---------------------------------------------------------------------


class DataType(StrEnum):
    STRING = "String"
    INTEGER = "Integer"
    DECIMAL = "Decimal"
    FLOAT = "Float"
    BOOLEAN = "Boolean"
    DATE = "Date"
    TIME = "Time"
    DATETIME = "DateTime"
    DATETIME_TZ = "DateTimeTz"
    OPAQUE = "Opaque"


TEMPORAL_DATATYPES = frozenset(
    {DataType.DATE, DataType.TIME, DataType.DATETIME, DataType.DATETIME_TZ}
)


class DialectExpression(_Base):
    dialect: str = Field(min_length=1)
    expression: str = Field(min_length=1)


class Expression(_Base):
    sql: str | None = Field(None, min_length=1)
    dialects: list[DialectExpression] | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def _one_of_sql_or_dialects(self) -> Expression:
        if self.sql is None and self.dialects is None:
            raise ValueError("expression needs 'sql' or 'dialects'")
        return self


class Dimension(_Base):
    is_time: bool | None = None


class Field_(_Base):
    """A dataset field. The trailing underscore avoids clashing with :func:`pydantic.Field`."""

    name: str = Field(min_length=1)
    expression: Expression
    dimension: Dimension | None = None
    label: str | None = None
    description: str | None = None
    datatype: DataType | None = None
    ai_context: AIContext | None = None
    nonportable_extensions: list[NonportableExtension] | None = None

    @property
    def is_time(self) -> bool:
        """Whether this is a time dimension (the spec's default applies if is_time is unset)."""
        if self.dimension and self.dimension.is_time is not None:
            return self.dimension.is_time
        return self.datatype in TEMPORAL_DATATYPES


class Metric(_Base):
    name: str = Field(min_length=1)
    expression: Expression
    description: str | None = None
    datatype: DataType | None = None
    ai_context: AIContext | None = None
    nonportable_extensions: list[NonportableExtension] | None = None


class Dataset(_Base):
    name: str = Field(min_length=1)
    source: str = Field(min_length=1)
    primary_key: list[str] | None = Field(None, min_length=1)
    unique_keys: list[list[str]] | None = Field(None, min_length=1)
    description: str | None = None
    ai_context: AIContext | None = None
    fields: list[Field_] | None = None
    metrics: list[Metric] | None = None
    nonportable_extensions: list[NonportableExtension] | None = None


class Relationship(_Base):
    name: str = Field(min_length=1)
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    from_columns: list[str] = Field(min_length=1)
    to_columns: list[str] = Field(min_length=1)
    ai_context: AIContext | None = None
    nonportable_extensions: list[NonportableExtension] | None = None


class SemanticModel(_Base):
    version: SpecVersion
    name: str = Field(min_length=1)
    description: str | None = None
    ai_context: AIContext | None = None
    datasets: list[Dataset] = Field(min_length=1)
    relationships: list[Relationship] | None = None
    metrics: list[Metric] | None = None
    nonportable_extensions: list[NonportableExtension] | None = None


# --- ontology --------------------------------------------------------------------------------


class ConceptType(StrEnum):
    ENTITY_TYPE = "EntityType"
    VALUE_TYPE = "ValueType"


class Multiplicity(StrEnum):
    MANY_TO_ONE = "ManyToOne"
    ONE_TO_ONE = "OneToOne"


BUILTIN_CONCEPTS: dict[str, ConceptType] = {
    "Any": ConceptType.ENTITY_TYPE,
    "Boolean": ConceptType.VALUE_TYPE,
    "Date": ConceptType.VALUE_TYPE,
    "DateTime": ConceptType.VALUE_TYPE,
    "Decimal": ConceptType.VALUE_TYPE,
    "Float": ConceptType.VALUE_TYPE,
    "Integer": ConceptType.VALUE_TYPE,
    "String": ConceptType.VALUE_TYPE,
}

Annotations = dict[str, Any]


class Role(_Base):
    concept: str = Field(min_length=1)
    name: str | None = Field(None, min_length=1)
    annotations: Annotations | None = None


class OntologyRelationship(_Base):
    """An n-ary relationship: the declaring concept plays the first role, ``roles`` the rest."""

    name: str = Field(min_length=1)
    description: str | None = None
    roles: list[Role] | None = None
    multiplicity: Multiplicity | None = None
    derived_by: list[str] | None = None
    requires: list[str] | None = None
    verbalizes: list[str]
    annotations: Annotations | None = None
    iri: str | None = Field(None, min_length=1)

    @property
    def arity(self) -> int:
        return 1 + len(self.roles or [])


class Concept(_Base):
    """An ``OntologyComponent`` in the schema."""

    concept: str = Field(min_length=1)
    type: ConceptType
    description: str | None = None
    extends: list[str] | None = None
    derived_by: list[str] | None = None
    identify_by: list[str] | None = None
    requires: list[str] | None = None
    relationships: list[OntologyRelationship] | None = None
    annotations: Annotations | None = None
    iri: str | None = Field(None, min_length=1)


class ReferentMapping(_Base):
    relationship: str = Field(min_length=1)
    expression: str | None = Field(None, min_length=1)
    referent_mappings: list[ReferentMapping] | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def _one_of(self) -> ReferentMapping:
        if self.expression is None and self.referent_mappings is None:
            raise ValueError("referent mapping needs 'expression' or 'referent_mappings'")
        return self


class ObjectMapping(_Base):
    concept: str | None = Field(None, min_length=1)
    referent_mappings: list[ReferentMapping] | None = Field(None, min_length=1)
    expression: str | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def _one_of(self) -> ObjectMapping:
        if self.expression is None and self.referent_mappings is None:
            raise ValueError("object mapping needs 'expression' or 'referent_mappings'")
        return self


class LinkMapping(_Base):
    relationship: str | None = Field(None, min_length=1)
    object_mapping: ObjectMapping
    children: list[LinkMapping] | None = None


class ConceptMapping(_Base):
    concept: str = Field(min_length=1)
    object_mappings: list[ObjectMapping] | None = Field(None, min_length=1)
    link_mappings: list[LinkMapping] | None = Field(None, min_length=1)

    @model_validator(mode="after")
    def _one_of(self) -> ConceptMapping:
        if self.object_mappings is None and self.link_mappings is None:
            raise ValueError("concept mapping needs 'object_mappings' or 'link_mappings'")
        return self


class EmbeddedOntologyMap(_Base):
    """The deprecated ``ontology_mappings`` entry embedded in an ontology document."""

    name: str | None = Field(None, min_length=1)
    description: str | None = None
    semantic_model: SemanticModel
    concept_mappings: list[ConceptMapping]


class Ontology(_Base):
    version: SpecVersion
    name: str = Field(min_length=1)
    description: str | None = None
    ai_context: AIContext | None = None
    requires: list[str] | None = None
    ontology: list[Concept] = Field(min_length=1)
    ontology_mappings: list[EmbeddedOntologyMap] | None = None
    annotations: Annotations | None = None
    prefixes: dict[str, str] | None = None


# --- mapping document ------------------------------------------------------------------------


class DocumentReference(_Base):
    name: str = Field(min_length=1)
    iri: str | None = Field(None, min_length=1)


class MappingDocument(_Base):
    version: SpecVersion
    name: str = Field(min_length=1)
    description: str | None = None
    ontology_ref: DocumentReference
    semantic_model_ref: DocumentReference
    concept_mappings: list[ConceptMapping]
    nonportable_extensions: list[NonportableExtension] | None = None


def to_dict(model: BaseModel) -> dict[str, Any]:
    """Serialize a model back to its JSON shape: JSON keys, with ``None`` values left out."""
    return model.model_dump(mode="json", by_alias=True, exclude_none=True)
