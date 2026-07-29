"""Shared SQLAlchemy declarative base."""

import inspect
from typing import Any

from advanced_alchemy.base import BasicAttributes
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, MappedAsDataclass

from squid.persistence.docs import extract_attribute_docstrings


class Base(BasicAttributes, AsyncAttrs, MappedAsDataclass, DeclarativeBase):
    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Populate table/column comments from docstrings, so the database is self-documenting.

        A class docstring becomes the table comment; a bare string literal
        immediately following an attribute's annotation becomes that column's
        comment (mirroring how attribute docstrings are written throughout
        this module, e.g. in pydantic models).
        """
        is_mapped_table = "__tablename__" in cls.__dict__

        # Table construction happens inside DeclarativeBase's __init_subclass__, so
        # __table_args__ must be finalized before we delegate to it via super().
        if is_mapped_table and cls.__doc__ is not None:
            table_comment = inspect.cleandoc(cls.__doc__)
            if not hasattr(cls, "__table_args__"):
                cls.__table_args__ = {"comment": table_comment}
            elif isinstance(cls.__table_args__, dict) and cls.__table_args__.get("comment") is None:
                cls.__table_args__["comment"] = table_comment
            elif isinstance(cls.__table_args__, tuple):
                if cls.__table_args__ and isinstance(cls.__table_args__[-1], dict):
                    cls.__table_args__[-1].setdefault("comment", table_comment)
                else:
                    cls.__table_args__ = (*cls.__table_args__, {"comment": table_comment})

        super().__init_subclass__(**kwargs)

        if not is_mapped_table:
            return  # Mixin or abstract base, not a mapped table.

        # Columns only exist as mapped attributes after the delegation above.
        for attribute, comment in extract_attribute_docstrings(cls).items():
            column = getattr(cls, attribute, None)
            underlying_column = getattr(column, "column", None)
            if underlying_column is not None and underlying_column.comment is None:
                underlying_column.comment = comment
