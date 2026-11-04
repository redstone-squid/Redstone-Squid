"""Shared advanced-alchemy repository foundation."""

from advanced_alchemy.base import ModelProtocol
from advanced_alchemy.repository import SQLAlchemyAsyncRepository


class BaseAsyncRepository[ModelT: ModelProtocol](SQLAlchemyAsyncRepository[ModelT]):
    """Project-wide base for Advanced Alchemy's asynchronous model repositories."""
