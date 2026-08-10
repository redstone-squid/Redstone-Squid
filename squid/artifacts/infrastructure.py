"""Local and S3-compatible artifact storage adapters."""

import asyncio
import errno
import hashlib
import os
import stat
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


class ArtifactSourceChangedError(ValueError):
    """A staged source changed while it was being copied to durable storage."""


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

    async def put_path(
        self,
        key: str,
        source: Path,
        *,
        content_type: str,
        max_bytes: int,
    ) -> ArtifactMetadata:
        """Copy a regular staged file without loading it into application memory."""
        del content_type
        return await asyncio.to_thread(_copy_regular_atomic, source, self._path(key), max_bytes)

    async def get(self, key: str, *, max_bytes: int) -> bytes | None:
        path = self._path(key)
        try:
            return await asyncio.to_thread(self._read_bounded, path, max_bytes)
        except FileNotFoundError:
            return None

    async def get_path(
        self,
        key: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> ArtifactMetadata | None:
        """Copy a stored object to a caller-owned path with a hard byte bound."""
        try:
            return await asyncio.to_thread(_copy_regular_atomic, self._path(key), destination, max_bytes)
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

    async def aclose(self) -> None:
        """Release resources owned by the adapter."""

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

    async def put_path(
        self,
        key: str,
        source: Path,
        *,
        content_type: str,
        max_bytes: int,
    ) -> ArtifactMetadata:
        """Upload a bounded regular staged file through the SDK's streaming body."""
        return await asyncio.to_thread(
            self._put_path,
            self._key(key),
            source,
            content_type,
            max_bytes,
        )

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

    async def get_path(
        self,
        key: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> ArtifactMetadata | None:
        """Stream a bounded object into a caller-owned path."""
        return await asyncio.to_thread(self._get_path, self._key(key), destination, max_bytes)

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

    async def aclose(self) -> None:
        """Close the SDK connection pool."""
        await asyncio.to_thread(self._client.close)

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

    def _put_path(self, key: str, source: Path, content_type: str, max_bytes: int) -> ArtifactMetadata:
        with _open_regular(source, max_bytes) as (stream, initial):
            digest = _hash_stream(stream, max_bytes)
            stream.seek(0)
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=stream,
                ContentLength=initial.st_size,
                ContentType=content_type,
                Metadata={"sha256": digest},
            )
            try:
                _require_unchanged(stream.fileno(), initial)
            except ArtifactSourceChangedError:
                self._client.delete_object(Bucket=self._bucket, Key=key)
                raise
        return ArtifactMetadata(byte_size=initial.st_size, sha256=digest)

    def _get_path(self, key: str, destination: Path, max_bytes: int) -> ArtifactMetadata | None:
        _require_positive_budget(max_bytes)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
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
            return _write_body_atomic(body, destination, max_bytes, expected_size=byte_size)
        finally:
            body.close()


def create_artifact_store(config: ObjectStorageConfig) -> LocalArtifactStore | S3ArtifactStore:
    """Build the configured process-owned artifact adapter."""
    if config.backend == "s3":
        return S3ArtifactStore(config)
    return LocalArtifactStore(config.local_directory, prefix=config.prefix)


def _is_not_found(error: ClientError) -> bool:
    code = str(error.response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def _copy_regular_atomic(source: Path, destination: Path, max_bytes: int) -> ArtifactMetadata:
    _require_positive_budget(max_bytes)
    with _open_regular(source, max_bytes) as (stream, initial):
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
        temporary_path = Path(temporary_name)
        digest = hashlib.sha256()
        copied = 0
        try:
            with os.fdopen(descriptor, "wb") as output:
                while chunk := stream.read(min(1024 * 1024, max_bytes - copied + 1)):
                    copied += len(chunk)
                    if copied > max_bytes:
                        msg = f"Artifact exceeds the {max_bytes}-byte transfer budget."
                        raise ArtifactTooLargeError(msg)
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            _require_unchanged(stream.fileno(), initial)
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)
    return ArtifactMetadata(byte_size=copied, sha256=digest.hexdigest())


class _RegularSource:
    """Context manager for a no-follow regular file and its initial identity."""

    def __init__(self, path: Path, max_bytes: int) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._stream: Any = None
        self._initial: os.stat_result | None = None

    def __enter__(self) -> tuple[Any, os.stat_result]:
        _require_positive_budget(self._max_bytes)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._path, flags)
        except OSError as error:
            if error.errno != errno.ELOOP:
                raise
            msg = "Artifact sources must be regular files."
            raise ValueError(msg) from error
        try:
            initial = os.fstat(descriptor)
            if not stat.S_ISREG(initial.st_mode):
                msg = "Artifact sources must be regular files."
                raise ValueError(msg)
            if initial.st_size > self._max_bytes:
                msg = f"Artifact exceeds the {self._max_bytes}-byte transfer budget."
                raise ArtifactTooLargeError(msg)
            self._stream = os.fdopen(descriptor, "rb")
            descriptor = -1
            self._initial = initial
            return self._stream, initial
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._stream is not None:
            self._stream.close()


def _open_regular(path: Path, max_bytes: int) -> _RegularSource:
    return _RegularSource(path, max_bytes)


def _hash_stream(stream: Any, max_bytes: int) -> str:
    digest = hashlib.sha256()
    consumed = 0
    while chunk := stream.read(min(1024 * 1024, max_bytes - consumed + 1)):
        consumed += len(chunk)
        if consumed > max_bytes:
            msg = f"Artifact exceeds the {max_bytes}-byte transfer budget."
            raise ArtifactTooLargeError(msg)
        digest.update(chunk)
    return digest.hexdigest()


def _require_unchanged(descriptor: int, initial: os.stat_result) -> None:
    current = os.fstat(descriptor)
    if (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    ) != (
        initial.st_dev,
        initial.st_ino,
        initial.st_size,
        initial.st_mtime_ns,
    ):
        msg = "Artifact source changed while it was being transferred."
        raise ArtifactSourceChangedError(msg)


def _write_body_atomic(body: Any, destination: Path, max_bytes: int, *, expected_size: int) -> ArtifactMetadata:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    copied = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            while chunk := body.read(min(1024 * 1024, max_bytes - copied + 1)):
                copied += len(chunk)
                if copied > max_bytes:
                    msg = f"Artifact exceeds the {max_bytes}-byte transfer budget."
                    raise ArtifactTooLargeError(msg)
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if copied != expected_size:
            msg = "Artifact download did not match its declared byte size."
            raise ArtifactSourceChangedError(msg)
        temporary_path.replace(destination)
    finally:
        temporary_path.unlink(missing_ok=True)
    return ArtifactMetadata(byte_size=copied, sha256=digest.hexdigest())


def _require_positive_budget(max_bytes: int) -> None:
    if max_bytes < 1:
        msg = "Artifact transfer budgets must be positive."
        raise ValueError(msg)
