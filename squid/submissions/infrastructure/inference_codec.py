"""Pydantic-backed JSON codec for retained inference inputs."""

from typing import cast, override

from pydantic import TypeAdapter

from squid.builds.application.inference import BuildInferenceInput, InlineImage
from squid.core.errors import JSONValue
from squid.submissions.application.inference_runs import InferenceRunCodec
from squid.submissions.domain.source_files import SubmissionSourceFile

_BUNDLE = TypeAdapter(BuildInferenceInput)
_IMAGE = TypeAdapter(InlineImage)
_SOURCE_FILES = TypeAdapter(tuple[SubmissionSourceFile, ...])


class PydanticInferenceRunCodec(InferenceRunCodec):
    """Validate JSON when it crosses between application values and PostgreSQL."""

    @override
    def encode_bundle(self, bundle: BuildInferenceInput) -> dict[str, JSONValue]:
        return cast(dict[str, JSONValue], _BUNDLE.dump_python(bundle, mode="json"))

    @override
    def decode_bundle(self, value: JSONValue) -> BuildInferenceInput:
        return _BUNDLE.validate_python(value)

    @override
    def encode_source_files(self, source_files: tuple[SubmissionSourceFile, ...]) -> list[JSONValue]:
        return cast(list[JSONValue], _SOURCE_FILES.dump_python(source_files, mode="json"))

    @override
    def decode_source_files(self, value: JSONValue) -> tuple[SubmissionSourceFile, ...]:
        return _SOURCE_FILES.validate_python(value)

    @override
    def decode_image(self, value: dict[str, JSONValue], data: bytes) -> InlineImage:
        return _IMAGE.validate_python(
            {
                "data": data,
                "content_type": value["content_type"],
                "source_message_id": value["source_message_id"],
                "origin": value["origin"],
            }
        )
