"""Every framework exception derives from its distribution's documented root.

`except SquidUiError` (or `ReactivityError`, `StorageError`, `ReplicationError`) is the
supported way to catch everything a distribution raises deliberately, so an exception
class added outside the hierarchy silently escapes those handlers. This scan keeps the
promise structural rather than a review convention.
"""

import importlib
import pkgutil

import pytest

# Exception classes that are deliberately *not* errors: control-flow signals raised to be
# caught by exactly one frame. Deriving these from a root would make "catch everything
# that went wrong" also catch "nothing went wrong". Private signals (leading underscore)
# are exempt by convention; public ones must be enumerated here with their contract.
CONTROL_FLOW_SIGNALS = {
    # A MessageDestination declining to deliver after already telling the user why.
    "squid_ui_discord.delivery.DeliveryAbandoned",
}

# Package root -> the base class every exception defined inside must derive from.
# squid-ui-widgets raises squid-ui and stdlib errors only, so it has no root of its own;
# the scan still runs over it to catch a locally defined exception appearing later.
HIERARCHIES = {
    "squid_reactivity": ("squid_reactivity", "ReactivityError"),
    "squid_ui": ("squid_ui", "SquidUiError"),
    "squid_ui_widgets": ("squid_ui", "SquidUiError"),
    "squid_ui_discord": ("squid_ui", "SquidUiError"),
    "squid_storage": ("squid_storage", "StorageError"),
    "squid_replication": ("squid_replication", "ReplicationError"),
}


def _package_exceptions(package_name: str) -> list[type[BaseException]]:
    package = importlib.import_module(package_name)
    modules = [package]
    for info in pkgutil.walk_packages(package.__path__, prefix=f"{package_name}."):
        modules.append(importlib.import_module(info.name))
    found: dict[str, type[BaseException]] = {}
    for module in modules:
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseException)
                and value.__module__.startswith(package_name)
            ):
                found[f"{value.__module__}.{value.__qualname__}"] = value
    return [found[name] for name in sorted(found)]


@pytest.mark.parametrize(("package_name", "root_spec"), sorted(HIERARCHIES.items()))
def test_every_exception_derives_from_the_distribution_root(package_name: str, root_spec: tuple[str, str]) -> None:
    root_module, root_name = root_spec
    root: type[BaseException] = getattr(importlib.import_module(root_module), root_name)
    strays = [
        name
        for cls in _package_exceptions(package_name)
        if not issubclass(cls, root)
        and not cls.__qualname__.startswith("_")
        and (name := f"{cls.__module__}.{cls.__qualname__}") not in CONTROL_FLOW_SIGNALS
    ]
    assert strays == [], f"exceptions outside {root_name}: {strays}"
