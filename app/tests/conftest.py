import pytest

from brainhub.config import Paths


@pytest.fixture
def paths(tmp_path):
    p = Paths(tmp_path)
    p.data.mkdir(parents=True, exist_ok=True)
    p.brain.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def store(paths):
    from brainhub.store import Store
    s = Store(paths.db)
    s.init_schema()
    return s
