# Source this file; names are resolved by the shared Python module, never the path's spelling.
HS_NAMES_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
hs_names() {
    local assignments
    assignments=$(python3 -I -B "$HS_NAMES_DIR/hs_names.py" "$@") || return
    eval "$assignments"
}
hs_build_env() {
    local assignments
    assignments=$(python3 -I -B "$HS_NAMES_DIR/hs_names.py" checkout "$@") || return
    eval "$assignments"
}
hs_protected_user_paths() {
    python3 -I -B "$HS_NAMES_DIR/hs_names.py" protected "$1"
}
