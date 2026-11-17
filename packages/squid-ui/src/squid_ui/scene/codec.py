"""Canonical scene envelopes with codecs supplied by target packages."""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Never, cast

from pydantic import TypeAdapter, ValidationError

from squid_ui.errors import SquidUiError
from squid_ui.scene._json import JsonObject, JsonReader
from squid_ui.scene.model import Asset, Body, Pager, Scene
from squid_ui.scene.schema import SceneSchema


class CodecError(SquidUiError, ValueError):
    """A scene payload is malformed or its body codec is unavailable."""


@dataclass(frozen=True, slots=True)
class SceneDialect[BodyT: Body]:
    """Wire encoding and schema declarations owned by one target vocabulary."""

    id: str
    bodies: tuple[type[BodyT], ...]
    encode: Callable[[BodyT], dict[str, Any]]
    decode: Callable[[JsonObject], BodyT]
    schema: Callable[[], dict[str, Any]]
    query_types: Mapping[str, type] = field(default_factory=dict)

    @staticmethod
    def validate[ValueT](adapter: TypeAdapter[ValueT], payload: JsonObject) -> ValueT:
        try:
            return adapter.validate_python(payload)
        except ValidationError as error:
            raise CodecError(str(error)) from error


_json = JsonReader(CodecError)


def _reject_json_constant(value: str) -> Never:
    message = f"non-finite JSON number {value}"
    raise ValueError(message)


class Codec:
    """Encode protocol 1 using explicitly imported target scene vocabularies.

    Import a target's scene module before decoding its bodies or exporting its
    schema. Registering a target never imports other targets or changes the core
    envelope. Fingerprints include declared assets and pagers, but exclude bytes
    and process-local callbacks.
    """

    protocol = 1
    _dialects: ClassVar[dict[str, SceneDialect[Any]]] = {}
    _bodies: ClassVar[dict[str, SceneDialect[Any]]] = {}

    @classmethod
    def register[B: Body](cls, dialect: SceneDialect[B]) -> None:
        """Register a target vocabulary, rejecting ambiguous IDs and body tags."""
        if dialect.id in cls._dialects:
            if cls._dialects[dialect.id] is dialect:
                return
            message = f"scene dialect {dialect.id!r} is already registered"
            raise ValueError(message)
        kinds = [body.KIND for body in dialect.bodies]
        if len(kinds) != len(set(kinds)) or any(kind in cls._bodies for kind in kinds):
            message = f"scene dialect {dialect.id!r} reuses a body kind"
            raise ValueError(message)
        query_types = cls.query_types()
        if any(name in query_types and query_types[name] is not kind for name, kind in dialect.query_types.items()):
            message = f"scene dialect {dialect.id!r} reuses a selector type name"
            raise ValueError(message)
        erased = cast(SceneDialect[Any], dialect)
        cls._dialects[dialect.id] = erased
        cls._bodies.update((kind, erased) for kind in kinds)

    @classmethod
    def query_types(cls) -> dict[str, type]:
        return {name: kind for dialect in cls._dialects.values() for name, kind in dialect.query_types.items()}

    @classmethod
    def schema(cls) -> dict[str, Any]:
        """Return an isolated JSON Schema for the registered target vocabularies."""
        return SceneSchema.envelope(tuple(dialect.schema() for _, dialect in sorted(cls._dialects.items())))

    @classmethod
    def schema_json(cls, *, indent: int | None = None) -> str:
        return json.dumps(
            cls.schema(), sort_keys=True, indent=indent, separators=(",", ":") if indent is None else None
        )

    @classmethod
    def dumps(cls, scene: Scene[Any]) -> str:
        return json.dumps(cls.to_dict(scene), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def loads(cls, payload: str) -> Scene:
        try:
            raw = json.loads(payload, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError) as error:
            raise CodecError(str(error)) from error
        if not isinstance(raw, dict):
            message = "scene must be a JSON object"
            raise CodecError(message)
        return cls.from_dict(raw)

    @classmethod
    def fingerprint(cls, scene: Scene[Any]) -> str:
        return hashlib.blake2s(cls.dumps(scene).encode(), digest_size=16).hexdigest()

    @classmethod
    def to_dict(cls, scene: Scene[Any]) -> dict[str, Any]:
        if scene.protocol != cls.protocol:
            message = f"unsupported scene protocol {scene.protocol}"
            raise CodecError(message)
        dialect = cls._bodies.get(scene.body.KIND)
        if dialect is None or type(scene.body) not in dialect.bodies:
            message = f"unregistered scene body {type(scene.body).__name__!r}"
            raise CodecError(message)
        return {
            "protocol": scene.protocol,
            "target": scene.target,
            "target_version": scene.target_version,
            "body": dialect.encode(scene.body),
            "assets": [asset.model_dump(mode="json") for asset in scene.assets],
            "pagers": [pager.model_dump(mode="json") for pager in scene.pagers],
        }

    @classmethod
    def from_dict(cls, raw: JsonObject) -> Scene:
        protocol = _json.integer(raw, "protocol")
        if protocol != cls.protocol:
            message = f"unsupported scene protocol {protocol}"
            raise CodecError(message)
        assets, pagers = raw.get("assets", []), raw.get("pagers", [])
        if not isinstance(assets, list) or not isinstance(pagers, list):
            message = "scene assets and pagers must be arrays"
            raise CodecError(message)
        try:
            declared_assets = tuple(Asset.model_validate(_json.require_object(asset)) for asset in assets)
            declared_pagers = tuple(Pager.model_validate(_json.require_object(pager)) for pager in pagers)
        except ValidationError as error:
            raise CodecError(str(error)) from error
        body = _json.require_object(raw.get("body"))
        kind = _json.string(body, "kind")
        dialect = cls._bodies.get(kind)
        if dialect is None:
            message = f"unknown scene body kind {kind!r}"
            raise CodecError(message)
        return Scene(
            protocol,
            _json.string(raw, "target"),
            _json.integer(raw, "target_version"),
            dialect.decode(body),
            declared_assets,
            declared_pagers,
        )
