#!/bin/bash
# What an LS build from source needs on this machine (Arch) that the system does not have, fetched into one folder
# of the work area, nothing outside it (no pacman, no sudo, no pip outside that folder). Written 2026-11-10 (HsWorkbench).
#
# Why not scripts/linux/deps.sh: it writes only under $DEPS, but it is written for Ubuntu 22.04 (the AppImage's
# system): its Tk step copies /usr/lib/x86_64-linux-gnu/libXft.so.2 and its licence step /usr/share/doc/libxft2/,
# Debian paths that do not exist here; dav1d needs meson + ninja and SDL2 cmake + ninja, none installed here. A source
# build does not need those parts: the system has clang/lld 23, FFmpeg 9.0.2 with dav1d (pkg-config), sdl2-compat 2.32
# over SDL3 3.4, Vulkan headers 1.4.363 and a libzstd.pc. Missing are only the SPIR-V headers, VMA, and a Python with
# the pinned packages. This script takes those three from deps.sh's own pins and checksums (read from the worktree's
# deps.sh at run time, so it follows a pin change):
#   python/   python-build-standalone CPython 3.12 + requirements.txt (deps.sh's python step, same download and SHA-256)
#   cpm/      Vulkan-Headers, SPIRV-Headers, VulkanMemoryAllocator in the CPM layout (deps.sh's cpm step, same tree hashes)
#
#   deps-local.sh <game worktree> [<deps folder, default /home/mooki/Code/hookshot-wt/deps>]
# Then: RX_CPM=<deps>/cpm and <deps>/python/bin/python3 for every nx2/ command.
set -euo pipefail
WT=$(cd "${1:?worktree}" && pwd)
DEPS=${2:-/home/mooki/Code/hookshot-wt/deps}
case "$DEPS" in /home/mooki/Code/hookshot-wt/*) ;; *) echo "deps folder must be inside /home/mooki/Code/hookshot-wt/"; exit 2 ;; esac
D=$WT/scripts/linux/deps.sh
pin() { sed -n "s/^$1=\([^ ]*\).*/\1/p" "$D" | head -1; }
PY_TAG=$(pin PY_TAG) PY_VER=$(pin PY_VER) PY_SHA256=$(pin PY_SHA256)
VK_HEADERS=$(pin VK_HEADERS) SPIRV_HEADERS=$(pin SPIRV_HEADERS) VMA=$(pin VMA)
VK_HEADERS_TREE=$(pin VK_HEADERS_TREE) SPIRV_HEADERS_TREE=$(pin SPIRV_HEADERS_TREE) VMA_TREE=$(pin VMA_TREE)
DL=$DEPS/downloads
mkdir -p "$DL"
CURL=(curl -fL --retry 5 --retry-delay 10 --proto '=https')
fetch() {   # url file [sha256]
    [ -f "$DL/$2" ] || { "${CURL[@]}" -o "$DL/$2.part" "$1" && mv -f "$DL/$2.part" "$DL/$2"; }
    if [ -n "${3:-}" ] && ! echo "$3  $DL/$2" | sha256sum -c --quiet -; then rm -f "$DL/$2"; echo "$2: SHA-256 differs"; exit 1; fi
}
tree_is() {   # folder sha256 name (deps.sh's check: files by name and content, no links)
    local got other
    other=$(find "$1" ! -type f ! -type d -print -quit)
    got=$(cd "$1" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)
    if [ -n "$other" ] || [ "$got" != "$2" ]; then rm -rf "$1" "$DL/$3"; echo "$3: tree $got, expected $2"; exit 1; fi
}
done_for() { [ -f "$DEPS/.$1" ] && [ "$(cat "$DEPS/.$1")" = "$2" ]; }
mark() { echo "$2" > "$DEPS/.$1"; }

PY_MARK="$PY_VER-$PY_TAG-$(cd "$WT" && sha256sum requirements.txt nx2/prompt_requirements.txt | sha256sum | cut -c1-12)"
if ! done_for python "$PY_MARK"; then
    fetch "https://github.com/astral-sh/python-build-standalone/releases/download/$PY_TAG/cpython-$PY_VER+$PY_TAG-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz" python.tar.gz "$PY_SHA256"
    rm -rf "$DEPS/python"
    tar -xzf "$DL/python.tar.gz" -C "$DEPS"
    (cd "$WT" && "$DEPS/python/bin/python3" -m pip install --no-cache-dir --only-binary :all: --disable-pip-version-check -q -r requirements.txt)
    mark python "$PY_MARK"
fi
if ! done_for cpm "$VK_HEADERS-$SPIRV_HEADERS-$VMA"; then
    rm -rf "$DEPS/cpm"
    get_headers() {   # repo tag name tree
        fetch "https://github.com/$1/archive/refs/tags/$2.tar.gz" "$3-$2.tar.gz"
        mkdir -p "$DEPS/cpm/$3/$2"
        tar -xzf "$DL/$3-$2.tar.gz" -C "$DEPS/cpm/$3/$2" --strip-components=1
        tree_is "$DEPS/cpm/$3/$2" "$4" "$3-$2.tar.gz"
    }
    get_headers KhronosGroup/Vulkan-Headers "$VK_HEADERS" vulkanheaders "$VK_HEADERS_TREE"
    get_headers KhronosGroup/SPIRV-Headers "$SPIRV_HEADERS" spirv-headers "$SPIRV_HEADERS_TREE"
    get_headers GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator "$VMA" vulkanmemoryallocator "$VMA_TREE"
    mark cpm "$VK_HEADERS-$SPIRV_HEADERS-$VMA"
fi
"$DEPS/python/bin/python3" -I -c 'import sys, lz4, numpy, capstone, unicorn, zstandard, fontTools, PIL; print("python", sys.version.split()[0], "numpy", numpy.__version__, "unicorn", unicorn.__version__)'
echo "deps in $DEPS: $(du -sh --exclude=downloads "$DEPS" | cut -f1); RX_CPM=$DEPS/cpm"
