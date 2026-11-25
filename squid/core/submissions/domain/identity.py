"""Time-ordered identifiers for drafts and inference runs.

Drafts and runs use UUIDv7, so sorting by ID sorts by creation time. Discord retries must land on
the draft or run they already created, so IDs made for a Discord event are derived from it: the
timestamp comes from the event's snowflake and the random bits from a hash of a stable name.
"""

import hashlib
from uuid import UUID

DISCORD_EPOCH_MS = 1_420_070_400_000


def stable_uuid7(timestamp_ms: int, name: str) -> UUID:
    """Return the UUIDv7 for `timestamp_ms` whose random bits are a hash of `name`.

    Equal inputs always produce the same ID. Unlike `uuid5`, the result still sorts by time.
    """
    random = int.from_bytes(hashlib.sha256(name.encode()).digest()[:10])
    rand_a, rand_b = random >> 68, random & ((1 << 62) - 1)
    return UUID(int=(timestamp_ms & (1 << 48) - 1) << 80 | 0x7 << 76 | rand_a << 64 | 0b10 << 62 | rand_b)


def discord_uuid7(snowflake: int, name: str) -> UUID:
    """Return the stable UUIDv7 for something created by the Discord event with `snowflake`."""
    return stable_uuid7((snowflake >> 22) + DISCORD_EPOCH_MS, name)
