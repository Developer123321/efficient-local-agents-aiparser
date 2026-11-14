import pytest

from hacklab import providers

ENVIRONMENT = (
    "SMALL_MODEL_API_KEY",
    "SMALL_MODEL_API_KEY_FILE",
    "SMALL_MODEL_BASE_URL",
    "SMALL_MODEL_NAME",
    "TYPESAFE_API_KEY",
    "TYPESAFE_API_KEY_FILE",
    "TYPESAFE_BASE_URL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_API_KEY_FILE",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL_NAME",
    "HACKLAB_CACHE_MODE",
    "HACKLAB_DB",
    "HACKLAB_DEBUG",
    "HACKLAB_TRACE_DIR",
)


@pytest.fixture(autouse=True)
def isolated_from_the_developers_environment(monkeypatch):
    """Tests never see the real `.env`: no key, no endpoint, no cache, no trace."""
    for name in ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("hacklab.cli.load_dotenv", lambda *args, **kwargs: False)
    # The outage counters live for a process; every test starts without one.
    monkeypatch.setattr("hacklab.providers.JEV_OUTAGE", providers._Outage())
    monkeypatch.setattr("hacklab.providers.EMBEDDING_OUTAGE", providers._Outage())
    # Retries pause in production; a test that wants the pause sets it itself.
    monkeypatch.setattr("hacklab.providers.RETRY_PAUSES", (0.0,))
