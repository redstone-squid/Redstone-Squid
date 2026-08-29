"""Smoke-test a clean installation of every Squid UI distribution and extra.

The expected release version arrives through the ``FRAMEWORK_VERSION`` environment
variable, which the release workflow already exports for the install step.
"""

import os
from importlib import import_module
from importlib.metadata import version

DISTRIBUTIONS = {
    "squid-reactivity": "squid_reactivity",
    "squid-replication": "squid_replication",
    "squid-storage": "squid_storage",
    "squid-ui": "squid_ui",
    "squid-ui-discord": "squid_ui_discord",
    "squid-ui-slack": "squid_ui_slack",
    "squid-ui-widgets": "squid_ui_widgets",
}


def main() -> None:
    """Import the release roots and backend modules from the installed environment."""
    expected = os.environ["FRAMEWORK_VERSION"]
    for distribution, package in DISTRIBUTIONS.items():
        assert version(distribution) == expected
        import_module(package)

    import_module("asyncpg")
    import_module("loro")
    import_module("pycrdt")
    import_module("slack_sdk")
    backends = import_module("squid_replication.backends")
    assert backends.LoroBackend
    assert backends.PycrdtTextEngine


if __name__ == "__main__":
    main()
