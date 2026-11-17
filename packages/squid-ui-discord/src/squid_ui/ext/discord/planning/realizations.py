"""Resolved native Discord realizations interpreted by Discord composition."""

from dataclasses import dataclass, replace

from squid_ui.ext.discord import scene
from squid_ui.ext.discord.planning.native_extensions import NativeComposition
from squid_ui.ext.discord.primitives import nodes as primitive
from squid_ui.native import NativeRealization
from squid_ui.planning.composition import SharedUse
from squid_ui.planning.realizations import Realization


@dataclass(frozen=True, slots=True)
class NativeSectionRealization(NativeRealization):
    pass


@dataclass(frozen=True, slots=True)
class ThumbnailRealization(NativeRealization):
    node: scene.Thumbnail


@dataclass(frozen=True, slots=True)
class SeparatorRealization(NativeRealization):
    node: scene.Separator


@dataclass(frozen=True, slots=True)
class FileRealization(NativeRealization):
    node: scene.File


@dataclass(frozen=True, slots=True)
class PremiumRealization(NativeRealization):
    sku_id: int


@dataclass(frozen=True, slots=True)
class ClassicContentRealization(NativeRealization):
    declarations: int


@dataclass(frozen=True, slots=True)
class CardRealization(NativeRealization):
    node: primitive.Card


@dataclass(frozen=True, slots=True)
class OpaqueRealization(NativeRealization):
    node: scene.Extension
    resource: SharedUse[object]
    composition: NativeComposition


@dataclass(frozen=True, slots=True)
class GalleryRealization(NativeRealization):
    items: tuple[scene.GalleryItem, ...]
    collection: bool = False

    def fragment(self, start: int, end: int) -> Realization:
        return replace(self, items=self.items[start:end])
