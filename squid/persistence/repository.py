"""Shared advanced-alchemy repository foundation."""

from typing import Generic, TypeVar

from advanced_alchemy.base import ModelProtocol
from advanced_alchemy.repository import SQLAlchemyAsyncRepository

ModelT = TypeVar("ModelT", bound=ModelProtocol)


class BaseAsyncRepository(SQLAlchemyAsyncRepository[ModelT], Generic[ModelT]):
    """Base repository for model-level persistence operations."""
