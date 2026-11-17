"""Build scene schemas from declarations supplied by target packages."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter

from squid_ui.scene.model import Asset, Pager


@dataclass(frozen=True, slots=True)
class SceneSchema:
    tagged: tuple[tuple[type, str, str], ...]
    bodies: tuple[type, ...]
    plain: tuple[tuple[str, Any], ...] = ()
    required: Mapping[type, tuple[str, ...]] = field(default_factory=dict)
    unions: Mapping[str, tuple[type, ...]] = field(default_factory=dict)
    renames: Mapping[type, Mapping[str, str]] = field(default_factory=dict)

    def fragment(self) -> dict[str, Any]:
        definitions: dict[str, Any] = {}
        tagged: dict[str, Any] = {}
        for model, name, kind in self.tagged:
            generated = TypeAdapter(model).json_schema(ref_template="#/$defs/{model}")
            definitions.update(generated.pop("$defs", {}))
            if "$ref" in generated:
                generated = definitions[model.__name__]
            properties = dict(generated["properties"])
            for old, new in self.renames.get(model, {}).items():
                properties[new] = properties.pop(old)
            contract = {
                "type": "object",
                "additionalProperties": False,
                "properties": {"kind": {"const": kind}, **properties},
                "required": ["kind", *properties],
            }
            definitions[name] = contract
            tagged[model.__name__] = contract
        definitions.update(tagged)
        for name, model in self.plain:
            generated = TypeAdapter(model).json_schema(ref_template="#/$defs/{model}")
            definitions.update(generated.pop("$defs", {}))
            definitions[name] = generated
        definitions.update(tagged)
        for model, required in self.required.items():
            definition = definitions[model.__name__]
            definition["additionalProperties"] = False
            definition["required"] = list(required)
        for name, model in self.plain:
            if isinstance(model, type) and model in self.required:
                definitions[name] = definitions[model.__name__]
        definitions.update(
            {
                name: {"oneOf": [{"$ref": f"#/$defs/{model.__name__}"} for model in models]}
                for name, models in self.unions.items()
            }
        )
        return {"$defs": definitions, "bodies": [model.__name__ for model in self.bodies]}

    @staticmethod
    def envelope(fragments: tuple[dict[str, Any], ...]) -> dict[str, Any]:
        definitions: dict[str, Any] = {}
        bodies: list[str] = []
        for fragment in fragments:
            for name, definition in deepcopy(fragment["$defs"]).items():
                if name in definitions and definitions[name] != definition:
                    message = f"scene schema definition {name!r} has conflicting declarations"
                    raise ValueError(message)
                definitions[name] = definition
            bodies.extend(fragment["bodies"])
        for name, model in (("asset", Asset), ("pager", Pager)):
            generated = TypeAdapter(model).json_schema(ref_template="#/$defs/{model}")
            definitions.update(generated.pop("$defs", {}))
            definitions[name] = generated
        definitions["body"] = {"oneOf": [{"$ref": f"#/$defs/{name}"} for name in bodies]} if bodies else {"not": {}}
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://redstone-squid.github.io/Redstone-Squid/schema/scene-v1.schema.json",
            "title": "squid-ui resolved scene protocol 1",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "protocol": {"const": 1},
                "target": {"type": "string"},
                "target_version": {"type": "integer", "minimum": 0},
                "body": {"$ref": "#/$defs/body"},
                "assets": {"type": "array", "items": {"$ref": "#/$defs/asset"}},
                "pagers": {"type": "array", "items": {"$ref": "#/$defs/pager"}},
            },
            "required": ["protocol", "target", "target_version", "body", "assets", "pagers"],
            "$defs": definitions,
        }
