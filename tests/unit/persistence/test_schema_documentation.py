"""Model docstrings reach the database as table and column comments."""

from typing import ClassVar

from sqlalchemy import BigInteger, ForeignKey, Integer, MetaData, Table, Text
from sqlalchemy.orm import Mapped, mapped_column

import squid.persistence.model_registry  # noqa: F401  # imported for its model registration side effect
from squid.persistence.base import Base
from squid.persistence.docs import extract_attribute_docstrings


class SandboxBase(Base):
    """A throwaway declarative base sharing `Base`'s documentation behaviour.

    Declaring the sample models against `Base` itself would register their tables in the
    metadata Alembic autogenerates from, so they get a `MetaData` of their own.
    """

    __abstract__ = True
    metadata: ClassVar[MetaData] = MetaData()


def table_of(model: type[Base]) -> Table:
    """The model's own table, narrowed from the `FromClause` the declarative API promises."""
    table = model.__table__
    assert isinstance(table, Table)
    return table


def test_attribute_docstrings_become_column_comments() -> None:
    """The docstring under a `mapped_column()` is what the column is commented with."""

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

    assert table_of(DocWidget).comment == "A widget."
    comments = {column.name: column.comment for column in table_of(DocWidget).columns}
    assert comments == {
        "id": None,
        "name": "What the widget is called.",
        "db_renamed": "A column whose attribute name and database name differ.",
        "explicit": "Set on the column itself.",
        "undocumented": None,
    }


def test_a_subclass_documents_its_own_table() -> None:
    """A joined-inheritance subclass must not inherit or overwrite its parent's `__table_args__`.

    Reading `__table_args__` by attribute rather than off `__dict__` finds the parent's,
    whose comment slot is already filled -- so the subclass's own table silently kept no
    comment at all while the parent's stayed the only one documented.
    """

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

    assert table_of(DocParent).comment == "The parent table."
    assert table_of(DocChild).comment == "The child table."
    assert table_of(DocParent).c.id.comment is None
    assert table_of(DocChild).c.id.comment == "Documented on the child's own table, not the parent's."


def test_every_attribute_docstring_in_the_schema_reached_its_column() -> None:
    """Sweep the real registry, so a silently no-op documentation pass cannot pass this.

    The mechanism failed exactly this way once: it looked columns up on an attribute that
    no longer held one, found nothing, and documented three columns out of the whole schema.
    Asserting on a sample model would not have noticed.
    """
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
