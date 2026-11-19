"""Private moderation of attachment warnings on saved builds."""

import asyncio
import hashlib
import io
import json
import logging
import mimetypes
import os
from functools import partial
from pathlib import Path
from typing import Self, override
from uuid import UUID

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.routes import AppRequest, ScreenUnavailable, router
from squid.bot.submission.attachment_display import attachment_issue_line
from squid.bot.utils.permissions import allows
from squid.builds.application import BuildEditPatch
from squid.builds.domain import Status
from squid.builds.models import Build
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT
from squid_ui.ext.discord.target_types import ComponentsV2Target

logger = logging.getLogger(__name__)


def archive_root() -> Path:
    return Path(os.environ.get("ATTACHMENT_REVIEW_ARCHIVE", "/var/lib/app/attachment-review"))


def warn_if_attachment_archive_unavailable() -> None:
    """Check archive access at startup without scanning the retained files."""
    root = archive_root()
    try:
        with (root / "retained-files.jsonl").open("rb") as manifest:
            manifest.read(1)
        with os.scandir(root / "retained-files") as files:
            next(files, None)
    except OSError as error:
        logger.warning(
            "Attachment review archive unavailable at %s: %s. "
            "Preview and publish require the archive. Set ATTACHMENT_REVIEW_ARCHIVE "
            "and include compose.attachment-review.yml when deploying with Compose.",
            root,
            error,
        )


def retained_bytes(identity: str, filename: str) -> bytes:
    """Read the verified archived original, addressed only by source UUID."""
    root = archive_root()
    key = str(UUID(identity))
    with (root / "retained-files.jsonl").open() as manifest:
        for line in manifest:
            entry = json.loads(line)
            if entry["source_id"] != key or not entry.get("retained") or entry["filename"] != filename:
                continue
            path = root / "retained-files" / key
            if entry["byte_size"] > 100_000_000:
                msg = "Archived original exceeds the 100 MB review limit"
                raise ValueError(msg)
            if path.stat().st_size != entry["byte_size"]:
                msg = "Archived original size does not match the manifest"
                raise ValueError(msg)
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() == entry["sha256"]:
                return data
            msg = "Archived original checksum does not match the manifest"
            raise ValueError(msg)
    msg = "No retained manifest entry matches the attachment ID and filename"
    raise ValueError(msg)


class AttachmentReview(sd.Screen[None]):
    policy = sd.ResponseSpec(audience="personal", timeout=900)
    build: Build | None = sl.state(None, opaque=True)
    index: int = sl.state(0)

    def __init__(self, request: AppRequest) -> None:
        self.request = request

    @classmethod
    async def open(cls, request: AppRequest, build_id: int | None = None) -> Self:
        if not await allows(request, BUILD_SUBMISSION_EDIT):
            msg = "Build edit permission is required."
            raise ScreenUnavailable(msg)
        screen = cls(request)
        builds = request.client.services.builds
        if build_id is None:
            build_id = await builds.next_attachment_review(0)
        if build_id is not None:
            screen.build = await builds.get(build_id)
            if screen.build is None or screen.build.submission_status is not Status.CONFIRMED:
                msg = "This build is unavailable for attachment review."
                raise ScreenUnavailable(msg)
        return screen

    @override
    def render(self) -> sl.DocumentLike[ComponentsV2Target, sd.Request]:
        build = self.build
        if build is None:
            return (sl.note("End of the attachment queue. Open /build issues to start again."),)
        failures = build.extra_info.get("attachment_failures", [])
        nodes: list[sl.LayoutNode[ComponentsV2Target, sd.Request]] = [
            sl.heading(f"Attachment review · #{build.id} {build.title}")
        ]
        if failures:
            item = failures[self.index]
            issue = attachment_issue_line(item)
            nodes.append(sl.paragraph(f"{self.index + 1}/{len(failures)} · {issue}").truncate())
            links = [
                sl.link(f"Source {message.id}", message.link, key=f"source-{message.id}")
                for message in build.source_messages
                if message.guild_id and message.link
            ]
            if links:
                nodes.append(sl.action_controls(*links, key="sources"))
            nodes.append(
                sl.action_controls(
                    sl.action_control(
                        "Preview original", partial(self._review, build, self.index, "preview", ""), key="preview"
                    ),
                    sl.action_control(
                        "Publish to Catbox and attach",
                        partial(self._review, build, self.index, "assign", ""),
                        key="assign",
                    ),
                    sl.form("Exclude from this build", ExcludeAttachment(self, build, self.index), key="exclude"),
                    sl.action_control(
                        "Other candidate", partial(self._candidate, item["attachment_id"]), key="candidate"
                    ),
                    len(failures) > 1 and sl.action_control("Next attachment", self._advance, key="advance"),
                    key="review-actions",
                )
            )
        else:
            nodes.append(sl.note("All attachments on this build have been reviewed."))
        decisions = build.extra_info.get("attachment_resolutions", [])
        if decisions:
            last = decisions[-1]
            decision = attachment_issue_line(last["issue"], resolution=last["reason"])
            nodes.extend(
                (
                    sl.note(f"Last decision · {decision}"),
                    sl.action_controls(
                        sl.action_control(
                            "Reopen last decision", partial(self._review, build, -1, "reopen", ""), key="reopen"
                        ),
                        key="history",
                    ),
                )
            )
        nodes.append(sl.action_controls(sl.action_control("Next build", self._next, key="next"), key="queue"))
        return nodes

    async def _candidate(self, attachment_id: str, event: sd.PressEvent) -> None:
        if not await allows(self.request, BUILD_SUBMISSION_EDIT):
            await event.notice("Build edit permission is required.")
            return
        assert self.build and self.build.id
        builds = self.request.client.services.builds
        ids: set[int] = set()
        for message in self.build.source_messages:
            ids.update(await builds.list_ids_for_source_message(message.id))
        current = self.build.id
        ids.discard(current)
        for identity in sorted(ids, key=lambda value: (value < current, value)):
            candidate = await builds.get(identity)
            if candidate and candidate.submission_status is Status.CONFIRMED:
                for index, issue in enumerate(candidate.extra_info.get("attachment_failures", [])):
                    if issue["attachment_id"] == attachment_id:
                        self.build = candidate
                        self.index = index
                        return
        await event.notice("No other confirmed build from these messages has this attachment awaiting review.")

    async def _advance(self, event: sl.PressEvent) -> None:
        assert self.build is not None
        self.index = (self.index + 1) % len(self.build.extra_info["attachment_failures"])

    async def _next(self, event: sl.PressEvent) -> None:
        if not await allows(self.request, BUILD_SUBMISSION_EDIT):
            await event.notice("Build edit permission is required.")
            return
        builds = self.request.client.services.builds
        identity = await builds.next_attachment_review(self.build.id or 0 if self.build else 0)
        self.build = await builds.get(identity) if identity else None
        self.index = 0

    async def _review(
        self, shown: Build, index: int, action: str, reason: str, event: sl.ActionEvent[sd.Request]
    ) -> None:
        request = self.request
        if not await allows(request, BUILD_SUBMISSION_EDIT):
            await event.notice("Build edit permission is required.")
            return
        builds = request.client.services.builds
        assert shown.id is not None
        build = await builds.get(shown.id)
        if build is None or build.submission_status is not Status.CONFIRMED or shown.revision != build.revision:
            await event.notice("This build changed. Reopen attachment review.")
            return
        failures = build.extra_info.get("attachment_failures", [])
        info = build.extra_info.copy()
        decisions = list(info.get("attachment_resolutions", []))
        url = None
        patch = BuildEditPatch()
        if action == "reopen":
            last = decisions.pop()
            info["attachment_failures"] = [*failures, last["issue"]]
            if last["added_url"]:
                patch = BuildEditPatch(
                    image_urls=[u for u in build.image_urls if u != last["added_url"]],
                    video_urls=[u for u in build.video_urls if u != last["added_url"]],
                )
        else:
            info["attachment_failures"] = failures[:index] + failures[index + 1 :]
        if action in {"assign", "preview"}:
            item = failures[index]
            try:
                data = await asyncio.to_thread(retained_bytes, item["attachment_id"], item["filename"])
            except (OSError, ValueError, KeyError, TypeError) as error:
                logger.warning(
                    "Attachment review %s failed for build %s, attachment %s: %s",
                    action,
                    build.id,
                    item["attachment_id"],
                    error,
                )
                await event.notice("The verified original is unavailable. Check the source messages or review archive.")
                return
            if action == "preview":
                interaction = event.context.interaction
                if interaction is not None:
                    await event.acknowledge()
                    await interaction.followup.send(
                        file=discord.File(io.BytesIO(data), filename=item["filename"]), ephemeral=True
                    )
                return
            mime = mimetypes.guess_type(item["filename"])[0] or ""
            if mime not in {
                "image/jpeg",
                "image/png",
                "image/webp",
                "image/gif",
                "video/webm",
                "video/mp4",
                "video/quicktime",
            }:
                await event.notice("Only supported images and videos can be published as build media.")
                return
            url = await request.client.catbox.upload(item["filename"], data, mime)
            if mime.startswith("image/") and url not in build.image_urls:
                patch = BuildEditPatch(image_urls=[*build.image_urls, url])
            elif mime.startswith("video/") and url not in build.video_urls:
                patch = BuildEditPatch(video_urls=[*build.video_urls, url])
            else:
                url = None
        if action != "reopen":
            decisions.append({"issue": failures[index], "reason": reason or "Assigned to this build", "added_url": url})
        info["attachment_resolutions"] = decisions
        patch = BuildEditPatch.combine([patch, BuildEditPatch(extra_info=info)])
        actor = await request.client.actors.resolve(request, refresh=True)
        self.build = await builds.apply_edit(
            actor, shown.id, patch, expected_revision=shown.revision, source="attachment_review_" + action
        )
        self.index = 0
        await request.client.refresh_posts("build", str(self.build.id))


class ExcludeAttachment(sl.forms.Form[sd.Request]):
    title = "Exclude attachment"
    reason = sl.forms.TextAreaField(label="Why does this file belong elsewhere?", required=True, maximum=400)

    def __init__(self, panel: AttachmentReview, build: Build, index: int) -> None:
        super().__init__()
        self.panel = panel
        self.build = build
        self.index = index

    @override
    async def on_submit(self, event: sl.SubmitEvent[sd.Request]) -> None:
        await self.panel._review(self.build, self.index, "exclude", self.reason or "", event)


@router.route("/builds/{build_id}/attachments")
async def review_attachments(request: AppRequest, build_id: int) -> AttachmentReview:
    return await AttachmentReview.open(request, build_id)
