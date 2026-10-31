#!/data/data/com.termux/files/usr/bin/sh
set -eu

if [ "$(uname -o)" != Android ]; then
    echo "Run this wrapper inside Termux." >&2
    exit 1
fi
: "${PREFIX:?Termux must set PREFIX}"

repo_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export UV_CACHE_DIR="${UV_CACHE_DIR:-$repo_dir/.cache/uv}"
export UV_LINK_MODE="${UV_LINK_MODE:-copy}"
export ANDROID_API_LEVEL="${ANDROID_API_LEVEL:-24}"
# Release LTO can exhaust the device's memory when building Rust tools.
export CARGO_PROFILE_RELEASE_LTO="${CARGO_PROFILE_RELEASE_LTO:-off}"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="$repo_dir/scripts/android-link-with-libpython3.sh"

# Nucleation 0.10.14: upstream issues #36–38; see docs/plans/completed/nucleation-android-build.md.
export NUCLEATION_FEATURES="bridge,meshing,simulation,rendering,scripting-lua,voxelize,world-segment,store-ssh,mc-tick,routing,hdl"
export CMAKE_ARGS="${CMAKE_ARGS:+$CMAKE_ARGS }-DPython3_SABI_LIBRARY=$PREFIX/lib/libpython3.so -DCMAKE_MODULE_LINKER_FLAGS=-landroid"

if [ "$#" -eq 0 ]; then
    set -- uv sync --locked
fi
exec "$@"
