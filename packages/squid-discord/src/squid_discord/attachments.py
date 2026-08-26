"""Turning a plan's declarative assets into Discord files.

Shared by every path that can carry an attachment — a mount's delivery, a sessionless
composition, and a fragment contributed to someone else's view — so a file behaves the same
way wherever it was planned.
"""

import io
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit

import discord

from squid_ui import scene
from squid_ui.assets import Asset, InlineAsset, StoredAsset
from squid_ui.scene.model import PlanResult


def scene_nodes(document: scene.Document) -> tuple[scene.Node, ...]:
    """Every drawable node in a scene, whichever kind of message it resolved to."""
    match document.body:
        case scene.ComponentsV2(children=children):
            return children
        case scene.ClassicMessage(rows=rows):
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


def linked_file_assets(nodes: Sequence[scene.Node], resources: Mapping[str, object]) -> frozenset[str]:
    """Asset keys the scene already references by URL, which cost no attachment slot."""
    linked: set[str] = set()
    for node in nodes:
        if isinstance(node, scene.Panel):
            linked.update(linked_file_assets(node.children, resources))
            continue
        if not isinstance(node, scene.File):
            continue
        resource = resources.get(f"asset:{node.asset_key}")
        if not isinstance(resource, Asset) or not isinstance(resource.source, StoredAsset):
            continue
        parsed = urlsplit(resource.source.reference)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            linked.add(node.asset_key)
    return frozenset(linked)
