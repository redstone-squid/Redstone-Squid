"""Guard which modules a narrow import surface is allowed to drag into `sys.modules`."""

import json
import subprocess
import sys

import pytest

# Importing the schematic domain or application layer must never drag in the native engine:
# it is optional, expensive to import, and absent on some deployments.
NO_NATIVE_ENGINE = ("nucleation", "sqlalchemy", "discord")


@pytest.mark.parametrize(
    ("module", "unexpected_modules"),
    [
        # Problem details are safe to reuse from route modules as long as the import does not
        # initialize persistence or Discord transports.
        pytest.param("squid.api.errors", ("sqlalchemy", "discord"), id="api-errors"),
        pytest.param("squid.bot.utils.permissions", ("squid.bot.app",), id="bot-permissions"),
        pytest.param(
            "squid.bot.submission.ui.fields",
            ("squid.bot.submission.ui.views",),
            id="submission-fields",
        ),
        pytest.param("squid.schematics.domain", NO_NATIVE_ENGINE, id="schematic-domain"),
        pytest.param("squid.schematics.application", NO_NATIVE_ENGINE, id="schematic-application"),
        pytest.param("squid.schematics.infrastructure.capability", ("nucleation",), id="schematic-capability"),
    ],
)
def test_narrow_transport_imports_do_not_load_application_graph(
    module: str, unexpected_modules: tuple[str, ...]
) -> None:
    """Each boundary reports the modules it actually leaked, not just a failed exit code.

    The child process reports rather than asserts, so an unimportable module and a
    leaked import are distinguishable and the failure names the offending module.
    """
    script = (
        "import json\n"
        "import sys\n"
        f"import {module}\n"
        f"print(json.dumps([name for name in {unexpected_modules!r} if name in sys.modules]))\n"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"importing {module} failed:\n{result.stderr}"
    leaked: list[str] = json.loads(result.stdout)
    assert leaked == [], f"importing {module} also loaded {leaked}"
