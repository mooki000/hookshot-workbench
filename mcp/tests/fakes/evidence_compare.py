"""Synthetic comparison contract; the production adapter imports the installed rules."""
def compare_paired(runs, scenario, shared):
    assert [row["role"] for row in runs] == ["candidate", "reference", "candidate", "reference"]
    assert scenario["repeat_tolerance"] == .05
    if any(shared.classification(row["result"], scenario) != "good" for row in runs):
        return {"state": "not-judged", "reason": "shared validity/quiet rule"}
    values = [row["result"]["value"] for row in runs]
    new, previous = values[::2], values[1::2]
    if not shared.repeats_agree(new, .05) or not shared.repeats_agree(previous, .05):
        return {"state": "not-judged", "reason": "repeat tolerance"}
    gaps = [a - b for a, b in zip(new, previous)]
    spread = max(new) - min(new) + max(previous) - min(previous)
    state = "within-repeat-spread"
    if min(gaps) > spread and min(new) > max(previous):
        assert shared.paired_decision(runs, scenario, "good") == "bad"
        state = "regression"
    elif max(gaps) < -spread and max(new) < min(previous):
        state = "improvement"
    return {"state": state, "new": new, "previous": previous, "paired_gaps_worse_units": gaps,
            "combined_repeat_spread": spread, "repeat_tolerance": .05}
