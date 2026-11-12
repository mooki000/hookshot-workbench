"""Synthetic regression API recording adapter calls; no runnable regression harness."""
import json
from pathlib import Path


def classification(result, scenario):
    if result["valid"] is not True or result["kernel_errors"] != 0:
        return "invalid"
    if result["load_before"] is None or result["load_after"] is None:
        return "invalid"
    if result["disturbed"] is not False or result["others_cores_window"] >= 3 or result["others_cores_peak"] > 6:
        return "disturbed"
    return "good"


def repeats_agree(values, tolerance):
    return min(values) > 0 and max(values) / min(values) - 1 <= tolerance


def paired_decision(runs, scenario, reference):
    Path(__file__).with_name("paired-called.json").write_text(json.dumps({"reference": reference, "scenario": scenario}))
    return "bad"
