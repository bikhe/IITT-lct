"""Общие фикстуры тестов."""

import pytest

from app.cli import load_normalized_dataset
from app.domain.models import Engineer, Order
from tests.helpers import REGIONS


@pytest.fixture(scope="session")
def datasets() -> dict[str, tuple[list[Order], list[Engineer]]]:
    return {reg: load_normalized_dataset(reg) for reg in REGIONS}
