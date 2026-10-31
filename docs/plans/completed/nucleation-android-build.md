# Building nucleation on Termux/Android

Verified `nucleation==0.10.14` from source on aarch64 Termux, API 24, Python 3.14.6,
on 2026-08-15. `scripts/termux.sh` supplies the build workarounds for the `schematics`
extra. Each issue below includes a reproducer; revisit the workarounds when upgrading
the pinned engine.

| Upstream issue | Failure | Workaround |
| --- | --- | --- |
| [#38](https://github.com/Schem-at/Nucleation/issues/38) | `rquickjs-sys` has no Android bindings; removing other features leaves unresolved C++ bridge symbols. | Build `bridge-full` minus only `scripting-js`. |
| [#36](https://github.com/Schem-at/Nucleation/issues/36) | CMake uses `Python3_SABI_LIBRARY` but discovers `Python_SABI_LIBRARY`; import fails on `PyExc_ImportError`. | Set `Python3_SABI_LIBRARY` to `$PREFIX/lib/libpython3.so`. |
| [#37](https://github.com/Schem-at/Nucleation/issues/37) | The rendering backend omits `libandroid`; import fails on `ANativeWindow_setBuffersGeometry`. | Set `CMAKE_MODULE_LINKER_FLAGS=-landroid`. |

Use `CMAKE_ARGS` so the definitions override cached CMake values. The extension is a
CMake `MODULE`, so `CMAKE_SHARED_LINKER_FLAGS` does not apply. Termux's `libpython3.so`
is the stable-ABI shim and does not depend on a Python minor version.

The original device verification covered import, schematic serialization round-trips,
fingerprints, and tick simulation. Rendering also needed an application fix:
`RenderConfig.create(width, height)` followed by `set_isometric()`. That API mismatch
affected every platform and is covered by the adapter contract tests.

PyO3 extensions (`cryptography`, `jsonschema-rs`) need a separate linker wrapper:
Termux's `--as-needed` drops their `-lpython3` request. Repeating it between
`--no-as-needed` and `--as-needed` retains the library in `DT_NEEDED`. The original
device investigation confirmed this with captured linker arguments; `RUSTFLAGS` and
`CARGO_ENCODED_RUSTFLAGS` alone did not fix the imports.
