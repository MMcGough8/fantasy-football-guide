import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name)) as f:
        return json.load(f)


@pytest.fixture
def league():
    return load_fixture("league.json")


@pytest.fixture
def draft():
    return load_fixture("draft.json")


@pytest.fixture
def projections():
    return load_fixture("projections_sample.json")


@pytest.fixture
def picks():
    return load_fixture("picks_sample.json")
