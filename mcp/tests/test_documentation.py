from pathlib import Path
from src.generate_readme import catalogue, replace_catalogue


async def test_readme_catalogue_is_current():
    readme = Path(__file__).resolve().parents[1] / "README.md"
    existing = readme.read_text()
    assert replace_catalogue(existing, await catalogue()) == existing
