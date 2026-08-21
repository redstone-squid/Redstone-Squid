"""Stable logical identities shared by planners and local region slicers."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from enum import Enum


def stable_fingerprint(values: Sequence[object]) -> str:
    """Hash logical structure without callback identity or process addresses."""
    payload = json.dumps(stable_value(values), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2s(payload.encode(), digest_size=16).hexdigest()


def stable_value(value: object) -> object:
    """Reduce a logical value to deterministic JSON-compatible data."""
    if callable(value):
        return "<callback>"
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__qualname__,
            "fields": {item.name: stable_value(getattr(value, item.name)) for item in fields(value)},
        }
    if isinstance(value, Mapping):
        return {str(key): stable_value(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [stable_value(item) for item in value]
    if isinstance(value, bytes):
        return hashlib.blake2s(value, digest_size=16).hexdigest()
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return type(value).__qualname__
