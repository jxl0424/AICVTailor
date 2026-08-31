import pytest

from aicvtailor.config import reload_config


@pytest.fixture(autouse=True)
def _clean_config_cache():
    """Config is lru_cached, so a test that changes env must not leak into the
    next one."""
    reload_config()
    yield
    reload_config()
