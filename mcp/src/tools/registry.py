"""Add a group module with SPECS/register, then one entry in GROUPS."""
from importlib import import_module
from src.policy import Safety, check_spec

GROUPS = ("status", "checks", "evidence", "knowledge", "session", "telemetry")


def select(specs, profile, extra_tools=()):
    if profile not in {"observe", "operate"}:
        raise ValueError("Unknown tool profile: " + profile)
    unknown = set(extra_tools) - specs.keys()
    if unknown:
        raise ValueError("Unknown extra tools: " + ", ".join(sorted(unknown)))
    return frozenset({name for name, spec in specs.items() if spec.safety == Safety.READ or profile == "operate"} | set(extra_tools))


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
    deps.specs = specs
    deps.selected = select(specs, deps.config.profile, deps.config.extra_tools)
    return groups


def register(mcp, deps, groups):
    for group in groups:
        group.register(mcp, deps)
