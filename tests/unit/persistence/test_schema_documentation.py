"""Model docstrings reach the database as table and column comments."""

from typing import ClassVar

from sqlalchemy import BigInteger, ForeignKey, Integer, MetaData, Table, Text
from sqlalchemy.orm import Mapped, mapped_column

import squid.persistence.model_registry  # noqa: F401  # imported for its model registration side effect
from squid.persistence.base import Base
from squid.persistence.docs import extract_attribute_docstrings
from tests.support.schema import model_table


class SandboxBase(Base):
    """A throwaway declarative base sharing `Base`'s documentation behaviour.

    Declaring the sample models against `Base` itself would register their tables in the
    metadata Alembic autogenerates from, so they get a `MetaData` of their own.
    """

    __abstract__ = True
    metadata: ClassVar[MetaData] = MetaData()


def test_attribute_docstrings_become_column_comments() -> None:

    class DocWidget(SandboxBase, kw_only=True):
        """A widget."""

        __tablename__ = "doc_widgets"

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, init=False)
        name: Mapped[str] = mapped_column(Text)
        """What the widget is called."""
        renamed: Mapped[str] = mapped_column("db_renamed", Text)
        """A column whose attribute name and database name differ."""
        explicit: Mapped[str] = mapped_column(Text, comment="Set on the column itself.")
        """This docstring loses to the explicit comment."""
        undocumented: Mapped[int] = mapped_column(Integer)

    assert model_table(DocWidget).comment == "A widget."
    comments = {column.name: column.comment for column in model_table(DocWidget).columns}
    assert comments == {
        "id": None,
        "name": "What the widget is called.",
        "db_renamed": "A column whose attribute name and database name differ.",
        "explicit": "Set on the column itself.",
        "undocumented": None,
    }


def test_a_subclass_documents_its_own_table() -> None:
    """Give joined-inheritance tables their own comments without modifying the parent's metadata."""

    class DocParent(SandboxBase, kw_only=True):
        """The parent table."""

        __tablename__ = "doc_parents"
        __table_args__ = ({"info": {"origin": "parent"}},)

        id: Mapped[int] = mapped_column(BigInteger, primary_key=True, init=False)
        kind: Mapped[str] = mapped_column(Text)

        __mapper_args__ = {"polymorphic_on": kind}

    class DocChild(DocParent, kw_only=True):
        """The child table."""

        __tablename__ = "doc_children"
        __mapper_args__ = {"polymorphic_identity": "child"}

        id: Mapped[int] = mapped_column(  # pyright: ignore[reportIncompatibleVariableOverride]
            BigInteger, ForeignKey("doc_parents.id"), primary_key=True, init=False
        )
        """Documented on the child's own table, not the parent's."""

    assert model_table(DocParent).comment == "The parent table."
    assert model_table(DocChild).comment == "The child table."
    assert model_table(DocParent).c.id.comment is None
    assert model_table(DocChild).c.id.comment == "Documented on the child's own table, not the parent's."


def test_every_attribute_docstring_in_the_schema_reached_its_column() -> None:
    """Check the real model registry as well as the isolated sample models."""
    undocumented: list[str] = []
    documented = 0
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if not isinstance(table, Table) or table.metadata is not Base.metadata:
            continue  # A sandbox model declared by another test, not part of the schema.
        for attribute, docstring in extract_attribute_docstrings(mapper.class_).items():
            column_attribute = mapper.column_attrs.get(attribute)
            if column_attribute is None:
                continue  # A relationship or a plain class attribute, not a column.
            for column in column_attribute.columns:
                if column.comment == docstring:
                    documented += 1
                else:
                    undocumented.append(f"{table.name}.{column.name}")

    assert undocumented == [], f"these documented attributes lost their comment: {undocumented}"
    assert documented > 50, "the registry sweep found almost no attribute docstrings, so it proves nothing"
