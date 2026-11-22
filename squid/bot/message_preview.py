"""Composable Discord source-message previews."""

from collections.abc import Sequence

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid_i18n import Text
from squid_ui.ext.discord.target_types import ComponentsV2Target


def message_preview(
    *,
    content: sl.TextLike | None = None,
    author: sl.TextLike | None = None,
    avatar_url: str | None = None,
    reply: sl.TextLike | None = None,
    media: Sequence[str] = (),
    jump_url: str | None = None,
    jump_label: sl.TextLike = "Original Discord message",
) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
    """Compose a preview from source details the destination is allowed to show."""
    children: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = []
    if author is not None or content is not None:
        body = Text.join("\n", (part for part in (author, content) if part is not None))
        body_node = sd.v2.text(body)
        children.append(sd.v2.section(body_node, accessory=sd.v2.thumbnail(avatar_url)) if avatar_url else body_node)
    if reply is not None:
        children.append(sd.v2.text(reply))
    if media:
        children.append(sd.v2.gallery(*media[:10]))
    if jump_url is not None:
        children.append(sd.v2.row(sd.v2.link_button(jump_label, jump_url)))
    return tuple(children)
