"""Approved signatures, not user regexes.

Sources: regress/run_case.py:30-36,281-294,481; nightly/monitor.py:108-123;
nightly/metrics.py:12-14,185. Repeat markers inherit their preceding driver
message. Allocation refusals stop on a failed run or more than three events;
they never conceal an Xid, cache-operation fault or unknown driver message.
"""
import re

FATAL = re.compile(r"FATAL|CRASH:")
DEVICE_LOST = re.compile(r"VK_ERROR_DEVICE_LOST|device lost", re.I)
DRIVER = re.compile(r"NVRM|kmemsysDoCacheOp|i915|\bxe ", re.I)
DRIVER_FAULT = re.compile(r"NVRM: Xid|kmemsysDoCacheOp|(?:i915|xe ).*(?:hang|reset|error)", re.I)
MEMMAP = "memMap_IMPL: CPU mapping not supported for addressSpace: 0x1"
ALLOCATION = re.compile(r"NV_ERR_NO_MEMORY.*_memdescAllocInternal.*mem_desc\.c\b|rmStatus.*system_mem\.c\b|pRmApi->Alloc.*nv_gpu_ops\.c\b")
REPEAT = re.compile(r"(?:last (?:print|message)|message) repeated (\d+) times", re.I)
MEMORY = re.compile(r"vram|memory budget|texture cache|(?:device|card).*memory|memory.*(?:used|budget)", re.I)
WARNINGS = re.compile(r"\b(?:warn(?:ing)?|error|fatal|crash|failed|failure|unsupported|timeout)\b|VK_ERROR_|ran without a core", re.I)
FILTERS = {"fatal": re.compile(r"FATAL|CRASH:|VK_ERROR_DEVICE_LOST|device lost", re.I),
           "device_lost": DEVICE_LOST, "memory": MEMORY,
           "readback": re.compile(r"readbacks of GPU results|readback", re.I),
           "driver": DRIVER, "warnings": WARNINGS}


def classify_kernel(lines: list[str], failed: bool | None) -> dict:
    counts = {"known": 0, "unknown": 0, "faults": 0, "repeats": 0,
              "mapping_refusals": 0, "allocation_lines": 0, "allocation_events": 0}
    selected = []
    previous = None
    allocation_starts = 0
    for line in lines:
        repeat = REPEAT.search(line)
        if repeat:
            if previous is None:
                counts["unknown"] += 1
                selected.append(line)
                continue
            category, allocation_start = previous
            if category == "ignored":
                continue
            n = min(int(repeat[1]), 2**31 - 1)
            counts["repeats"] += n
        else:
            previous = None
            if not DRIVER.search(line):
                previous = "ignored", False
                continue
            n = 1
            allocation_start = "_memdescAllocInternal" in line
            category = ("faults" if DRIVER_FAULT.search(line) else
                        "mapping_refusals" if MEMMAP in line else
                        "allocation_lines" if ALLOCATION.search(line) else "unknown")
            previous = category, allocation_start
        counts[category] += n
        if category in ("mapping_refusals", "allocation_lines"):
            counts["known"] += n
        if category == "allocation_lines" and allocation_start:
            allocation_starts += n
        selected.append(line)
    counts["allocation_events"] = max(allocation_starts, (counts["allocation_lines"] + 2) // 3)
    stop = bool(counts["faults"] or counts["unknown"] or counts["allocation_events"] > 3 or
                (counts["allocation_lines"] and failed is True))
    if not stop and counts["allocation_lines"] and failed is None:
        stop = None
    return {**counts, "stop_required": stop, "lines": selected}
