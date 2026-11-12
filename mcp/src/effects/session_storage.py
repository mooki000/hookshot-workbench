"""Operator-only session registration and plain-file save copies.

Catalogue paths are configuration, not MCP arguments. Clone destinations are new
owned directories; source libraries are never modified. Hashes describe a copy,
not a validated gameplay checkpoint or a save-writer barrier.
"""
import asyncio
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from src.errors import fail
from src.models.session import SessionCatalogue

@contextmanager
def lane_guard(deps, lane):
    """Serialize registration, not GPU scheduling; gate files remain untouched."""
    fd = os.open(deps.config.state_root / ("session-" + lane + ".lock"),
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise deps.error("busy", "session_launch") from None
        yield
    finally:
        os.close(fd)


MAX_FILES = 4096
MAX_SAVE_BYTES = 512 * 1024 * 1024


def read_json(path: Path, bound: int = 262144):
    if path.is_symlink():
        raise fail("refused_by_policy", "session_catalogue")
    try:
        with path.open("rb") as stream:
            raw = stream.read(bound + 1)
        if len(raw) > bound:
            raise ValueError
        return json.loads(raw)
    except FileNotFoundError:
        raise fail("unavailable", "session_catalogue") from None
    except (OSError, ValueError):
        raise fail("backend_failed", "session_catalogue") from None


def catalogue(deps) -> SessionCatalogue:
    path = getattr(deps.config, "session_catalogue", None)
    if path is None:
        raise deps.error("unavailable", "session_catalogue")
    try:
        return SessionCatalogue.model_validate(read_json(path))
    except ValueError:
        raise deps.error("unavailable", "session_catalogue") from None


def new_directory(deps, path: Path) -> Path:
    target = deps.paths.output(path)
    # Refuse every symlink component, including aliases within allowed roots.
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise deps.error("refused_by_policy", "session_path")
    try:
        target.mkdir(mode=0o700)
    except OSError:
        raise deps.error("refused_by_policy", "session_path") from None
    return target


def write_new(deps, path: Path, value: str):
    fd = deps.paths.reserve_file(path)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def plain_files(deps, root: Path, slot: int | None = None) -> list[tuple[Path, os.stat_result]]:
    deps.paths.read(root)
    if any(p.is_symlink() for p in (root, *root.parents)) or not root.is_dir():
        raise deps.error("refused_by_policy", "save_clone")
    result, total, entries, found_slot = [], 0, 0, slot is None
    for top, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            entries += 1
            if entries > MAX_FILES * 2:
                raise deps.error("invalid_argument", "save_clone")
            path = Path(top) / name
            info = path.lstat()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                raise deps.error("refused_by_policy", "save_clone")
            rel = path.relative_to(root)
            if any(len(part) > 100 for part in rel.parts) or len(rel.as_posix()) > 256:
                raise deps.error("refused_by_policy", "save_clone")
            selected = True
            for part in rel.parts:
                if re.fullmatch(r"slot_[0-9]{2}", part):
                    if slot is not None and int(part[5:]) == slot:
                        found_slot = True
                    elif slot is not None:
                        selected = False
            if stat.S_ISREG(info.st_mode) and selected:
                total += info.st_size
                result.append((path, info))
            if len(result) > MAX_FILES or total > MAX_SAVE_BYTES:
                raise deps.error("invalid_argument", "save_clone")
    if not result or not found_slot:
        raise deps.error("not_found", "save_clone")
    return result


async def clone_tree(deps, source: Path, destination: Path, slot: int | None = None,
                     progress=None) -> list[dict]:
    files = plain_files(deps, source, slot)
    new_directory(deps, destination)
    manifest = []
    for path, before in files:
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        digest = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as src, os.fdopen(deps.paths.reserve_file(target), "wb") as out:
            opened = os.fstat(src.fileno())
            if (opened.st_ino, opened.st_dev, opened.st_size, opened.st_mtime_ns) != (before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns):
                raise deps.error("busy", "save_clone")
            copied = 0
            while chunk := src.read(1024 * 1024):
                copied += len(chunk)
                if copied > before.st_size:
                    raise deps.error("uncertain_outcome", "save_clone")
                out.write(chunk)
                digest.update(chunk)
                await asyncio.sleep(0)  # cancellation boundary, not a readiness delay
            after = os.fstat(src.fileno())
            if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
                raise deps.error("uncertain_outcome", "save_clone")
            out.flush()
            os.fsync(out.fileno())
        manifest.append({"file": relative.as_posix(), "bytes": before.st_size, "sha256": digest.hexdigest()})
        if progress:
            progress(len(manifest), len(files))
    return manifest
