"""Shared SQLAlchemy infrastructure."""

# Model registration lives in ``model_registry`` so importing ``persistence.base``
# cannot recursively initialize every infrastructure adapter.
