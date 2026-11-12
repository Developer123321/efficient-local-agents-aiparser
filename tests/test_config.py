from pathlib import Path

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


def test_every_shipped_profile_is_valid_and_ships_with_cache_and_allowance_off(monkeypatch):
    monkeypatch.delenv("HACKLAB_CACHE_MODE", raising=False)
    profiles = sorted((Path(__file__).parents[1] / "configs").glob("*.toml"))
    assert {path.stem for path in profiles} == {
        "baseline",
        "jev-select",
        "live",
        "mock",
        "practice",
    }
    for path in profiles:
        config = load_config(path)
        # What is submitted must neither replay old replies nor stop on a development limit.
        assert config.cache.mode == "off", path.name
        assert config.budget.model_dump() == {
            "max_calls": None,
            "max_tokens": None,
            "max_usd": None,
        }
    monkeypatch.setenv("HACKLAB_CACHE_MODE", "replay")
    assert load_config(profiles[0]).cache.mode == "replay"


def test_guard_and_agent_settings_are_validated():
    with pytest.raises(ValidationError):
        Config.model_validate({"guard": {"injection": "sometimes"}})
    with pytest.raises(ValidationError):
        Config.model_validate({"agent": {"playbooks": "not a module path"}})
    with pytest.raises(ValidationError):
        Config.model_validate({"model": {"auth_header": "bad header"}})
    with pytest.raises(ValidationError):
        Config.model_validate({"model": {"provider": "openai"}, "budget": {"max_usd": 5}})
    config = Config.model_validate(
        {"agent": {"playbooks": "team.playbooks:registry", "tools": ["read"]}}
    )
    assert config.agent.tools == ["read"] and config.guard.injection == "flag"
