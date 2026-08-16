#!/data/data/com.termux/files/usr/bin/sh
# cargo linker override for PyO3 extension modules on Termux/Android.
#
# pyo3-ffi's build script already detects Android correctly and emits
# `cargo:rustc-link-lib=python3`: bionic's dynamic linker (unlike glibc's)
# doesn't resolve CPython C-API symbols from the already-running host process,
# so the extension module needs an explicit NEEDED entry for libpython3.so
# (Termux's unversioned, version-independent stable-ABI shim) or it fails to
# import with e.g. "dlopen failed: cannot locate symbol PyLong_Type".
#
# That `-lpython3` request genuinely reaches the final linker invocation --
# confirmed by capturing the real argv cargo invokes the linker with, both
# directly and through `maturin build` -- but Termux's default clang/lld
# toolchain links with --as-needed active, which silently elides it: nothing
# earlier on that huge argv (dozens of .rlib archives, `-lssl -lcrypto -ldl
# -llog -lunwind -ldl -lm -lc`, then `-nodefaultlibs`) leaves an unresolved
# symbol at the point `-lpython3` is processed, at least in the position
# rustc/cargo place it, so the linker treats it as unneeded and drops it from
# DT_NEEDED even though PyLong_Type et al. are genuinely referenced. Passing
# the request again wrapped in --no-as-needed/--as-needed (rather than relying
# on pyo3-ffi's own, unwrapped one) is what actually survives.
#
# RUSTFLAGS and CARGO_ENCODED_RUSTFLAGS were both tried first (simpler, no
# wrapper script needed) and both still produced an unlinked .so through
# `uv sync` even though pyo3-ffi's own request should already cover it --
# consistent with this being the same --as-needed elision landing on whichever
# copy of `-lpython3` command-line position it ends up in, not something
# rustflags-injection order fixes. A linker override sidesteps the guesswork:
# every real link invocation (nothing else emits `-C linker=` for this target)
# gets `-lpython3` re-asserted in a position --as-needed can't drop it from.
# Intermediate rlib compiles never invoke a linker at all, so this is a no-op
# there.
#
# Wire this in per-package via CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER in
# pyproject.toml's [tool.uv.extra-build-variables] -- see the cryptography and
# jsonschema-rs entries there, and docs/plans/completed/nucleation-android-build.md for
# the parallel nucleation (CMake, not cargo) version of this same failure mode.
exec cc "$@" -Wl,--no-as-needed -lpython3 -Wl,--as-needed
