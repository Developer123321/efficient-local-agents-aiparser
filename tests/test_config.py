from pathlib import Path

import pytest
from pydantic import ValidationError

from hacklab.config import Config, GuardConfig, Limits, load_config, read_secret
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
    with pytest.raises(ValidationError):
        Config.model_validate(
            {
                "model": {
                    "provider": "openai",
                    "input_usd_per_million": 0.1,
                    "output_usd_per_million": 0.1,
                },
                "embedding": {"enabled": True},
                "limits": {"max_cost_usd": 0.01},
            }
        )


def test_env_override_and_secret_file(tmp_path, monkeypatch):
    path = tmp_path / "model.toml"
    path.write_text('[model]\nprovider="openai"\n')
    monkeypatch.setenv("SMALL_MODEL_NAME", "event-model")
    assert load_config(path).model.model == "event-model"
    # A gateway for Jev inside the container needs no new configuration file.
    assert load_config(path).jev.base_url == "https://api.typesafe.ai/v1/systemone"
    monkeypatch.setenv("TYPESAFE_BASE_URL", "http://gateway:8080/v1/systemone")
    assert load_config(path).jev.base_url == "http://gateway:8080/v1/systemone"
    # The embedding model is named by the environment, like the small model.
    assert load_config(path).embedding.model == ""
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "Qwen/Qwen3-Embedding-0.6B")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://gateway:8080/v1")
    embedding = load_config(path).embedding
    assert (embedding.model, embedding.base_url, embedding.enabled) == (
        "Qwen/Qwen3-Embedding-0.6B",
        "http://gateway:8080/v1",
        False,
    )
    secret = tmp_path / "key"
    secret.write_text("fake-test-key\n")
    monkeypatch.setenv("EXAMPLE_KEY_FILE", str(secret))
    assert read_secret("EXAMPLE_KEY") == "fake-test-key"


def test_task_refuses_reference_labels():
    with pytest.raises(ValidationError):
        Task.model_validate({"id": "x", "question": "Question", "expected": "leak"})


def test_every_shipped_profile_is_valid_and_ships_with_cache_and_allowance_off(monkeypatch):
    profiles = sorted((Path(__file__).parents[1] / "configs").glob("*.toml"))
    names = {path.stem for path in profiles}
    assert names == {
        "baseline",
        "embed-rank",
        "image-read",
        "jev-select",
        "live",
        "mock",
        "openrouter",
        "openrouter-jev",
        "practice",
    }
    for path in profiles:
        config = load_config(path)
        assert config.cache.mode == "off", path.name
        if path.stem.startswith("openrouter"):
            # The rehearsal profile guards a small temporary credit.
            assert config.budget.max_usd is not None
            continue
        # What is submitted must neither replay old replies nor stop on a development limit.
        assert config.budget.model_dump() == {
            "max_calls": None,
            "max_tokens": None,
            "max_usd": None,
        }
    monkeypatch.setenv("HACKLAB_CACHE_MODE", "replay")
    assert load_config(profiles[0]).cache.mode == "replay"


CONFIGS = Path(__file__).parents[1] / "configs"


def test_the_candidate_profile_differs_from_the_measured_one_only_where_the_event_requires():
    live = load_config(CONFIGS / "live.toml")
    measured = load_config(CONFIGS / "openrouter.toml")
    # The numbers in docs/benchmarks.md come from openrouter.toml, the rehearsal
    # profile. The submitted profile follows it except for what the event's
    # rules changed; a difference that is not named here is a mistake.
    event_limits = {"max_steps", "max_calls", "max_context_chars", "max_total_tokens"}
    assert live.limits.model_dump(exclude=event_limits) == measured.limits.model_dump(
        exclude=event_limits
    )
    # More hops, the operator rules in every prompt, and room for both.
    assert all(getattr(live.limits, name) > getattr(measured.limits, name) for name in event_limits)
    assert live.limits.max_total_tokens >= live.limits.max_steps * live.limits.max_context_chars
    event_agent = {"playbooks", "skills", "rules", "on_unverified", "fallback"}
    assert live.agent.model_dump(exclude=event_agent) == measured.agent.model_dump(
        exclude=event_agent
    )
    # A wrong answer and no answer both score 0 at the event.
    assert (live.agent.on_unverified, measured.agent.on_unverified) == ("answer", "abstain")
    # The loop took nearly all of a dev run's spend for 1.13 of 9 points; the
    # bounded one-call fallback replaces an empty submission. Measured on the
    # smoke cases only, not on event workspaces.
    assert (live.agent.fallback, measured.agent.fallback) == ("single", "loop")
    scenario = {"allowed_actions", "protected_targets"}
    assert live.guard.model_dump(exclude=scenario) == measured.guard.model_dump(exclude=scenario)
    assert live.model.response_format == measured.model.response_format
    # The owner asked for Jev after the event, for the two typed decisions code
    # narrows to fixed labels. That use is unmeasured; it stays optional.
    assert live.jev.enabled is True
    # A profile written from scratch starts from the measured limits.
    assert measured.limits.model_dump(exclude={"http_retries"}) == Limits().model_dump(
        exclude={"http_retries"}
    )
    assert live.guard.max_actions == GuardConfig().max_actions
    # The Jev variants differ from their base in the [jev] table only.
    for variant, base in (("jev-select", "live"), ("openrouter-jev", "openrouter")):
        with_jev = load_config(CONFIGS / f"{variant}.toml")
        without = load_config(CONFIGS / f"{base}.toml")
        assert with_jev.jev.enabled
        assert with_jev.model_dump(exclude={"jev"}) == without.model_dump(exclude={"jev"})
    assert load_config(CONFIGS / "openrouter.toml").jev.enabled is False
    assert load_config(CONFIGS / "jev-select.toml").jev.select is True
    # The same holds for the embedding variant and its [embedding] table.
    ranked = load_config(CONFIGS / "embed-rank.toml")
    assert ranked.embedding.enabled and not live.embedding.enabled
    assert ranked.model_dump(exclude={"embedding"}) == live.model_dump(exclude={"embedding"})
    # Reading pictures is one more tool in the variant; the submitted profile has
    # none until a run shows what it gains and costs.
    pictured = load_config(CONFIGS / "image-read.toml")
    assert "image" not in live.agent.tools and "image" not in Config().agent.tools
    assert pictured.agent.tools == [*live.agent.tools, "image"]
    live_agent, pictured_agent = live.model_copy(deep=True), pictured.model_copy(deep=True)
    live_agent.agent.tools = pictured_agent.agent.tools = []
    assert pictured_agent.model_dump() == live_agent.model_dump()


def test_the_candidate_profile_finds_its_solvers_and_recipes_wherever_it_runs(
    tmp_path, monkeypatch
):
    from hacklab import playbooks
    from hacklab.agent import _rules, skill_files

    live = load_config(CONFIGS / "live.toml")
    # Both are named by package, not by path: the image's working directory
    # and the team's laptops need not agree.
    monkeypatch.chdir(tmp_path)
    assert isinstance(playbooks.load(live.agent.playbooks), playbooks.Playbooks)
    assert "DENIED_SECURITY" in _rules(live.agent.rules)
    rehearsal = load_config(CONFIGS / "openrouter.toml")
    assert len(skill_files(rehearsal.agent.skills)) >= 2


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


def test_the_fallback_can_be_switched_for_one_run(monkeypatch):
    assert load_config(CONFIGS / "live.toml").agent.fallback == "single"
    monkeypatch.setenv("HACKLAB_FALLBACK", "loop")
    assert load_config(CONFIGS / "live.toml").agent.fallback == "loop"
    monkeypatch.setenv("HACKLAB_FALLBACK", "none")
    assert load_config(CONFIGS / "live.toml").agent.fallback == "none"
