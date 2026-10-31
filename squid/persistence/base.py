"""Shared SQLAlchemy declarative base."""

import inspect
from typing import Any

from advanced_alchemy.base import BasicAttributes
from sqlalchemy import Column
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass
from sqlalchemy.orm.properties import MappedColumn

from squid.persistence.docs import extract_attribute_docstrings


class Base(BasicAttributes, AsyncAttrs, MappedAsDataclass, DeclarativeBase):
    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Populate table/column comments from docstrings, so the database is self-documenting.

        A class docstring becomes the table comment; a bare string literal
        immediately following an attribute's annotation becomes that column's
        comment (mirroring how attribute docstrings are written throughout
        this module, e.g. in pydantic models).
        """
        # All of this has to happen before the delegation below, because that is where
        # DeclarativeBase builds the Table: afterwards __table_args__ has already been read,
        # and each mapped_column() in the class body has been swapped out for an
        # InstrumentedAttribute, which does not expose the Column it stands for. Reaching
        # for the mapper instead is not an option either -- Mapper.column_attrs forces every
        # mapper in the registry to configure, mid-class-body, before the module has finished
        # defining the classes its relationship() strings name.
        if "__tablename__" in cls.__dict__:  # Anything else is a mixin or an abstract base.
            if cls.__doc__ is not None:
                _document_table(cls, inspect.cleandoc(cls.__doc__))
            for attribute, docstring in extract_attribute_docstrings(cls).items():
                # __dict__ rather than getattr: an inherited name resolves to the parent's
                # already-instrumented attribute, whose column belongs to the parent's table.
                declaration = cls.__dict__.get(attribute)
                if not isinstance(declaration, MappedColumn):
                    continue  # A relationship or a plain class attribute, not a column.
                column: Column[Any] = declaration.column
                if column.comment is None:
                    column.comment = docstring

        super().__init_subclass__(**kwargs)


def _document_table(cls: type[Base], comment: str) -> None:
    """Record *comment* as the table comment in *cls*'s own `__table_args__`.

    Reads from `__dict__` rather than by attribute: a joined-inheritance subclass inherits
    its parent's `__table_args__`, and filling in a comment there would document the parent's
    table a second time while leaving this one undocumented.
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
    """Fill in the comment slot unless the model already spelled one out."""
    if table_args.get("comment") is None:
        table_args["comment"] = comment
