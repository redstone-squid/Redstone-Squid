"""Turning a plan's declarative assets into Discord files.

Shared by every path that can carry an attachment — a mount's delivery, a sessionless
composition, and a fragment contributed to someone else's view — so a file behaves the same
way wherever it was planned.
"""

import io
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

import discord

from squid_layouts.assets import Asset, InlineAsset, StoredAsset
from squid_layouts.scene.model import (
    PlanResult,
    SceneClassicMessage,
    SceneComponentsV2,
    SceneDocument,
    SceneFile,
    SceneNode,
    ScenePanel,
)


def scene_nodes(scene: SceneDocument) -> tuple[SceneNode, ...]:
    """Every drawable node in a scene, whichever kind of message it resolved to."""
    match scene.body:
        case SceneComponentsV2(children=children):
            return children
        case SceneClassicMessage(rows=rows):
            # A classic body's text lives in embeds, which cannot reference a file; only its
            # controls can carry a link, and only rows hold controls.
            return tuple(control for row in rows for control in row.controls)


def attachment_assets(plan: PlanResult) -> tuple[Asset, ...]:
    """The assets a plan expects to be uploaded, excluding ones already served by URL."""
    linked = linked_file_assets(scene_nodes(plan.scene), plan.resources)
    return tuple(
        asset
        for scene_asset in plan.scene.assets
        if isinstance(asset := plan.resources.get(f"asset:{scene_asset.key}"), Asset) and scene_asset.key not in linked
    )


def files_for(assets: Sequence[Asset]) -> list[discord.File]:
    """Materialize fresh `discord.File` wrappers; a sent file cannot be re-sent."""
    files: list[discord.File] = []
    for asset in assets:
        if not isinstance(asset.source, InlineAsset):
            message = f"Discord delivery needs a host resolver for stored asset {asset.key!r}"
            raise TypeError(message)
        files.append(discord.File(io.BytesIO(asset.source.data), filename=asset.name))
    return files


def linked_file_assets(nodes: Sequence[SceneNode], resources: Mapping[str, object]) -> frozenset[str]:
    """Asset keys the scene already references by URL, which cost no attachment slot."""
    linked: set[str] = set()
    for node in nodes:
        if isinstance(node, ScenePanel):
            linked.update(linked_file_assets(node.children, resources))
            continue
        if not isinstance(node, SceneFile):
            continue
        resource = resources.get(f"asset:{node.asset_key}")
        if not isinstance(resource, Asset) or not isinstance(resource.source, StoredAsset):
            continue
        parsed = urlsplit(resource.source.reference)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            linked.add(node.asset_key)
    return frozenset(linked)
