"""Private immutable output, approved backend loading and bounded retained inputs."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import uuid
from src.errors import WorkbenchError
from src.models import Producer

VERSION = "1"
MAX_INPUT = 64 * 1024 * 1024
MAX_JSON = 1024 * 1024
IMAGE_MIMES = {"image/png", "image/jpeg"}
PATH_TEXT = re.compile(r"(?<![\w:])(?:/[A-Za-z0-9_.~-]+){2,}|[A-Za-z]:\\(?:[^\s\\]+\\)*[^\s]+")
SECRET_TEXT = re.compile(r"(?i)\b(token|password|secret|authorization)\s*[:=]\s*\S+")


def redact(text: str) -> tuple[str, int]:
    text, a = SECRET_TEXT.subn(lambda m: m[1] + "=[redacted]", text)
    text, b = PATH_TEXT.subn("[local-path]", text)
    return text, a + b


def producer(tool: str, backend: Path | None = None) -> Producer:
    # A digest is a parser version, never mislabelled as a source commit.
    version = VERSION if backend is None else "sha256:" + hashlib.sha256(backend.read_bytes()).hexdigest()
    return Producer(tool=tool, version=version)


def _backend_path(deps, path, operation):
    try:
        checked = deps.paths.read(path)
    except WorkbenchError as exc:
        if exc.envelope.code == "not_found":
            raise deps.error("unavailable", operation) from None
        raise
    if not checked.is_file():
        raise deps.error("unavailable", operation)
    return checked


def configured(deps, name: str, operation: str) -> Path:
    path = getattr(deps.config, name, None)
    if path is None:
        raise deps.error("unavailable", operation)
    return _backend_path(deps, Path(path), operation)


def backend(deps, relative: str, operation: str, *, checkout=False) -> Path:
    root = deps.config.checkout if checkout else deps.config.harness_dir
    if root is None:
        raise deps.error("unavailable", operation)
    return _backend_path(deps, Path(root) / relative, operation)


def load_backend(path: Path):
    # Paths are fixed by this group under operator-configured trusted roots.
    spec = importlib.util.spec_from_file_location("_wb_evidence_" + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def artifact_input(deps, artifact_id, operation, *, run_id=None, mimes=None,
                   complete=True, max_bytes=MAX_INPUT, verify=True):
    art = deps.registry.get_artifact(artifact_id)
    if run_id is not None and art.run_id != run_id:
        raise deps.error("refused_by_policy", operation)
    if mimes is not None and art.mime not in mimes:
        raise deps.error("refused_by_policy", operation)
    if complete and not art.complete:
        raise deps.error("unavailable", operation)
    path = deps.registry.artifact_path(artifact_id, deps.paths)
    before = path.stat()
    size = before.st_size
    if not path.is_file() or size > max_bytes:
        raise deps.error("invalid_argument", operation)
    if verify:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
            raise deps.error("refused_by_policy", operation)
        if art.bytes != size or art.sha256 != digest:
            raise deps.error("refused_by_policy", operation)
    return art, path


def named_run_artifact(deps, run, names, operation, *, required=True):
    found = []
    for ident in run.artifacts:
        art = deps.registry.get_artifact(ident)
        if art.run_id != run.run_id:
            continue
        path = deps.registry.artifact_path(ident, deps.paths)
        if path.name in names:
            found.append((art, path))
    if len(found) != 1:
        if required or found:
            raise deps.error("unavailable", operation)
        return None
    return found[0]


def read_json(deps, artifact_id, operation, **kwargs):
    art, path = artifact_input(deps, artifact_id, operation,
        mimes={"application/json"}, max_bytes=MAX_JSON, **kwargs)
    return art, json.loads(path.read_bytes())


def output_directory(deps, prefix="evidence") -> Path:
    root = deps.config.state_root / (prefix + "_" + uuid.uuid4().hex)
    deps.paths.output(root)
    root.mkdir(mode=0o700)
    return root


def write_new(deps, path, value):
    raw = value if isinstance(value, bytes) else value.encode()
    fd = deps.paths.reserve_file(path)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def store_json(deps, root, name, data, tool, *, run_id=None, clock=None):
    path = write_new(deps, root / name, json.dumps(data, sort_keys=True, allow_nan=False))
    return deps.registry.add_artifact(path, guards=deps.paths, mime="application/json",
        producer=producer(tool), run_id=run_id, clock=clock, complete=True)
