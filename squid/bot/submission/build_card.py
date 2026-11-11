"""Compose build cards for browsing, editing, and publication in Discord.

Media previews are resolved before layout composition. Callers own destination
selection and wrap the returned node with their controls or message payload.
"""

import logging
import mimetypes
from collections.abc import Sequence

from discord.utils import escape_markdown

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.ui import (
    DISCORD_GREEN,
    DISCORD_GREY,
    DISCORD_RED,
    DISCORD_YELLOW,
    truncate_display_text,
)
from squid.bot.utils.web import MediaPreviewClient
from squid.builds.domain import Status
from squid.builds.domain.models import AttachmentFailureInfo, SchematicDuplicateInfo
from squid.builds.domain.titles import format_build_display_title
from squid.builds.models import Build, DoorBuild
from squid.observability import add_counter

logger = logging.getLogger(__name__)

_SPONSOR_CREDIT_MAX_CHARACTERS = 255
_SPONSOR_WEBSITE_MAX_CHARACTERS = 512
_EVIDENCE_FIELD_MAX_CHARACTERS = 1_000
_EVIDENCE_LINE_MAX_CHARACTERS = 240


def _compact_plain(value: str) -> str:
    return escape_markdown(" ".join(value.replace("@", "@\u200b").split()))


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
            f"{_compact_plain(failure['filename'])} — {_compact_plain(failure['detail'])}",
            _EVIDENCE_LINE_MAX_CHARACTERS,
        )
        for failure in failures[:4]
    ]
    if len(failures) > 4:
        lines.append(f"…and {len(failures) - 4} more")
    return truncate_display_text("\n".join(lines), _EVIDENCE_FIELD_MAX_CHARACTERS)


def _status_colour(status: Status | None, *, build_id: int | None) -> int:
    """Return an exhaustive build-status accent, reporting invalid persisted state."""
    match status:
        case Status.PENDING:
            return DISCORD_YELLOW
        case Status.CONFIRMED:
            return DISCORD_GREEN
        case Status.DENIED:
            return DISCORD_RED
        case _:
            raw_status = status.name if isinstance(status, Status) else str(status)
            logger.error(
                "Build %s has no valid submission status",
                build_id,
                extra={"squid.build.id": build_id, "squid.build.status": raw_status},
            )
            add_counter(
                "squid.build.invalid_submission_status",
                attributes={"squid.build.status": raw_status},
            )
            return DISCORD_GREY


async def render_build_card(
    build: Build, media_previews: MediaPreviewClient
) -> sl.LayoutNode[sl.ComponentsV2Target, sd.Request]:
    """Return a build card that callers can compose with other layout nodes."""
    metadata = build_metadata_fields(build)
    performance_names = {
        "Dimensions",
        "Volume",
        "Opening Time",
        "Closing Time",
        "Visible Opening Time",
        "Visible Closing Time",
    }
    resource_names = {
        "Server",
        "Coordinates",
        "Command",
        "World Download",
        "Schematic",
        "Videos",
        "Sponsor Website",
    }
    credit_names = {"Creators", "Date Of Completion", "Sponsoring Server"}
    review_names = {"⚠ Possible duplicate", "⚠ Attachment issues"}

    ladders = _field_ladders(build)

    # Give each field its own shorter alternatives so one long value does not
    # force every field in the section to shrink.
    def group(title: str, names: set[str]) -> sl.LayoutNode | None:
        entries = tuple(
            sl.field(
                name,
                escape_markdown(value),
                fallbacks=tuple(escape_markdown(alt) for alt in ladders.get(name, ())),
            )
            for name, value in metadata.items()
            if name in names
        )
        return sl.section(sl.heading(title), sl.fields(*entries)) if entries else None

    # A draft preview renders an unsaved build, which has no ID yet.
    footer = f"Submission ID: {build.id}" if build.id is not None else "Preview · not submitted yet"
    if build.edited_time is not None:
        footer += f" • Updated <t:{build.edited_time.timestamp()}:R>"
    rows = ()
    if build.original_link is not None:
        rows = (
            sl.action_controls(
                sl.link("Original submission", build.original_link, key="original-submission"),
                key="submission-links",
            ),
        )
    description = build_description(build)
    media = await _get_media_urls(build, media_previews)
    extra_media = media[1:]
    return sl.section(
        sl.heading(format_build_display_title(build, markdown=True)),
        # Allow the description to shrink when the full card exceeds the layout budget.
        description and sl.paragraph(description).truncate(),
        group("Review warnings", review_names),
        group("Size & performance", performance_names),
        group("Compatibility", {"Versions"}),
        group("Credits", credit_names),
        group("Resources", resource_names),
        bool(extra_media) and sl.media(*extra_media, key="media"),
        sl.note(footer),
        *rows,
        accent=_status_colour(build.submission_status, build_id=build.id),
        thumbnail=media[0] if media else None,
    )


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
    ):
        if len(urls) > 1:
            ladders[name] = (f"{len(urls)} links — first: {urls[0]}", f"{len(urls)} links")
    creators = sorted(build.creators_ign)
    if len(creators) > 3:
        ladders["Creators"] = (f"{creators[0]} and {len(creators) - 1} others",)
    return ladders


async def _get_media_urls(build: Build, media_previews: MediaPreviewClient) -> list[str]:
    media: list[str] = []
    for url in build.image_urls:
        mimetype, _ = mimetypes.guess_type(url)
        if mimetype is not None and mimetype.startswith("image"):
            media.append(url)
            continue
        try:
            preview = await media_previews.get(url)
        except Exception:
            logger.warning("Could not resolve a build media preview", exc_info=True)
            continue
        image = preview["image"]
        if isinstance(image, str):
            media.append(image)

    if not media:
        for url in build.video_urls:
            try:
                preview = await media_previews.get(url)
            except Exception:
                logger.warning("Could not resolve a build video preview", exc_info=True)
                continue
            image = preview["image"]
            if isinstance(image, str):
                media.append(image)
                break

    # Prefer submitted images or a video preview over a generated render for the thumbnail.
    media.extend(build.render_urls)

    return media[:10]


def build_description(build: Build) -> str | None:
    """Describe locational/directional restrictions and escaped submitter notes, or return None."""
    desc = []

    if "Locational" in build.miscellaneous_restrictions:
        desc.append("**Locational**.")
    elif "Locational with fixes" in build.miscellaneous_restrictions:
        desc.append("**Locational** with known fixes for each location.")

    if "Directional" in build.miscellaneous_restrictions:
        desc.append("**Directional**.")
    elif "Directional with fixes" in build.miscellaneous_restrictions:
        desc.append("**Directional** with known fixes for each direction.")

    if build.description:
        desc.append("\n" + escape_markdown(build.description))

    return "\n".join(desc) if desc else None


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
        if build.normal_opening_time:
            fields["Opening Time"] = f"{build.normal_opening_time / 20}s"
        if build.normal_closing_time:
            fields["Closing Time"] = f"{build.normal_closing_time / 20}s"
        if build.visible_opening_time:
            fields["Visible Opening Time"] = f"{build.visible_opening_time / 20}s"
        if build.visible_closing_time:
            fields["Visible Closing Time"] = f"{build.visible_closing_time / 20}s"

    if build.creators_ign:
        fields["Creators"] = ", ".join(sorted(build.creators_ign))

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

    server_info = build.extra_info.get("server_info", {})
    if ip := server_info.get("server_ip"):
        fields["Server"] = ip
        if coordinates := server_info.get("coordinates"):
            fields["Coordinates"] = coordinates
        if command := server_info.get("command_to_build"):
            fields["Command"] = command

    if build.world_download_urls:
        fields["World Download"] = ", ".join(build.world_download_urls)
    if build.schematic_urls:
        fields["Schematic"] = ", ".join(build.schematic_urls)
    if build.video_urls:
        fields["Videos"] = ", ".join(build.video_urls)

    if duplicates := build.extra_info.get("schematic_duplicates"):
        fields["⚠ Possible duplicate"] = _duplicate_summary(duplicates)
    if failures := build.extra_info.get("attachment_failures"):
        fields["⚠ Attachment issues"] = _attachment_failure_summary(failures)

    return fields
