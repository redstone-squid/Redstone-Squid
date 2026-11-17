"""Third-party scene vocabularies participate without core registration tables."""

from dataclasses import dataclass
from typing import ClassVar

import pytest

from squid_ui.scene import Body, Codec, CodecError, Scene
from squid_ui.scene.codec import SceneDialect


@dataclass(frozen=True, slots=True)
class CustomBody(Body):
    KIND: ClassVar[str] = "custom-test"
    text: str


def test_independent_dialect_round_trips_and_contributes_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Codec, "_dialects", {})
    monkeypatch.setattr(Codec, "_bodies", {})
    dialect = SceneDialect[CustomBody](
        "custom-test",
        (CustomBody,),
        lambda body: {"kind": body.KIND, "text": body.text},
        lambda raw: CustomBody(str(raw["text"])),
        lambda: {"$defs": {"CustomBody": {"type": "object"}}, "bodies": ["CustomBody"]},
    )
    original = Scene(1, "custom-test", 7, CustomBody("hello"))
    with pytest.raises(CodecError, match="unregistered scene body"):
        Codec.dumps(original)
    Codec.register(dialect)
    encoded = Codec.dumps(original)
    decoded = Codec.loads(encoded)
    assert decoded == original
    assert Codec.fingerprint(decoded) == Codec.fingerprint(original)
    assert Codec.schema()["$defs"]["body"] == {"oneOf": [{"$ref": "#/$defs/CustomBody"}]}
    assert "CustomBody" in Codec.schema()["$defs"]

    Codec.register(dialect)
    with pytest.raises(ValueError, match="reuses a body kind"):
        Codec.register(SceneDialect("other-test", (CustomBody,), dialect.encode, dialect.decode, dialect.schema))
