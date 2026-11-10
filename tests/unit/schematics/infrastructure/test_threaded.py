"""The threaded adapter must not load the optional engine until an operation needs it."""

import subprocess
import sys
import textwrap


def test_native_engine_is_loaded_on_first_operation() -> None:
    # A fresh interpreter keeps other schematic tests from preloading the native engine.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""\
                import asyncio
                import sys
                from importlib.abc import MetaPathFinder

                class MissingEngine(MetaPathFinder):
                    def find_spec(self, fullname, path=None, target=None):
                        if fullname == 'squid.schematics.infrastructure.nucleation_adapter':
                            raise ModuleNotFoundError('optional engine unavailable', name=fullname)

                sys.meta_path.insert(0, MissingEngine())
                from squid.config import SchematicConfig
                from squid.schematics.infrastructure.threaded import ThreadedSchematicAnalyzer

                assert 'squid.schematics.infrastructure.nucleation_adapter' not in sys.modules
                analyzer = ThreadedSchematicAnalyzer(SchematicConfig())
                assert 'squid.schematics.infrastructure.nucleation_adapter' not in sys.modules
                try:
                    asyncio.run(analyzer.capabilities())
                except ModuleNotFoundError as error:
                    assert error.name == 'squid.schematics.infrastructure.nucleation_adapter'
                else:
                    raise AssertionError('The first operation must attempt to load the engine')
            """),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
