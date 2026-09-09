"""Audits for the shared schema base and for hand-written field patterns."""

import importlib
import inspect
import pkgutil

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import squid.api
from squid.api.schema import ApiSchema
from squid.api.v1.schemas.minecraft_auth import ChallengeApprovalRequest
from squid.minecraft_auth.application.crypto import MinecraftSecretCodec


class _AttributeDocstringExample(ApiSchema):
    model_config = ConfigDict(extra="forbid")

    documented: int
    """How many times the caller may retry."""
    also_annotated: int = Field(description="Explicit.")
    """Not published: an explicit description wins over the attribute docstring."""


def _api_models() -> list[type[BaseModel]]:
    """Every pydantic model defined under `squid.api`, found by importing the whole package."""
    models: dict[str, type[BaseModel]] = {}
    for module_info in pkgutil.walk_packages(squid.api.__path__, prefix="squid.api."):
        module = importlib.import_module(module_info.name)
        for value in vars(module).values():
            if (
                inspect.isclass(value)
                and issubclass(value, BaseModel)
                and value is not BaseModel
                and value.__module__.startswith("squid.api")
            ):
                models[f"{value.__module__}.{value.__qualname__}"] = value
    return list(models.values())


def test_every_api_model_publishes_its_attribute_docstrings() -> None:
    models = _api_models()

    assert models
    for model in models:
        assert model.model_config.get("use_attribute_docstrings") is True, (
            f"{model.__module__}.{model.__qualname__} does not derive from ApiSchema"
        )


def test_an_attribute_docstring_becomes_a_published_description() -> None:
    properties = _AttributeDocstringExample.model_json_schema()["properties"]

    assert properties["documented"]["description"] == "How many times the caller may retry."
    assert properties["also_annotated"]["description"] == "Explicit."


def test_user_code_accepts_a_code_the_server_issues() -> None:
    issued = MinecraftSecretCodec(b"p" * 32).random_user_code()

    assert ChallengeApprovalRequest(user_code=issued).user_code == issued
    # Approval normalizes case and dashes away before lookup, so both forms reach the same challenge.
    assert ChallengeApprovalRequest(user_code=issued.lower()).user_code == issued.lower()
    assert ChallengeApprovalRequest(user_code=issued.replace("-", "")).user_code == issued.replace("-", "")


@pytest.mark.parametrize(
    "code",
    [
        "ABC1-EFGH-IJKL-MNOP",  # 0, 1, 8 and 9 are outside the base32 alphabet
        "ABCD-EFGH-IJKL",  # one group short of an issued code
        "ABCD-EFGH-IJKL-MNOP-QRST",  # one group too many
        "ABCD_EFGH_IJKL_MNOP",  # the separator base64url uses, not this one
        " ABCD-EFGH-IJKL-MNOP",  # pydantic does not strip before matching
    ],
)
def test_user_code_rejects_a_code_the_server_never_issues(code: str) -> None:
    with pytest.raises(ValidationError):
        ChallengeApprovalRequest(user_code=code)
