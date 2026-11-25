"""Canonical comparison of a record's meaningful published contents."""

import hashlib
import json

from pydantic import TypeAdapter

from squid.core.records.application.models import ComputedRecordResult

_RECORD = TypeAdapter(ComputedRecordResult)


def record_fingerprint(record: ComputedRecordResult) -> str:
    """Digest what a reader of the record can see.

    Evaluation times, the rules revision and members that supply no published
    evidence are left out, so recomputing an unchanged record, or renaming it
    with identical wording, matches its stored fingerprint.
    """
    document = _RECORD.dump_python(record, mode="json")
    resolution = record.resolution
    published_ids = {*resolution.holder_ids, *resolution.tied_ids, *(gap.build_id for gap in resolution.gaps)}
    published_ids.update(build_id for period in record.history for build_id in period.build_ids)
    document["category"]["candidates"] = sorted(
        (c for c in document["category"]["candidates"] if c["build_id"] in published_ids),
        key=lambda c: c["build_id"],
    )
    document["resolution"]["gaps"].sort(key=lambda g: (g["build_id"], g["field"]))
    if document["door_evidence"] is not None:
        del document["door_evidence"]["evaluation"]["evaluated_at"]
        document["door_evidence"]["sources"].sort(key=lambda source: source["build_id"])
    value = {"fingerprint_version": 5, "record": document}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
