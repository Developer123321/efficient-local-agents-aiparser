import pytest

ENVIRONMENT = (
    "SMALL_MODEL_API_KEY",
    "SMALL_MODEL_API_KEY_FILE",
    "SMALL_MODEL_BASE_URL",
    "SMALL_MODEL_NAME",
    "TYPESAFE_API_KEY",
    "TYPESAFE_API_KEY_FILE",
    "HACKLAB_CACHE_MODE",
    "HACKLAB_DB",
    "HACKLAB_TRACE_DIR",
)


@pytest.fixture(autouse=True)
def isolated_from_the_developers_environment(monkeypatch):
    """Tests never see the real `.env`: no key, no endpoint, no cache, no trace."""
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("hacklab.cli.load_dotenv", lambda *args, **kwargs: False)
