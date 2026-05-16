from pathlib import Path

import pytest


@pytest.fixture
def sample_md_dir() -> Path:
    return Path(__file__).parent / "sample_md"
