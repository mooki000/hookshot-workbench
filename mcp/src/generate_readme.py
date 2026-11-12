"""Generate README catalogue tables from the real registered in-memory server."""
import argparse
import asyncio
from pathlib import Path
import tempfile
from fastmcp import Client
from src.config import Config
from src.deps import Deps
from src.main import build_server

START = "<!-- BEGIN GENERATED CATALOGUE -->"
END = "<!-- END GENERATED CATALOGUE -->"


def cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


async def catalogue() -> str:
    rows = {}
    resources = {}
    prompts = {}
    with tempfile.TemporaryDirectory(prefix="workbench-catalogue-") as folder:
        for profile in ("observe", "operate"):
            deps = Deps(Config(state_root=Path(folder) / profile, profile=profile))
            async with Client(build_server(deps=deps)) as client:
                for tool in await client.list_tools():
                    entry = rows.setdefault(tool.name, {"tool": tool, "group": deps.groups[tool.name],
                        "safety": deps.specs[tool.name].safety.value, "profiles": [],
                        "broker": deps.specs[tool.name].requires_broker, "route": deps.specs[tool.name].shell_route})
                    entry["profiles"].append(profile)
                for resource in await client.list_resources():
                    key = str(resource.uri)
                    resources.setdefault(key, {"description": resource.description or "", "profiles": []})["profiles"].append(profile)
                for template in await client.list_resource_templates():
                    key = template.uri_template
                    resources.setdefault(key, {"description": template.description or "", "profiles": []})["profiles"].append(profile)
                for prompt in await client.list_prompts():
                    prompts.setdefault(prompt.name, {"description": prompt.description or "", "profiles": []})["profiles"].append(profile)
    text = [START, "", "### Tools", "", "| Tool | Group | Safety | Profiles | CLI broker | Required arguments | Replaces |", "|---|---|---|---|---|---|---|"]
    for name, entry in sorted(rows.items()):
        tool = entry["tool"]
        required = ", ".join(tool.input_schema.get("required", [])) or "none"
        text.append("| " + " | ".join(map(cell, (f"`{name}`", entry["group"], entry["safety"], ", ".join(entry["profiles"]),
            "yes" if entry["broker"] else "no", required, entry["route"]))) + " |")
    text.extend(["", "### Resources", "", "| URI or template | Profiles | Purpose |", "|---|---|---|"])
    for uri, entry in sorted(resources.items()):
        text.append("| " + " | ".join(map(cell, (f"`{uri}`", ", ".join(entry["profiles"]), entry["description"]))) + " |")
    text.extend(["", "### Prompts", "", "| Prompt | Profiles | Purpose |", "|---|---|---|"])
    for name, entry in sorted(prompts.items()):
        text.append("| " + " | ".join(map(cell, (f"`{name}`", ", ".join(entry["profiles"]), entry["description"]))) + " |")
    text.extend(["", END])
    return "\n".join(text)


def replace_catalogue(existing: str, generated: str) -> str:
    before, marker, rest = existing.partition(START)
    if not marker or END not in rest:
        raise ValueError("README catalogue markers are missing")
    _, _, after = rest.partition(END)
    return before + generated + after


def catalogue_document(generated: str) -> str:
    return "# Registered workbench MCP catalogue\n\nGenerated from the real registered server; do not edit by hand.\n" + generated.removeprefix(START).removesuffix(END).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = asyncio.run(catalogue())
    path = Path(__file__).resolve().parents[1] / "README.md"
    catalogue_path = path.with_name("CATALOGUE.md")
    document = catalogue_document(generated)
    if args.write or args.check:
        existing = path.read_text()
        updated = replace_catalogue(existing, generated)
        if args.write:
            path.write_text(updated)
            catalogue_path.write_text(document)
        elif updated != existing or not catalogue_path.exists() or catalogue_path.read_text() != document:
            raise SystemExit("Generated catalogue is stale; run python -m src.generate_readme --write")
    else:
        print(generated)


if __name__ == "__main__":
    main()
