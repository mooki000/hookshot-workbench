"""Add a group module with SPECS/register, then one entry in GROUPS."""
from importlib import import_module
from src.policy import Safety, check_spec

GROUPS = ("status", "checks", "evidence", "knowledge", "session")


def configure(deps):
    groups = [import_module("src.tools." + name) for name in GROUPS]
    specs = {}
    for group in groups:
        for spec in group.SPECS:
            check_spec(spec)
            if spec.name in specs:
                raise ValueError("Duplicate tool: " + spec.name)
            specs[spec.name] = spec
            deps.groups[spec.name] = group.__name__.rsplit(".", 1)[-1]
    if deps.config.profile not in {"observe", "operate"}:
        raise ValueError("Unknown tool profile: " + deps.config.profile)
    unknown = set(deps.config.extra_tools) - specs.keys()
    if unknown:
        raise ValueError("Unknown extra tools: " + ", ".join(sorted(unknown)))
    selected = {name for name, spec in specs.items() if spec.safety == Safety.READ or deps.config.profile == "operate"}
    selected.update(deps.config.extra_tools)
    deps.specs = specs
    deps.selected = frozenset(selected)
    return groups


def register(mcp, deps, groups):
    for group in groups:
        group.register(mcp, deps)
