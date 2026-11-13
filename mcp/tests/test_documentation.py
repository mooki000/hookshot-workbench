import json
from importlib.metadata import distribution
from pathlib import Path
import tomllib
from src.generate_readme import catalogue, catalogue_document, replace_catalogue


async def test_readme_catalogue_is_current():
    readme = Path(__file__).resolve().parents[1] / "README.md"
    existing = readme.read_text()
    generated = await catalogue()
    assert replace_catalogue(existing, generated) == existing
    assert readme.with_name("CATALOGUE.md").read_text() == catalogue_document(generated)


def test_ls_package_and_client_identities():
    project = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    name = "ls-workbench-mcp"
    scripts = {"ls-workbench": "src.main:main", "ls-wb": "src.cli:main"}
    assert metadata["name"] == name
    assert metadata["scripts"] == scripts
    installed = distribution(name)
    assert installed.metadata["Name"] == name
    assert {entry.name: entry.value for entry in installed.entry_points
            if entry.group == "console_scripts"} == scripts
    locked = tomllib.loads((project / "uv.lock").read_text())["package"]
    assert [package["name"] for package in locked if package.get("source") == {"editable": "."}] == [name]
    clients = json.loads((project.parent / ".mcp.json.example").read_text())["mcpServers"]
    assert set(clients) == {"ls-workbench-observe", "ls-workbench-operate"}
    for profile in ("observe", "operate"):
        client = clients[f"ls-workbench-{profile}"]
        assert client["args"][-1] == "ls-workbench"
        assert client["env"]["LS_WB_PROFILE"] == profile
