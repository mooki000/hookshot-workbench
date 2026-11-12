from pathlib import Path
from src.generate_readme import catalogue, catalogue_document, replace_catalogue


async def test_readme_catalogue_is_current():
    readme = Path(__file__).resolve().parents[1] / "README.md"
    existing = readme.read_text()
    generated = await catalogue()
    assert replace_catalogue(existing, generated) == existing
    assert readme.with_name("CATALOGUE.md").read_text() == catalogue_document(generated)
