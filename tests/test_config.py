import pytest
from pydantic import ValidationError

from hacklab.config import Config, load_config, read_secret
from hacklab.models import Task


def test_cost_cap_requires_prices_for_each_live_provider():
    with pytest.raises(ValidationError):
        Config.model_validate({"model": {"provider": "openai"}, "limits": {"max_cost_usd": 0.01}})
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "model": {
                    "provider": "openai",
                    "input_usd_per_million": 0.1,
                    "output_usd_per_million": 0.1,
                },
                "jev": {"enabled": True},
                "limits": {"max_cost_usd": 0.01},
            }
        )


def test_env_override_and_secret_file(tmp_path, monkeypatch):
    path = tmp_path / "model.toml"
    path.write_text('[model]\nprovider="openai"\n')
    monkeypatch.setenv("SMALL_MODEL_NAME", "event-model")
    assert load_config(path).model.model == "event-model"
    secret = tmp_path / "key"
    secret.write_text("fake-test-key\n")
    monkeypatch.setenv("EXAMPLE_KEY_FILE", str(secret))
    assert read_secret("EXAMPLE_KEY") == "fake-test-key"


def test_task_refuses_reference_labels():
    with pytest.raises(ValidationError):
        Task.model_validate({"id": "x", "question": "Question", "expected": "leak"})
