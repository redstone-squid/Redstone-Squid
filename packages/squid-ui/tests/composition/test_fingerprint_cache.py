"""Incremental fingerprints skip immutable subtrees without retaining live owners."""

import gc
import weakref
from dataclasses import dataclass, field

from squid_ui.planning.identity import FingerprintCache, stable_fingerprint


@dataclass(frozen=True, slots=True)
class Branch:
    values: tuple[object, ...]


def test_changed_leaf_reuses_unchanged_subtrees() -> None:
    unchanged = Branch(tuple(Branch((f"item-{index}", index)) for index in range(100)))
    cache = FingerprintCache()
    stable_fingerprint((unchanged, "before"), cache=cache)
    work = []
    updated = (unchanged, "after")
    assert stable_fingerprint(updated, cache=cache, consume=lambda: work.append(None)) == stable_fingerprint(updated)
    assert len(work) == 3  # Root, retained branch, and edited leaf.
    assert cache.snapshot().hits > 0


def test_mutable_descendants_are_rehashed() -> None:
    values = ["before"]
    root = Branch((values,))
    cache = FingerprintCache()
    before = stable_fingerprint((root,), cache=cache)
    values[0] = "after"
    after = stable_fingerprint((root,), cache=cache)
    assert after != before
    assert after == stable_fingerprint((root,))


def test_callbacks_and_omitted_fields_do_not_keep_receivers_alive() -> None:
    class Receiver:
        def handle(self) -> None:
            pass

    @dataclass(frozen=True, slots=True)
    class Omitted:
        value: object = field(metadata={"stable_identity": False})

    receiver = Receiver()
    reference = weakref.ref(receiver)
    cache = FingerprintCache()
    stable_fingerprint((Branch((receiver.handle,)), Omitted(receiver)), cache=cache)
    del receiver
    gc.collect()
    assert reference() is None


def test_fingerprints_obey_both_memory_bounds_and_clear() -> None:
    cache = FingerprintCache(capacity=5, max_bytes=1600)
    for index in range(30):
        value = (Branch((str(index) * 100,)),)
        assert stable_fingerprint(value, cache=cache) == stable_fingerprint(value)
        assert cache.snapshot().entries <= 5
        assert cache.snapshot().retained_bytes <= 1600
    assert cache.snapshot().evictions > 0
    cache.clear()
    assert cache.snapshot().entries == cache.snapshot().retained_bytes == 0
