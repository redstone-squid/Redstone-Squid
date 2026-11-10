"""Errors in message declarations, catalogs, and rendering."""


class LocalizationError(Exception):
    """Base exception for a localization contract violation."""


class DeclarationError(LocalizationError, ValueError):
    """A Python declaration cannot be compiled into one Fluent message."""


class CatalogError(LocalizationError, ValueError):
    """A catalog contains invalid or conflicting definitions."""


class ResolutionError(LocalizationError, ValueError):
    """Neither the requested translation nor its source can be rendered."""
