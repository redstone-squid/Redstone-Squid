"""Fingerprint layout content for planning caches and remembered page positions.

Equivalent authored values should keep their identity across renders even when
their Python objects and callbacks are new. Fingerprints therefore describe
dataclass fields and container contents, replacing callbacks with a fixed marker.
They identify content, not which live handler should receive an event.
"""

import hashlib
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from string.templatelib import Template

from squid_ui.content.text import Text
from squid_ui.planning.identity_cache import IdentityCache
from squid_ui.primitives.constraints import RoutedPage
from squid_ui.primitives.nodes import Extension, RawItem


@dataclass(frozen=True, slots=True)
class _Digest:
    value: bytes
    weight: int
    immutable: bool


class FingerprintCache(IdentityCache[_Digest]):
    """Retain immutable subtree digests; admission is decided during traversal."""

    def __init__(self, *, capacity: int = 4096, max_bytes: int = 4 * 1024 * 1024) -> None:
        super().__init__(capacity=capacity, max_bytes=max_bytes)


def stable_fingerprint(
    values: Sequence[object], *, consume: Callable[[], None] | None = None, cache: FingerprintCache | None = None
) -> str:
    """Hash logical structure using child digests, with optional immutable-subtree reuse.

    Hashes identify content, never authority or callback equivalence. Cached and cold
    calls produce the same digest. The digest format is internal, not a scene protocol.
    """
    return _digest(values, consume, cache).value.hex()


def _digest(value: object, consume: Callable[[], None] | None, cache: FingerprintCache | None) -> _Digest:
    if consume is not None:
        consume()
    if cache is not None and (cached := cache.get(value)) is not None:
        return cached
    original = value
    immutable = True
    parts: list[bytes] = []
    weight = sys.getsizeof(value) + 128

    def child(item: object) -> None:
        nonlocal immutable, weight
        result = _digest(item, consume, cache)
        parts.append(result.value)
        weight += result.weight
        immutable &= result.immutable

    if callable(value):
        tag = b"callback"
        immutable = False
    elif isinstance(value, Enum):
        tag = b"enum"
        immutable = False
        child(value.value)
    elif isinstance(value, Template | Text):
        tag = b"text"
        immutable = type(value) is Text
        child(Text(value).structural_key())
    elif is_dataclass(value) and not isinstance(value, type):
        tag = b"record:" + type(value).__qualname__.encode()
        immutable = bool(getattr(getattr(type(value), "__dataclass_params__", None), "frozen", False)) and not hasattr(
            value, "__dict__"
        )
        for item in sorted(fields(value), key=lambda item: item.name):
            if item.metadata.get("stable_identity", True):
                parts.append(item.name.encode())
                child(getattr(value, item.name))
            else:
                # Omitted fields may retain live values even in a frozen dataclass.
                immutable = False
    elif isinstance(value, Mapping):
        tag = b"mapping"
        immutable = False
        for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
            parts.append(str(key).encode())
            child(item)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        tag = b"sequence"
        immutable = type(value) is tuple
        for item in value:
            child(item)
    elif isinstance(value, bytes):
        tag = b"bytes"
        immutable = type(value) is bytes
        parts.append(value)
    elif isinstance(value, str | int | float | bool) or value is None:
        tag = b"scalar"
        immutable = type(value) in (str, int, float, bool, type(None))
        parts.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    else:
        tag = b"opaque:" + type(value).__qualname__.encode()
        immutable = False
    digest = hashlib.blake2s(digest_size=16)
    for part in (tag, *parts):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    result = _Digest(digest.digest(), weight, immutable)
    if cache is not None and result.immutable:
        cache.put(original, result, weight=result.weight)
    return result


def requires_live_preparation(value: object) -> bool:
    """Whether routes or opaque factories can change output behind stable identity."""
    return _contains(value, (RoutedPage, Extension, RawItem))


def has_routed_page(value: object) -> bool:
    """Whether an authored tree contains external page-route factories."""
    return _contains(value, (RoutedPage,))


def _contains(value: object, kinds: tuple[type, ...]) -> bool:
    if isinstance(value, kinds):
        return True
    if callable(value) or isinstance(value, str | bytes):
        return False
    if is_dataclass(value) and not isinstance(value, type):
        return any(_contains(getattr(value, item.name), kinds) for item in fields(value))
    if isinstance(value, Mapping):
        return any(_contains(item, kinds) for item in value.values())
    if isinstance(value, Sequence):
        return any(_contains(item, kinds) for item in value)
    return False
