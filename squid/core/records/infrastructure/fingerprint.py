"""Canonical comparison of meaningful concrete publication contents."""

import hashlib
import json

from pydantic import TypeAdapter

from squid.core.records.application.models import ComputationBatch, ComputedRecordResult

_RECORD = TypeAdapter(ComputedRecordResult)


def publication_fingerprint(batch: ComputationBatch) -> str:
    """Ignore evaluation times and candidates that supply no published evidence."""
    records: list[object] = []
    for record in sorted(batch.records, key=lambda r: (r.category.identity.key, r.criterion.value)):
        document = _RECORD.dump_python(record, mode="json")
        published_ids = set(record.resolution.holder_ids)
        published_ids.update(build_id for period in record.history for build_id in period.build_ids)
        document["category"]["candidates"] = sorted(
            (c for c in document["category"]["candidates"] if c["build_id"] in published_ids),
            key=lambda c: c["build_id"],
        )
        document["resolution"]["gaps"].sort(key=lambda g: (g["build_id"], g["field"]))
        if document["door_evidence"] is not None:
            del document["door_evidence"]["evaluation"]["evaluated_at"]
            document["door_evidence"]["sources"].sort(key=lambda source: source["build_id"])
        records.append(document)
    value = {
        "fingerprint_version": 2,
        "rules_revision_id": batch.rules_revision_id,
        "kind": batch.kind.value,
        "version_id": batch.version_id,
        "records": records,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
