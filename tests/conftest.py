from __future__ import annotations

from pathlib import Path

import pytest

from scholaros.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path / "data",
        model="test-model",
        api_base="https://example.invalid/v1",
        api_key_env="SCHOLAROS_TEST_MISSING_KEY",
        contact_email="test@example.com",
        semantic_scholar_api_key=None,
        ieee_xplore_api_key=None,
    )
