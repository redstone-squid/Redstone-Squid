# Building nucleation on Termux/Android

The `schematics` extra depends on `nucleation`, whose sdist needs a Rust toolchain and, on
Termux, several build-time workarounds. This records what's local-only versus upstream-tracked,
so the workarounds in `pyproject.toml`'s `[tool.uv.extra-build-variables]` can be removed once
fixed releases land.

## Verified on 2026-08-15

Built `nucleation==0.10.14` from source on Termux (aarch64-linux-android, API level 24, Python
3.14.6) via `uv sync --extra schematics`. Three distinct build/link failures were found in
sequence -- each only surfaces once the previous one is worked around, since the extension
fails to `dlopen` on the first unresolved symbol it hits and never gets far enough to report
the next one. All three are reported upstream with reproducers:

1. **[Nucleation#38](https://github.com/Schem-at/Nucleation/issues/38)** -- The default
   `NUCLEATION_FEATURES=bridge-full` pulls in `rquickjs` (`scripting-js`), whose `rquickjs-sys`
   0.9.0 dependency has no pregenerated bindings for `aarch64-linux-android` and fails to
   compile outright. `nucleation_adapter.py` never touches the scripting bridge, so we want to
   drop `scripting-js` -- but the generated C++ bindings under `src/sub_modules` are globbed in
   unconditionally regardless of `NUCLEATION_FEATURES`, so dropping any *other* feature (we
   tried a minimal `bridge,rendering,mc-tick` set first) breaks the link looking for FFI
   symbols the Rust side never exported (e.g. `IoLayout_destroy` when `hdl` is dropped).
   **Workaround:** build with `bridge-full` minus only `scripting-js` --
   `NUCLEATION_FEATURES="bridge,meshing,simulation,rendering,scripting-lua,voxelize,world-segment,store-ssh,mc-tick,routing,hdl"`.
   This is safe specifically because `bridge/scripting.rs`'s exported symbol isn't
   module-level gated; it branches internally on `scripting-js`/`scripting-lua`.
2. **[Nucleation#36](https://github.com/Schem-at/Nucleation/issues/36)** -- `CMakeLists.txt`
   links the extension against `${Python3_SABI_LIBRARY}`, but the file calls
   `find_package(Python ...)` (singular), which only ever populates `Python_SABI_LIBRARY`. The
   `Python3_`-prefixed variable is always empty, so the built module carries no `libpython`
   `NEEDED` entry. glibc Linux hides this (the CPython executable already exports its C-API
   symbols globally, so `dlopen` resolves them anyway); Android's bionic linker does not do
   that implicit resolution, so the module fails to import with `cannot locate symbol
   "PyExc_ImportError"`. **Workaround:** pass
   `-DPython3_SABI_LIBRARY=/data/data/com.termux/files/usr/lib/libpython3.so` via `CMAKE_ARGS`
   (scikit-build-core's documented passthrough). That path is Termux's version-independent
   stable-ABI shim, not a specific Python minor version, so it doesn't need bumping when Termux
   updates its `python` package. `CMAKE_ARGS` (a command-line `-D`) was used instead of
   `LDFLAGS`/env-seeded cache variables because scikit-build-core reuses an incremental build
   directory across `uv sync` runs, and CMake only seeds a cache variable from the environment
   on a variable's *first* configure -- a later env change is silently ignored once the
   variable is already cached, whereas a `-D` on the command line always wins.
3. **[Nucleation#37](https://github.com/Schem-at/Nucleation/issues/37)** -- With `rendering`
   enabled, wgpu's Android GPU backend calls `ANativeWindow_setBuffersGeometry` (and the rest
   of that family) from `libandroid.so`. `CMakeLists.txt` links extra system libraries for
   `WIN32` and `APPLE` but has no `ANDROID` branch, so those calls are never linked, and the
   module fails to import with `cannot locate symbol "ANativeWindow_setBuffersGeometry"`.
   **Workaround:** pass `-DCMAKE_MODULE_LINKER_FLAGS=-landroid` via `CMAKE_ARGS`. It has to be
   `CMAKE_MODULE_LINKER_FLAGS`, not `CMAKE_SHARED_LINKER_FLAGS` -- `nanobind_add_module` builds
   a CMake `MODULE` library (it's `dlopen`'d, never linked against directly), and
   `CMAKE_SHARED_LINKER_FLAGS` silently has no effect on `MODULE` targets.

With all three workarounds applied (see `pyproject.toml`'s
`[tool.uv.extra-build-variables].nucleation` entry), the build succeeds and
`import nucleation` works, along with `Schematic.create`/`set_block`/`to_schematic_b64`/
`from_data` round-trip, `Fingerprint.compute`, and `TickSimulation.from_schematic` -- the calls
`nucleation_adapter.py` actually makes for import, sanitization, and simulation.

## A fourth finding: not an upstream bug, ours to fix separately

`nucleation_adapter.py`'s `render()` (`squid/schematics/infrastructure/nucleation_adapter.py`)
calls `nucleation.RenderConfig.create()` with no arguments, then `.set_isometric(...)` and
`.set_background(...)`. None of those three calls match the installed 0.10.14 API:
`RenderConfig.create` requires `(width: int, height: int)`, and neither `set_isometric` nor
`set_background` exist on `RenderConfig` at all (only `set_yaw`/`set_pitch`/`set_zoom`/
`set_sphere_fit`/`set_fov`/`set_directional_light`). `render()` will raise `TypeError` /
`AttributeError` immediately on any call, on any platform -- this isn't Android-specific.

The rendering surface evidently changed shape after `render()` was first written (git history:
`8466182c`, "schematics: render submission previews") and before the `nucleation` pin was
bumped to 0.10.14 in `06f55f90`. This needs its own fix -- mapping the isometric framing and
background color onto the current `create(width, height)` + yaw/pitch/zoom/fov API -- tracked
separately from the build issues above since it's application code, not a packaging problem.
