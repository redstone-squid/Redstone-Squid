"""Address-space accounting that sizes the schematic worker's `RLIMIT_AS`."""

from pathlib import Path

import pytest

from squid.schematics.infrastructure import worker_main
from squid.schematics.infrastructure.worker_main import _current_address_space_bytes

pytestmark = pytest.mark.skipif(worker_main.resource is None, reason="rlimits are POSIX-only")


def test_mapped_pages_are_converted_to_bytes(tmp_path: Path) -> None:
    """The budget is added to this baseline, so a page/byte mix-up would silently under-cap it."""
    assert worker_main.resource is not None
    page_size = worker_main.resource.getpagesize()
    statm = tmp_path / "statm"
    # Real /proc/self/statm fields: size, resident, shared, text, lib, data, dt.
    statm.write_text(f"{2500} 400 300 20 0 900 0\n", encoding="utf-8")

    assert _current_address_space_bytes(statm) == 2500 * page_size


def test_a_missing_statm_costs_nothing(tmp_path: Path) -> None:
    """POSIX hosts without /proc still have to start; they just get an absolute limit."""
    assert _current_address_space_bytes(tmp_path / "absent") == 0


@pytest.mark.parametrize("contents", ["", "\n", "not-a-number 400 300\n"])
def test_unparseable_statm_costs_nothing(tmp_path: Path, contents: str) -> None:
    statm = tmp_path / "statm"
    statm.write_text(contents, encoding="utf-8")

    assert _current_address_space_bytes(statm) == 0


@pytest.mark.skipif(not worker_main.STATM_PATH.exists(), reason="requires a Linux /proc")
def test_the_default_path_measures_the_running_process() -> None:
    """The parameter exists for the tests above; the default still has to work on a real host."""
    assert _current_address_space_bytes() > 0
