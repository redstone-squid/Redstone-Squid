"""Repair retained media artifacts without inference, build edits, or finalization.

Preview by default. --execute requires an append-only JSONL audit path. Completed
originals are skipped by checksum, so an interrupted run can be repeated. Discarded
uploads are intentionally excluded: their removal may reflect a reviewed decision.
"""

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anyio
import discord
from sqlalchemy import text

from squid.artifacts import ArtifactStore
from squid.artifacts.infrastructure import create_artifact_store
from squid.config import load_bot_process_config
from squid.media.application import MediaNormalizationRequest, MediaNormalizationService
from squid.media.application.jobs import _encode_report
from squid.media.domain import MediaKind
from squid.media.infrastructure import FfmpegMediaNormalizer
from squid.persistence.engine import DatabaseEngine

INVENTORY = """
SELECT u.*, j.status, d.source_messages, d.source_files,
       r.inputs->'bundle'->'channel_id' AS bundle_channel_id,
       jsonb_path_query_array(r.inputs, '$.bundle.primary[*].message_id') AS bundle_message_ids,
       (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.role) FROM media_artifacts a WHERE a.upload_id=u.id) AS artifacts
FROM media_uploads u
JOIN media_normalization_jobs j ON j.upload_id=u.id
JOIN submission_drafts d ON d.id=u.draft_id
LEFT JOIN submission_inference_runs r ON r.id=d.inference_run_id
WHERE j.status IN ('completed', 'dead') AND NOT u.strip_audio
  AND (CAST(:upload_id AS uuid) IS NULL OR u.id=CAST(:upload_id AS uuid))
  AND NOT EXISTS (SELECT 1 FROM media_artifacts a WHERE a.upload_id=u.id AND a.role='output'
                  AND a.sha256=u.source_sha256 AND j.status='completed')
ORDER BY u.created_at,u.id
LIMIT :limit
"""


async def recover_source(
    row: dict[str, Any],
    destination: Path,
    store: ArtifactStore,
    client: discord.Client,
    *,
    channels: dict[int, Any] | None = None,
    messages: OrderedDict[tuple[int, int], discord.Message] | None = None,
) -> None:
    """Recover only bytes matching the original upload's size and SHA-256."""
    expected_size = row["source_byte_size"]
    result = await store.get_path(row["source_object_key"], destination, max_bytes=expected_size)
    if result is not None:
        with destination.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest == row["source_sha256"]:
            return
        msg = "Stored source checksum mismatch"
        raise ValueError(msg)
    if channels is None:
        channels = {}
    if messages is None:
        messages = OrderedDict()
    attachment_ids: set[int] = set()
    for source_file in row.get("source_files", []):
        if source_file["id"] != str(row["id"]):
            continue
        parts = urlsplit(source_file["url"]).path.split("/")
        if len(parts) >= 5 and parts[1] == "attachments" and parts[3].isdecimal():
            attachment_ids.add(int(parts[3]))
    locations = [(int(source["channel_id"]), int(source["id"])) for source in row["source_messages"]]
    # Inference can attribute the build to its main message while its files came
    # from follow-up messages in the same retained bundle.
    if row.get("bundle_channel_id") is not None:
        locations.extend((int(row["bundle_channel_id"]), int(value)) for value in row.get("bundle_message_ids") or [])
    for channel_id, message_id in dict.fromkeys(locations):
        try:
            if channel_id not in channels:
                channels[channel_id] = await client.fetch_channel(channel_id)
            channel = channels[channel_id]
            if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                continue
            key = (channel_id, message_id)
            if key not in messages:
                messages[key] = await channel.fetch_message(message_id)
            message = messages[key]
            messages.move_to_end(key)
            while len(messages) > 512:
                messages.popitem(last=False)
        except discord.HTTPException:
            continue
        for attachment in message.attachments:
            # Discord's declared PNG size can differ from the downloaded bytes.
            # A retained attachment ID is stronger evidence for selecting a source;
            # the actual byte count and checksum below still decide acceptance.
            if attachment.id not in attachment_ids and attachment.size != expected_size:
                continue
            with anyio.fail_after(120):
                await attachment.save(destination)
            with destination.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if destination.stat().st_size == expected_size and digest == row["source_sha256"]:
                return
    msg = "No accessible Discord attachment matches the original checksum"
    raise ValueError(msg)


async def replace_artifacts(
    db: DatabaseEngine, store: ArtifactStore, row: dict[str, Any], artifacts: list[dict[str, Any]], paths: list[Path]
) -> None:
    """Lease new objects before writing, then replace metadata under the job lock."""
    token = uuid4()
    async with db.async_session.begin() as session:
        for artifact in sorted(artifacts, key=lambda item: item["object_key"]):
            parameters = {**artifact, "upload_id": row["id"], "token": token}
            await session.execute(
                text("""
                INSERT INTO media_artifact_objects
                  (object_key,sha256,byte_size,first_upload_id,last_upload_id)
                VALUES (:object_key,:sha256,:byte_size,:upload_id,:upload_id)
                ON CONFLICT (object_key) DO NOTHING
            """),
                parameters,
            )
            obj = (
                (
                    await session.execute(
                        text("""
                SELECT cleanup_claim_token,sha256,byte_size FROM media_artifact_objects
                WHERE object_key=:object_key FOR UPDATE
            """),
                        parameters,
                    )
                )
                .mappings()
                .one()
            )
            if obj["cleanup_claim_token"] is not None:
                msg = "Artifact cleanup is active; retry later"
                raise ValueError(msg)
            if obj["sha256"] != artifact["sha256"] or obj["byte_size"] != artifact["byte_size"]:
                msg = "Artifact identity conflict"
                raise ValueError(msg)
            await session.execute(
                text("""
                UPDATE media_artifact_objects SET deleted_at=NULL,last_seen_at=now(),last_upload_id=:upload_id
                WHERE object_key=:object_key
            """),
                parameters,
            )
            await session.execute(
                text("""
                INSERT INTO media_artifact_publications (object_key,upload_id,claim_token,expires_at)
                VALUES (:object_key,:upload_id,:token,now()+interval '48 hours')
            """),
                parameters,
            )
    for artifact, path in zip(artifacts, paths, strict=True):
        await store.put_path(
            artifact["object_key"], path, content_type=artifact["content_type"], max_bytes=artifact["byte_size"]
        )
    async with db.async_session.begin() as session:
        status = await session.scalar(
            text("""
            SELECT status FROM media_normalization_jobs WHERE upload_id=:id FOR UPDATE
        """),
            {"id": row["id"]},
        )
        current = await session.scalar(
            text("""
            SELECT jsonb_agg(to_jsonb(a) ORDER BY a.role) FROM media_artifacts a WHERE upload_id=:id
        """),
            {"id": row["id"]},
        )
        if status != row["status"] or current != row["artifacts"]:
            msg = "Media changed since inspection; preserving concurrent changes"
            raise ValueError(msg)
        leases = await session.scalar(
            text("""
            SELECT count(*) FROM media_artifact_publications
            WHERE upload_id=:id AND claim_token=:token AND expires_at>now()
        """),
            {"id": row["id"], "token": token},
        )
        if leases != len(artifacts):
            msg = "Publication lease expired; retry"
            raise ValueError(msg)
        await session.execute(text("DELETE FROM media_artifacts WHERE upload_id=:id"), {"id": row["id"]})
        for artifact in artifacts:
            await session.execute(
                text("""
                INSERT INTO media_artifacts (upload_id,role,object_key,content_type,byte_size,sha256,width,height)
                VALUES (:upload_id,:role,:object_key,:content_type,:byte_size,:sha256,:width,:height)
            """),
                {**artifact, "upload_id": row["id"]},
            )
        await session.execute(
            text("""
            UPDATE media_normalization_jobs SET status='completed',completed_at=now(),dead_at=NULL,last_error=NULL
            WHERE upload_id=:id
        """),
            {"id": row["id"]},
        )
        await session.execute(
            text("""
            DELETE FROM media_artifact_publications WHERE upload_id=:id AND claim_token=:token
        """),
            {"id": row["id"], "token": token},
        )


async def run(arguments: argparse.Namespace) -> int:
    config = load_bot_process_config()
    db = DatabaseEngine(config.runtime.database)
    store = create_artifact_store(config.runtime.object_storage)
    normalizer = MediaNormalizationService(FfmpegMediaNormalizer())
    failed = 0
    try:
        async with db.async_session() as session:
            rows = [
                dict(row)
                for row in (
                    await session.execute(
                        text(INVENTORY),
                        {
                            "upload_id": arguments.upload_id,
                            "limit": arguments.limit,
                        },
                    )
                ).mappings()
            ]
        print(json.dumps({"selected": len(rows), "execute": arguments.execute}), flush=True)
        if not arguments.execute:
            for row in rows:
                print(json.dumps({"upload_id": str(row["id"]), "kind": row["kind"], "status": row["status"]}))
            return 0
        async with discord.Client(intents=discord.Intents.none()) as client:
            await client.login(config.discord.token.get_secret_value())

            channels: dict[int, Any] = {}
            messages: OrderedDict[tuple[int, int], discord.Message] = OrderedDict()

            async def repair(row: dict[str, Any]) -> None:
                nonlocal failed
                try:
                    with tempfile.TemporaryDirectory(prefix="squid-asset-reindex-") as directory:
                        root = Path(directory)
                        source = root / "source"
                        await recover_source(row, source, store, client, channels=channels, messages=messages)
                        result = await normalizer.normalize(
                            MediaNormalizationRequest(
                                kind=MediaKind(row["kind"]),
                                source_path=source,
                                output_path=root / "output",
                                poster_path=root / "thumbnail.jpg" if row["kind"] == "video" else None,
                            )
                        )
                        artifacts: list[dict[str, Any]] = []
                        paths = [result.output_path]
                        roles = [("output", result.report.output)]
                        if result.report.poster is not None and result.poster_path is not None:
                            roles.append(("video_thumbnail", result.report.poster))
                            paths.append(result.poster_path)
                        for role, facts in roles:
                            artifacts.append(
                                {**asdict(facts), "role": role, "object_key": f"media/reindexed/{role}/{facts.sha256}"}
                            )
                        report = _encode_report(result.report)
                        report_path = root / "report.json"
                        report_path.write_bytes(report)
                        digest = hashlib.sha256(report).hexdigest()
                        artifacts.append(
                            {
                                "role": "report",
                                "object_key": f"media/reindexed/report/{digest}",
                                "sha256": digest,
                                "content_type": "application/json",
                                "byte_size": len(report),
                                "width": None,
                                "height": None,
                            }
                        )
                        paths.append(report_path)
                        with Path(arguments.audit).open("a") as audit:
                            audit.write(json.dumps({"before": row, "after": artifacts}, default=str) + "\n")
                            audit.flush()
                        await replace_artifacts(db, store, row, artifacts, paths)
                    print(json.dumps({"repaired": str(row["id"])}), flush=True)
                except Exception as exc:
                    failed += 1
                    print(json.dumps({"failed": str(row["id"]), "error": str(exc)}), flush=True)

            for start in range(0, len(rows), arguments.concurrency):
                async with anyio.create_task_group() as tasks:
                    for row in rows[start : start + arguments.concurrency]:
                        tasks.start_soon(repair, row)
    finally:
        await normalizer.aclose()
        await store.aclose()
        await db.close()
    return int(failed > 0)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--audit", help="Append pre-change metadata and replacement facts as JSONL.")
    parser.add_argument("--upload-id", help="Restrict to a single upload for a canary repair.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args(argv)
    if min(args.limit, args.concurrency) < 1 or (args.execute and not args.audit):
        parser.error("Use a positive --limit and supply --audit with --execute")
    return args


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse_args())))
