"""Shared SQLAlchemy declarative base."""

import inspect
from typing import Any, ClassVar

from advanced_alchemy.base import BasicAttributes
from sqlalchemy import Column, MetaData, Table
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass
from sqlalchemy.orm.properties import MappedColumn

from squid.persistence.docs import extract_attribute_docstrings


class Base(BasicAttributes, AsyncAttrs, MappedAsDataclass, DeclarativeBase, eq=False):
    """Common base for persisted entities and their SQL schema comments.

    Models inherit dataclass construction and awaitable access to attributes that
    require database I/O. Their class and attribute docstrings also become database
    comments through __init_subclass__.

    Entities compare by object identity, so changing a mapped field does not change
    equality or hashing while an instance is held in a set or dictionary.
    """

    metadata: ClassVar[MetaData] = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "fk": "%(table_name)s_%(column_0_name)s_fkey",
            "uq": "%(table_name)s_%(column_0_name)s_key",
            "pk": "%(table_name)s_pkey",
        }
    )

    __eq__ = object.__eq__
    __hash__ = object.__hash__

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Use model docstrings as table and column comments.

        A class docstring describes its table. A string immediately after an annotated
        column describes that column. Explicit SQLAlchemy comments take precedence.
        """
        # All of this has to happen before the delegation below, because that is where
        # DeclarativeBase builds the Table: afterwards __table_args__ has already been read,
        # and each mapped_column() in the class body has been swapped out for an
        # InstrumentedAttribute, which does not expose the Column it stands for. Reaching
        # for the mapper instead is not an option either -- Mapper.column_attrs forces every
        # mapper in the registry to configure, mid-class-body, before the module has finished
        # defining the classes its relationship() strings name.
        attribute_docstrings = {}
        if "__tablename__" in cls.__dict__:  # Anything else is a mixin or an abstract base.
            attribute_docstrings = extract_attribute_docstrings(cls)
            if cls.__doc__ is not None:
                _document_table(cls, inspect.cleandoc(cls.__doc__))
            for attribute, docstring in attribute_docstrings.items():
                # __dict__ rather than getattr: an inherited name resolves to the parent's
                # already-instrumented attribute, whose column belongs to the parent's table.
                declaration = cls.__dict__.get(attribute)
                if not isinstance(declaration, MappedColumn):
                    continue  # A relationship or a plain class attribute, not a column.
                column: Column[Any] = declaration.column
                if column.comment is None:
                    column.comment = docstring

        kwargs.setdefault("eq", False)
        super().__init_subclass__(**kwargs)

        # Mixin columns are copied onto this table during declarative setup. A model
        # can annotate one without assigning it to document its local meaning.
        table = cls.__dict__.get("__table__")
        if isinstance(table, Table):
            for attribute, docstring in attribute_docstrings.items():
                column = table.c.get(attribute)
                if column is not None and column.comment is None:
                    column.comment = docstring


def _document_table(cls: type[Base], comment: str) -> None:
    """Set the table comment without changing inherited table options.

    Read the class's own `__table_args__`: a subclass may inherit options for its
    parent's table, which must not receive the subclass's comment.
    """
    table_args = cls.__dict__.get("__table_args__")
    if table_args is None:
        cls.__table_args__ = {"comment": comment}
    elif isinstance(table_args, dict):
        _fill_comment(table_args, comment)
    elif isinstance(table_args, tuple):
        if table_args and isinstance(table_args[-1], dict):
            _fill_comment(table_args[-1], comment)
        else:
            cls.__table_args__ = (*table_args, {"comment": comment})


def _fill_comment(table_args: dict[str, Any], comment: str) -> None:
    """Use the docstring unless the model supplies an explicit comment."""
    if table_args.get("comment") is None:
        table_args["comment"] = comment
