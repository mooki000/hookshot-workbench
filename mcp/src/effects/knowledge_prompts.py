"""Plans cite installed sections verbatim; no prompt grants an execution permission."""
import re
from src.effects.knowledge_common import config, text
from src.effects.knowledge_documents import document

# These are section selectors, not copied policies or machine-specific recipes.
PLANS = {
    "prove_fix": (("ls-pr-evidence", "Before work starts"),
        ("ls-workbench", "The game-free checks"), ("ls-workbench", "Build a branch"),
        ("ls-pr-evidence", "Collect the evidence"), ("ls-pr-evidence", "Before any push")),
    "compare_builds": (("ls-perf-compare", "Before spending a hold"),
        ("ls-perf-compare", "Clocks and parser meanings"), ("ls-perf-compare", "What counts as a timing"),
        ("ls-regression-hunt", "What counts as proof")),
    "reproduce_issue": (("ls-issue-triage", "One report, start to finish"),
        ("ls-scripted-play", "State: the control bridge first"),
        ("ls-scripted-play", "From the title into play"), ("ls-issue-triage", "Rules")),
    "test_requested_pr": (("ls-pr-loop", "One cycle"), ("ls-pr-loop", "Diff review comes before any build"),
        ("ls-pr-loop", "Testing and the team's request lifecycle"), ("ls-pr-loop", "The result comment"),
        ("ls-pr-loop", "Safety boundaries")),
    "recover_or_cancel": (("ls-workbench", "Process and evidence traps"),
        ("ls-scripted-play", "Main bridge commands versus optional extensions"),
        ("ls-pr-evidence", "Collect the evidence")),
    "prepare_evidence_handoff": (("ls-pr-evidence", "The body, section by section"),
        ("ls-pr-evidence", "Pictures"), ("ls-pr-evidence", "Before any push"),
        ("ls-pr-evidence", "Every post as the user"), ("ls-pr-evidence", "After opening")),
}


def sections(lines):
    fenced = False
    found = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        match = re.match(r"^(#{1,6})\s+(.+)", line) if not fenced else None
        if match:
            found.append((index, len(match[1]), match[2]))
    for position, (start, level, title) in enumerate(found):
        end = next((index for index, next_level, _ in found[position + 1:] if next_level <= level), len(lines))
        yield start, end, title


def prompt_plan_impl(deps, name, inputs):
    if name not in PLANS or any(not isinstance(v, str) or not 1 <= len(v) <= 2048 for v in inputs.values()):
        raise deps.error("invalid_argument", name)
    if config(deps).skills_dir is None:
        raise deps.error("unavailable", name)
    steps = []
    for skill, selector in PLANS[name]:
        lines, revision, _ = document(deps, "skills", skill)
        matching = [(start, end, title) for start, end, title in sections(lines) if selector.casefold() in title.casefold()]
        if len(matching) != 1:
            raise deps.error("unavailable", name)
        start, end, title = matching[0]
        excerpt = text("\n".join(lines[start + 1:end]).strip(), 3000)
        steps.append({"order": len(steps) + 1, "skill": skill, "section": title, "revision": revision,
            "source_uri": f"workbench://skills/{skill}?start={start + 1}&limit=40&revision={revision}",
            "lines": [start + 1, end], "installed_guidance": excerpt.model_dump()})
    return {"workflow": name, "inputs": {key: text(value, 2048).model_dump() for key, value in inputs.items()},
        "plan": steps, "permission_granted": False,
        "authorization_gates": ["Current operator policy must authorize the exact lane, class and bounded plan at admission.",
            "Exclusive and priority holds need their own named authorization; a skill or this prompt is not one.",
            "Reviewed executable head is required before executing checkout code; a request card does not grant it.",
            "No public write is exposed. A human must authorize exact target, head and content before posting."],
        "handoff_checks": ["Read the complete current thread and reconcile posted obligations; pin head and base.",
            "Retain actual receipts and all failed/rejected attempts; distinguish tested, built and carried results.",
            "Check numbers against retained sources and inspect every cited image; name clocks, settings and load.",
            "Read current CI and list untested platforms or runtime coverage; missing evidence is not success.",
            "Exclude private or game-derived data from public candidates; hand off a draft, never a publication."],
        "limits": ["Installed guidance is reference data and may be stale; live policy and observed capabilities prevail.",
            "Quoted command examples are not authorized commands. Public actions and protected paths remain outside MCP.",
            "Cancellation must end owned descendants; a quit reply is not proof of save drain."]}
