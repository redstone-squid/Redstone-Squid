"""Subprocess import checks that install their blocker before loading the package."""

import importlib
import importlib.abc
import importlib.machinery
import sys
from collections.abc import Sequence
from typing import override


def _isolated_import(imports: Sequence[str], *blocked: str) -> None:
    """Import each of `imports` with `blocked` unimportable, and prove none of them arrived.

    The child-process half of `assert_imports_without`. Both halves are needed: refusing the
    import proves nothing reached for the module directly, and the `sys.modules` check catches
    a name that arrived transitively before the finder was installed.
    """

    class BlockImports(importlib.abc.MetaPathFinder):
        @override
        def find_spec(
            self, fullname: str, path: Sequence[str] | None = None, target: object = None
        ) -> importlib.machinery.ModuleSpec | None:
            if any(fullname == name or fullname.startswith(f"{name}.") for name in blocked):
                raise ModuleNotFoundError(fullname)
            return None

    sys.meta_path.insert(0, BlockImports())
    for name in imports:
        importlib.import_module(name)
    leaked = {
        module for module in sys.modules if any(module == name or module.startswith(f"{name}.") for name in blocked)
    }
    assert not leaked, f"{list(imports)} pulled in {sorted(leaked)}"
