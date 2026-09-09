"""Base configuration shared by every schema this API publishes."""

from pydantic import BaseModel, ConfigDict


class ApiSchema(BaseModel):
    """Root of the published request and response bodies.

    An attribute docstring becomes the field's `description` in the generated OpenAPI document, so
    anything written under a field reaches clients rather than only source readers. An explicit
    `Field(description=...)` wins where a field carries both. Pydantic merges `model_config` down the
    MRO, so a subclass declares only what it adds, such as `extra="forbid"` or `frozen=True`.

    Attribute docstrings are read from the class's source, which an installation that ships only
    bytecode does not have; such a deployment publishes the document generated at build time.
    """

    model_config = ConfigDict(use_attribute_docstrings=True)
