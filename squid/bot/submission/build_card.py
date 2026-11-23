"""Compose build cards for browsing, editing, and publication in Discord.

Media previews are resolved before layout composition. Callers own destination
selection and wrap the returned node with their controls or message payload.
"""

import logging
import mimetypes
from collections.abc import Sequence
from urllib.parse import urlsplit

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.catalogue.schematics.presentation import schematic_caption
from squid.bot.message_preview import message_preview
from squid.bot.submission.attachment_display import attachment_issue_line
from squid.bot.submission.ui.attachment_review import review_attachments
from squid.bot.submission.ui.expandable_size import expandable_size_text
from squid.bot.ui import (
    truncate_display_text,
    user_inline_text,
    user_markdown_paragraph,
)
from squid.bot.utils.web import MediaPreviewClient
from squid.core.builds.application.ports import BuildSchematicSummary, BuildSchematicSummaryProvider
from squid.core.builds.domain import BuildStatus
from squid.core.builds.domain.models import AttachmentFailureInfo, SchematicDuplicateInfo
from squid.core.builds.domain.titles import format_build_display_title
from squid.core.builds.models import Build, DoorBuild, ExtenderBuild
from squid.core.messages.domain import MessageSnapshot
from squid.core.observability import add_counter
from squid.core.tags.domain import TagModerationStatus, TagSemanticKind
from squid_i18n import Text
from squid_ui.ext.discord.target_types import ComponentsV2Target

logger = logging.getLogger(__name__)

PENDING = sl.Role("build.pending", base=sl.Tone.WARNING, marker="⏳ ")
APPROVED = sl.Role("build.approved", base=sl.Tone.SUCCESS)
REJECTED = sl.Role("build.rejected", base=sl.Tone.DANGER)

_SPONSOR_CREDIT_MAX_CHARACTERS = 255
_SPONSOR_WEBSITE_MAX_CHARACTERS = 512
_EVIDENCE_FIELD_MAX_CHARACTERS = 1_000
_EVIDENCE_LINE_MAX_CHARACTERS = 240


def _compact_plain(value: str) -> str:
    return " ".join(value.split())


def _duplicate_summary(duplicates: Sequence[SchematicDuplicateInfo]) -> str:
    labels = {
        "identical": "byte-identical file",
        "structural-match": "same structure, moved or rotated",
        "near": "near structural match",
    }
    lines: list[str] = []
    for candidate in duplicates[:5]:
        build_id = candidate["build_id"]
        title = truncate_display_text(_compact_plain(candidate.get("title") or f"Build #{build_id}"), 100)
        sources = candidate.get("source_attachments", [])
        filenames = [truncate_display_text(_compact_plain(source["filename"]), 70) for source in sources[:2]]
        omitted = f" and {len(sources) - 2} more" if len(sources) > 2 else ""
        source = f" · from {', '.join(filenames)}{omitted}" if filenames else ""
        lines.append(
            truncate_display_text(
                f"{title} (#{build_id}) — {labels[candidate['tier']]}{source}",
                _EVIDENCE_LINE_MAX_CHARACTERS,
            )
        )
    if len(duplicates) > 5:
        lines.append(f"…and {len(duplicates) - 5} more")
    return truncate_display_text("\n".join(lines), _EVIDENCE_FIELD_MAX_CHARACTERS)


def _attachment_failure_summary(failures: Sequence[AttachmentFailureInfo]) -> str:
    lines = [
        truncate_display_text(
            attachment_issue_line(failure),
            _EVIDENCE_LINE_MAX_CHARACTERS,
        )
        for failure in failures[:4]
    ]
    if len(failures) > 4:
        lines.append(f"…and {len(failures) - 4} more")
    return truncate_display_text("\n".join(lines), _EVIDENCE_FIELD_MAX_CHARACTERS)


def _status_tone(status: BuildStatus | None, *, build_id: int | None) -> sl.Tone | sl.Role:
    """Return an exhaustive build-status accent, reporting invalid persisted state."""
    match status:
        case BuildStatus.PENDING:
            return PENDING
        case BuildStatus.APPROVED:
            return APPROVED
        case BuildStatus.REJECTED:
            return REJECTED
        case _:
            raw_status = status.name if isinstance(status, BuildStatus) else str(status)
            logger.error(
                "Build %s has no valid submission status",
                build_id,
                extra={"squid.build.id": build_id, "squid.build.status": raw_status},
            )
            add_counter(
                "squid.build.invalid_submission_status",
                attributes={"squid.build.status": raw_status},
            )
            return sl.Tone.NEUTRAL


async def render_build_card(
    build: Build,
    media_previews: MediaPreviewClient,
    *,
    schematic_summaries: BuildSchematicSummaryProvider | None = None,
) -> sl.LayoutNode[ComponentsV2Target, sd.Request]:
    """Resolve media and the public schematic summary before composing the card."""
    summary = None
    if (
        build.id is not None  # pyright: ignore[reportUnnecessaryComparison]
        and build.submission_status is BuildStatus.APPROVED
        and schematic_summaries is not None
    ):
        try:
            summary = await schematic_summaries.summary_for(build.id)
        except Exception:
            logger.warning("Could not read schematic measurements for build %s", build.id, exc_info=True)
    thumbnail, media = await _get_media_urls(build, media_previews)
    return build_card(build, thumbnail=thumbnail, media=media, schematic=summary)


def build_card(
    build: Build,
    *,
    thumbnail: str | None = None,
    media: Sequence[str] = (),
    schematic: BuildSchematicSummary | None = None,
) -> sl.LayoutNode[ComponentsV2Target, sd.Request]:
    """Compose the full card from a build and its resolved resources."""
    fields = build_metadata_fields(build)
    fallbacks = _field_ladders(build)
    restrictions = _build_restrictions(build)
    description = build.description
    return sl.section(
        sl.heading(user_inline_text(format_build_display_title(build, markdown=False))),
        restrictions and sl.paragraph(restrictions),
        description and user_markdown_paragraph(description),
        _field_section("Review warnings", fields, fallbacks, {"⚠ Possible duplicate", "⚠ Attachment issues"}),
        _attachment_review_action(build),
        _field_section(
            "Size & performance",
            fields,
            fallbacks,
            {
                "Dimensions",
                "Volume",
                "Opening Time",
                "Closing Time",
                "Visible Opening Time",
                "Visible Closing Time",
                "Extension Time",
                "Retraction Time",
            },
        ),
        _field_section("Compatibility", fields, fallbacks, {"Versions"}),
        _field_section("Tags", fields, fallbacks, {"Tags"}),
        _field_section("Credits", fields, fallbacks, {"Creators", "Date Of Completion", "Sponsoring Server"}),
        _build_resources(build, fields, fallbacks, schematic),
        bool(media) and sl.media(*media, key="media"),
        *_build_source(build),
        accent=_status_tone(build.submission_status, build_id=build.id),
        thumbnail=thumbnail,
    )


def _field_section(
    title: str,
    fields: dict[str, str],
    fallbacks: dict[str, tuple[str, ...]],
    names: set[str],
    *,
    leading: Sequence[sl.semantic.Field] = (),
) -> sl.LayoutNode[ComponentsV2Target, sd.Request] | None:
    entries = tuple(
        sl.field(name, value, fallbacks=fallbacks.get(name, ())) for name, value in fields.items() if name in names
    )
    return sl.section(sl.heading(title), sl.fields(*leading, *entries)) if leading or entries else None


def _build_resources(
    build: Build,
    fields: dict[str, str],
    fallbacks: dict[str, tuple[str, ...]],
    schematic: BuildSchematicSummary | None,
) -> sl.LayoutNode[ComponentsV2Target, sd.Request] | None:
    names = {"Server", "Coordinates", "Command", "World Download", "Schematic", "Videos", "Images", "Sponsor Website"}
    if schematic is None:
        return _field_section("Resources", fields, fallbacks, names)
    multiple = schematic.multiple_files or len(build.schematic_urls) > 1
    caption, download = schematic_caption(
        schematic.width,
        schematic.height,
        schematic.length,
        filename=schematic.filename if multiple else None,
        download_url=schematic.download_url,
    )
    featured = sl.field("Schematic", caption, fallbacks=(download,))
    if "Schematic" in fields:
        fields = {"Submitted schematic links" if name == "Schematic" else name: value for name, value in fields.items()}
        fallbacks = {
            "Submitted schematic links" if name == "Schematic" else name: values for name, values in fallbacks.items()
        }
        names.add("Submitted schematic links")
    return _field_section("Resources", fields, fallbacks, names, leading=(featured,))


def _attachment_review_action(build: Build) -> sl.LayoutNode[ComponentsV2Target, sd.Request] | None:
    if (
        build.id is not None
        and build.submission_status is BuildStatus.APPROVED
        and (build.extra_info.get("attachment_failures") or build.extra_info.get("attachment_resolutions"))
    ):
        return sl.action_controls(
            sl.routed_action_control(
                "Review attachments", review_attachments.custom_id(build_id=build.id), key="review"
            ),
            key="attachment-review",
        )
    return None


def _build_source(build: Build) -> tuple[sl.LayoutNode[ComponentsV2Target, sd.Request], ...]:
    # A draft preview renders an unsaved build, which has no ID yet.
    footer = (
        f"Submission ID: {build.id}"
        if build.id is not None  # pyright: ignore[reportUnnecessaryComparison]
        else "Preview · not submitted yet"
    )
    if build.edited_time is not None:
        footer += f" • Updated <t:{build.edited_time.timestamp()}:R>"
    source = next(
        (
            message
            for message in build.source_messages
            if message.channel_id is not None and message.guild_id is not None
        ),
        None,
    )
    content = source.content if isinstance(source, MessageSnapshot) else build.source_message_content
    content = _compact_plain(content) if content else ""
    excerpt = sl.plain(truncate_display_text(content, 300)) if content else None
    preview = message_preview(content=excerpt, jump_url=source.link if source else None)
    return (sl.note(sl.plain(footer)), *preview)


def _field_ladders(build: Build) -> dict[str, tuple[str, ...]]:
    """Offer shorter field values when the full card does not fit.

    URL lists fall back to the first link and then a count, preserving complete
    links instead of truncating them in the middle. Long creator lists use a count.
    """
    ladders: dict[str, tuple[str, ...]] = {}
    for name, urls in (
        ("World Download", build.world_download_urls),
        ("Schematic", build.schematic_urls),
        ("Videos", build.video_urls),
        ("Images", build.image_urls),
    ):
        if len(urls) > 1:
            ladders[name] = (f"{len(urls)} links — first: {urls[0]}", f"{len(urls)} links")
    creators = sorted(build.creators_ign)
    if len(creators) > 3:
        ladders["Creators"] = (f"{creators[0]} and {len(creators) - 1} others",)
    return ladders


async def _get_media_urls(build: Build, media_previews: MediaPreviewClient) -> tuple[str | None, list[str]]:
    """Choose an image thumbnail and retain the remaining images and direct video files."""
    images: list[str] = []
    video_previews: list[str] = []
    videos: list[str] = []
    for url in (*build.image_urls, *build.video_urls):
        parsed = urlsplit(url)
        mimetype, _ = mimetypes.guess_type(parsed.path)
        if mimetype is not None and mimetype.startswith("image/"):
            images.append(url)
            continue
        if mimetype is not None and mimetype.startswith("video/"):
            videos.append(url)
            continue
        try:
            preview = await media_previews.get(url)
        except Exception:
            logger.warning("Could not resolve a build media preview", exc_info=True)
            continue
        image = preview["image"]
        if isinstance(image, str):
            (images if url in build.image_urls else video_previews).append(image)
        if video := preview["video"]:
            videos.append(video)

    # Page URLs are not valid image sources. Keep unresolved links in Resources.
    # Prefer submitted images or a video preview over a generated render for the thumbnail.
    images = list(dict.fromkeys((*(images or video_previews[:1]), *build.render_urls)))
    return (images[0] if images else None), list(dict.fromkeys((*images[1:], *videos)))


def _build_restrictions(build: Build) -> Text | None:
    """Describe locational and directional restrictions when present."""
    desc: list[sl.TextLike] = []

    if "Locational" in build.miscellaneous_restrictions:
        desc.append(sl.md("**Locational**."))
    elif "Locational with fixes" in build.miscellaneous_restrictions:
        desc.append(sl.md("**Locational** with known fixes for each location."))

    if "Directional" in build.miscellaneous_restrictions:
        desc.append(sl.md("**Directional**."))
    elif "Directional with fixes" in build.miscellaneous_restrictions:
        desc.append(sl.md("**Directional** with known fixes for each direction."))

    return Text.join("\n", desc) if desc else None


def build_metadata_fields(build: Build) -> dict[str, str]:
    """Format build facts as display labels and values.

    Callers must escape values for their output format. Timing values are shown
    in seconds; absent dimensions use question marks.
    """
    fields = {"Dimensions": f"{build.width or '?'} x {build.height or '?'} x {build.depth or '?'}"}

    if build.width and build.height and build.depth:
        fields["Volume"] = str(build.width * build.height * build.depth)

    # The times are stored as game ticks, so they need to be divided by 20 to get seconds
    if isinstance(build, DoorBuild):
        if build.expansion is not None:
            fields["Expandable Size"] = expandable_size_text(build.expansion).resolve()
        if build.normal_opening_time:
            fields["Opening Time"] = f"{build.normal_opening_time / 20}s"
        if build.normal_closing_time:
            fields["Closing Time"] = f"{build.normal_closing_time / 20}s"
        if build.visible_opening_time:
            fields["Visible Opening Time"] = f"{build.visible_opening_time / 20}s"
        if build.visible_closing_time:
            fields["Visible Closing Time"] = f"{build.visible_closing_time / 20}s"
    if isinstance(build, ExtenderBuild):
        if build.extension_time:
            fields["Extension Time"] = f"{build.extension_time / 20}s"
        if build.retraction_time:
            fields["Retraction Time"] = f"{build.retraction_time / 20}s"

    if build.credits:
        fields["Creators"] = ", ".join(
            credit.name if credit.role.value == "creator" else f"{credit.name} ({credit.role.value})"
            for credit in build.credits
        )

    if build.completion_time:
        fields["Date Of Completion"] = build.completion_time

    if sponsor := build.sponsor:
        credit = sponsor.display_name or sponsor.address or "Public Paper server"
        fields["Sponsoring Server"] = truncate_display_text(credit, _SPONSOR_CREDIT_MAX_CHARACTERS)
        if sponsor.website_url is not None:
            fields["Sponsor Website"] = truncate_display_text(
                sponsor.website_url,
                _SPONSOR_WEBSITE_MAX_CHARACTERS,
            )

    fields["Versions"] = build.version_spec or "Unknown"

    showcase = [
        assignment.rendered
        for assignment in build.tags
        if assignment.definition.semantic_kind is TagSemanticKind.SHOWCASE
        and assignment.definition.moderation_status is TagModerationStatus.APPROVED
        and not assignment.definition.withdrawn
    ]
    if showcase:
        fields["Tags"] = ", ".join(showcase)

    if build.server_address:
        fields["Server"] = build.server_address
        if build.server_coordinates:
            fields["Coordinates"] = build.server_coordinates
        if build.server_command:
            fields["Command"] = build.server_command

    if build.world_download_urls:
        fields["World Download"] = ", ".join(build.world_download_urls)
    if build.schematic_urls:
        fields["Schematic"] = ", ".join(build.schematic_urls)
    if build.image_urls:
        fields["Images"] = ", ".join(build.image_urls)
    if build.video_urls:
        fields["Videos"] = ", ".join(build.video_urls)

    if duplicates := build.extra_info.get("schematic_duplicates"):
        fields["⚠ Possible duplicate"] = _duplicate_summary(duplicates)
    if failures := build.extra_info.get("attachment_failures"):
        fields["⚠ Attachment issues"] = _attachment_failure_summary(failures)

    return fields
