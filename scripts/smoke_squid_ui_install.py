"""Smoke-test a clean installation of every Squid UI distribution and extra."""

from importlib import import_module
from importlib.metadata import version

VERSION = "0.1.0a1"
DISTRIBUTIONS = {
    "squid-reactivity": "squid_reactivity",
    "squid-replication": "squid_replication",
    "squid-storage": "squid_storage",
    "squid-ui": "squid_ui",
    "squid-ui-discord": "squid_ui_discord",
    "squid-ui-widgets": "squid_ui_widgets",
}


def main() -> None:
    """Import the release roots and backend modules from the installed environment."""
    for distribution, package in DISTRIBUTIONS.items():
        assert version(distribution) == VERSION
        import_module(package)

    import_module("asyncpg")
    import_module("loro")
    import_module("pycrdt")
    backends = import_module("squid_replication.backends")
    assert backends.LoroBackend
    assert backends.PycrdtTextEngine


if __name__ == "__main__":
    main()
