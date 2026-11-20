import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from squid.bot.submission.ui.attachment_review import retained_bytes, warn_if_attachment_archive_unavailable


def test_startup_warns_when_archive_mount_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("ATTACHMENT_REVIEW_ARCHIVE", str(tmp_path))
    (tmp_path / "retained-files.jsonl").touch()

    warn_if_attachment_archive_unavailable()

    assert "Attachment review archive unavailable" in caplog.text
    assert str(tmp_path / "retained-files") in caplog.text
    assert "deploy/compose.attachment-review.yml" in caplog.text

    caplog.clear()
    (tmp_path / "retained-files").mkdir()
    warn_if_attachment_archive_unavailable()

    assert not caplog.records


def test_review_uses_only_verified_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATTACHMENT_REVIEW_ARCHIVE", str(tmp_path))
    identity = str(uuid4())
    (tmp_path / "retained-files").mkdir()
    file = tmp_path / "retained-files" / identity
    file.write_bytes(b"original")
    manifest = {
        "source_id": identity,
        "retained": True,
        "filename": "door.webm",
        "byte_size": 8,
        "sha256": hashlib.sha256(b"original").hexdigest(),
    }
    (tmp_path / "retained-files.jsonl").write_text(json.dumps(manifest))
    assert retained_bytes(identity, "door.webm") == b"original"
    file.write_bytes(b"modified")
    with pytest.raises(ValueError, match="checksum"):
        retained_bytes(identity, "door.webm")
