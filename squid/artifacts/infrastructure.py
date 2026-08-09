"""Local and S3-compatible artifact storage adapters."""

import asyncio
import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

import boto3.session
from botocore.config import Config
from botocore.exceptions import ClientError

from squid.artifacts.application import ArtifactMetadata
from squid.config import ObjectStorageConfig


class ArtifactTooLargeError(ValueError):
    """An artifact exceeded the caller's download budget."""


class LocalArtifactStore:
    """Filesystem implementation used by development and single-host deployments."""

    def __init__(self, directory: Path, *, prefix: str = "") -> None:
        self._directory = directory
        self._prefix = prefix.strip("/")

    async def put(self, key: str, data: bytes, *, content_type: str) -> ArtifactMetadata:
        del content_type
        digest = hashlib.sha256(data).hexdigest()
        path = self._path(key)
        await asyncio.to_thread(self._write_atomic, path, data)
        return ArtifactMetadata(byte_size=len(data), sha256=digest)

    async def get(self, key: str, *, max_bytes: int) -> bytes | None:
        path = self._path(key)
        try:
            return await asyncio.to_thread(self._read_bounded, path, max_bytes)
        except FileNotFoundError:
            return None

    async def stat(self, key: str) -> ArtifactMetadata | None:
        path = self._path(key)
        try:
            byte_size = (await asyncio.to_thread(path.stat)).st_size
        except FileNotFoundError:
            return None
        return ArtifactMetadata(byte_size=byte_size)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            return

    def _path(self, key: str) -> Path:
        normalized = PurePosixPath(key)
        if (
            normalized.is_absolute()
            or not normalized.parts
            or any(part in {"", ".", ".."} for part in normalized.parts)
        ):
            msg = "Artifact keys must be non-empty relative paths without traversal."
            raise ValueError(msg)
        return self._directory.joinpath(self._prefix, *normalized.parts)

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _read_bounded(path: Path, max_bytes: int) -> bytes:
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            msg = f"Artifact exceeds the {max_bytes}-byte download budget."
            raise ArtifactTooLargeError(msg)
        return data


class S3ArtifactStore:
    """S3-compatible adapter with explicit timeouts and bounded downloads."""

    def __init__(self, config: ObjectStorageConfig) -> None:
        if config.backend != "s3" or config.bucket is None:
            msg = "S3ArtifactStore requires an S3 object-storage configuration."
            raise ValueError(msg)
        session = boto3.session.Session(
            aws_access_key_id=config.access_key.get_secret_value() if config.access_key is not None else None,
            aws_secret_access_key=(config.secret_key.get_secret_value() if config.secret_key is not None else None),
            region_name=config.region,
        )
        self._client: Any = session.client(
            "s3",
            endpoint_url=str(config.endpoint) if config.endpoint is not None else None,
            config=Config(
                connect_timeout=config.connect_timeout_seconds,
                read_timeout=config.read_timeout_seconds,
                retries={"max_attempts": config.max_attempts, "mode": "standard"},
                s3={"addressing_style": config.addressing_style},
            ),
        )
        self._bucket = config.bucket
        self._prefix = config.prefix.strip("/")

    async def put(self, key: str, data: bytes, *, content_type: str) -> ArtifactMetadata:
        digest = hashlib.sha256(data).hexdigest()
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=self._key(key),
            Body=data,
            ContentLength=len(data),
            ContentType=content_type,
            Metadata={"sha256": digest},
        )
        return ArtifactMetadata(byte_size=len(data), sha256=digest)

    async def get(self, key: str, *, max_bytes: int) -> bytes | None:
        try:
            response = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=self._key(key))
        except ClientError as error:
            if _is_not_found(error):
                return None
            raise
        byte_size = int(response.get("ContentLength", 0))
        body: Any = response["Body"]
        try:
            if byte_size > max_bytes:
                msg = f"Artifact exceeds the {max_bytes}-byte download budget."
                raise ArtifactTooLargeError(msg)
            data = await asyncio.to_thread(body.read, max_bytes + 1)
        finally:
            body.close()
        if len(data) > max_bytes:
            msg = f"Artifact exceeds the {max_bytes}-byte download budget."
            raise ArtifactTooLargeError(msg)
        return bytes(data)

    async def stat(self, key: str) -> ArtifactMetadata | None:
        try:
            response = await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=self._key(key))
        except ClientError as error:
            if _is_not_found(error):
                return None
            raise
        metadata = response.get("Metadata", {})
        return ArtifactMetadata(byte_size=int(response["ContentLength"]), sha256=metadata.get("sha256"))

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=self._key(key))

    def _key(self, key: str) -> str:
        normalized = PurePosixPath(key)
        if (
            normalized.is_absolute()
            or not normalized.parts
            or any(part in {"", ".", ".."} for part in normalized.parts)
        ):
            msg = "Artifact keys must be non-empty relative paths without traversal."
            raise ValueError(msg)
        relative = normalized.as_posix()
        return f"{self._prefix}/{relative}" if self._prefix else relative


def create_artifact_store(config: ObjectStorageConfig) -> LocalArtifactStore | S3ArtifactStore:
    """Build the configured process-owned artifact adapter."""
    if config.backend == "s3":
        return S3ArtifactStore(config)
    return LocalArtifactStore(config.local_directory, prefix=config.prefix)


def _is_not_found(error: ClientError) -> bool:
    code = str(error.response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}
