import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    ("module", "unexpected_modules"),
    [
        # Problem details are safe to reuse from route modules as long as the import does not
        # initialize persistence or Discord transports.
        ("squid.api.errors", ("sqlalchemy", "discord")),
        ("squid.bot.utils.permissions", ("squid.bot.app",)),
        ("squid.bot.submission.ui.components", ("squid.bot.submission.ui.views",)),
        # Importing the schematic domain or application layer must never drag in the native
        # engine: it is optional, expensive to import, and absent on some deployments.
        ("squid.schematics.domain", ("nucleation", "sqlalchemy", "discord")),
        ("squid.schematics.application", ("nucleation", "sqlalchemy", "discord")),
        ("squid.schematics.infrastructure.capability", ("nucleation",)),
    ],
)
def test_narrow_transport_imports_do_not_load_application_graph(
    module: str, unexpected_modules: tuple[str, ...]
) -> None:
    script = (
        f"import {module}\n"
        "import sys\n"
        f"unexpected = {unexpected_modules!r}\n"
        "assert all(name not in sys.modules for name in unexpected)\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
