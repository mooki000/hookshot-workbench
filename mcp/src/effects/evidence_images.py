"""Offline capture and panel adapters. Producing an image is not inspecting it."""
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
from src.effects.evidence_common import (IMAGE_MIMES, artifact_input, backend, configured,
    load_backend, producer, write_new)


def _image_size(path):
    with path.open("rb") as stream:
        prefix = stream.read(24)
        if prefix[:8] == b"\x89PNG\r\n\x1a\n" and prefix[12:16] == b"IHDR":
            return struct.unpack(">II", prefix[16:24])
        if prefix[:2] != b"\xff\xd8":
            raise ValueError("Unsupported image")
        stream.seek(2)
        for _ in range(10000):
            marker = stream.read(2)
            if len(marker) != 2 or marker[0] != 255:
                raise ValueError("Invalid image marker")
            while marker[1] == 255:
                marker = b"\xff" + stream.read(1)
            if marker[1] in (0xD9, 0xDA):
                break
            size = struct.unpack(">H", stream.read(2))[0]
            if size < 2:
                break
            if marker[1] in {0xC0, 0xC1, 0xC2}:
                _, h, w = struct.unpack(">BHH", stream.read(5))
                return w, h
            stream.seek(size - 2, 1)
    raise ValueError("Missing image dimensions")


def evidence_panels_impl(deps, images, layout, label, root):
    tool = "evidence_panels"
    script = configured(deps, "panels_script", tool)
    output = deps.paths.output(root / "panel.jpg")
    argv = [str(script), str(output), str(layout.per_row)]
    hashes, captions = {}, []
    for index, image in enumerate(images):
        art, path = artifact_input(deps, image.artifact_id, tool, mimes=IMAGE_MIMES, max_bytes=16 * 1024 * 1024)
        try:
            w, h = _image_size(path)
            if not 0 < w <= 8192 or not 0 < h <= 8192 or w * h > 33554432:
                raise ValueError
        except (ValueError, struct.error):
            raise deps.error("invalid_argument", tool) from None
        extension = ".png" if art.mime == "image/png" else ".jpg"
        # Neutral names avoid image-loader path syntax; originals are never output arguments.
        copied = write_new(deps, root / (f"source_{index}" + extension), path.read_bytes())
        argv.extend((str(copied), image.caption))
        hashes[art.id] = art.sha256
        captions.append(image.caption)
    env = dict(os.environ, PANEL_W=str(layout.width), PANEL_H=str(layout.height))
    env.pop("PANEL_CROP", None)
    if layout.crop:
        env["PANEL_CROP"] = layout.crop
    with (root / "panels-output.txt").open("xb") as log:
        result = subprocess.run(argv, env=env, cwd=root, stdout=log, stderr=log, timeout=300)
    if result.returncode != 0 or not output.is_file():
        raise deps.error("backend_failed", tool)
    _image_size(output)
    art = deps.registry.add_artifact(output, guards=deps.paths, mime="image/jpeg", producer=producer(tool, script), complete=True)
    return {"artifact": art.model_dump(), "source_hashes": hashes, "captions": captions,
            "label": label, "inspection": "pending"}


def evidence_scan_frames_impl(deps, capture_artifact_id, frame_window, detector, root):
    tool = "evidence_scan_frames"
    script = backend(deps, "scripts/framelog.py", tool, checkout=True)
    python = configured(deps, "dependency_python", tool)
    art, path = artifact_input(deps, capture_artifact_id, tool,
        mimes={"application/x-hookshot-framelog"}, max_bytes=1024 * 1024 * 1024)
    if detector != "brightness-triplet-v1":
        raise deps.error("invalid_argument", tool)
    parser = load_backend(script)
    try:
        info = parser.capture_info(path)
    except (ValueError, struct.error):
        raise deps.error("invalid_argument", tool) from None
    first, last = (frame_window.first, frame_window.last) if frame_window else (0, len(info.sequences) - 1)
    if last >= len(info.sequences) or last - first < 2:
        raise deps.error("invalid_argument", tool)
    selected = root / "capture.bin"
    fd = deps.paths.reserve_file(selected)
    with os.fdopen(fd, "wb") as output, path.open("rb") as source:
        source.seek(first * parser.SIZE)
        left = (last - first + 1) * parser.SIZE
        while left:
            chunk = source.read(min(left, 1024 * 1024))
            if not chunk:
                raise deps.error("invalid_argument", tool)
            output.write(chunk)
            left -= len(chunk)
    cropped = parser.capture_info(selected)
    out = deps.paths.output(root / "largest-strips.png")
    text_path = root / "frame-analysis.txt"
    with text_path.open("xb") as log:
        call = subprocess.run([str(python), "-I", str(script), str(selected), str(out)],
            cwd=root, stdout=log, stderr=log, timeout=420)
    if call.returncode != 0:
        raise deps.error("unavailable" if b"requires NumPy" in text_path.read_bytes()[:4096] else "backend_failed", tool)
    if text_path.stat().st_size > 65536:
        raise deps.error("backend_failed", tool)
    text = text_path.read_text(errors="replace")
    events = []
    pattern = re.compile(r"^frame (\d+) \((game|made)\) (brighter|darker) (\d+) blocks, peak (\d+), at ([\d.]+) s$", re.M)
    for match in pattern.finditer(text):
        index, kind, sign, blocks, peak, seconds = match.groups()
        index = int(index)
        if not 0 <= index < len(cropped.sequences):
            raise deps.error("backend_failed", tool)
        events.append({"record": first + index, "sequence": cropped.sequences[index], "kind": kind,
            "direction": sign, "blocks": int(blocks), "peak_luma": int(peak), "relative_seconds": float(seconds)})
    totals = re.search(r"events: (\d+) on the game's frames, (\d+) on made frames", text)
    if totals is None:
        raise deps.error("backend_failed", tool)
    total = int(totals[1]) + int(totals[2])
    gaps = [{"after_record": first + i, "missing": ((b - a) & parser.MASK) - 1}
            for i, (a, b) in enumerate(zip(cropped.sequences, cropped.sequences[1:])) if ((b - a) & parser.MASK) != 1]
    artifacts = []
    if out.exists():
        artifacts.append(deps.registry.add_artifact(out, guards=deps.paths, mime="image/png",
            producer=producer(tool, script), run_id=art.run_id, clock=art.clock, complete=True).model_dump())
    return {"events": events[:24], "events_total": total, "events_truncated": total > len(events[:24]),
        "sizes": {"width": parser.W, "height": parser.H, "records": len(cropped.sequences)},
        "gaps": gaps[:100], "gaps_truncated": len(gaps) > 100, "missing_frames": cropped.missing,
        "eligible_triplets": len(cropped.centres), "largest_strips": artifacts,
        "coverage": {"first_record": first, "last_record": last, "clock": "command-recording",
            "first_timestamp_ns": cropped.timestamps[0], "last_timestamp_ns": cropped.timestamps[-1],
            "source_sha256": art.sha256, "contiguous": cropped.missing == 0,
            "limitation": "Only retained consecutive triplets were tested; sparse frames cannot establish absence of flashes."},
        "requires_visual_review": True, "inspection": "pending", "producer": producer(tool, script).model_dump()}
