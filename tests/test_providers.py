import json
import stat

import httpx
import pytest

from hacklab.cache import ResponseCache
from hacklab.config import Config, DevBudget, EmbeddingConfig, JevConfig, Limits, ModelConfig
from hacklab.providers import (
    Budget,
    BudgetExceeded,
    DevSpend,
    EmbeddingProvider,
    FunctionModel,
    JevProvider,
    ModelOutputError,
    ProviderError,
    SmallModelProvider,
    chat_endpoint,
    embedding_usable,
    embeddings_endpoint,
    jev_usable,
    make_embedder,
    make_providers,
)
from hacklab.tracking import Tracker


@pytest.fixture
def receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("SMALL_MODEL_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("TYPESAFE_API_KEY", "synthetic-test-key")
    tracker = Tracker(tmp_path / "state" / "runs.sqlite3")
    run_id = tracker.start_run("test-task", {}, mode="openai")
    return tracker, run_id


def completion(value=None, usage=True, finish="stop"):
    payload = {
        "choices": [
            {
                "message": {"content": json.dumps(value or {"action": "final"})},
                "finish_reason": finish,
            }
        ]
    }
    if usage:
        payload["usage"] = {"prompt_tokens": 20, "completion_tokens": 10}
    return payload


def small(receipt, handler, limits=None, **config):
    tracker, run_id = receipt
    limits = limits or Limits()
    budget = Budget(limits)
    provider = SmallModelProvider(
        ModelConfig(
            provider="openai",
            base_url="http://127.0.0.1:1234/v1",
            model="event-small-model",
            **config,
        ),
        limits,
        budget,
        tracker,
        run_id,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return provider, budget


def test_http_retry_has_separate_receipts_and_unknown_failed_cost(receipt):
    seen = []

    def handler(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(503, json={"error": "synthetic private provider body"})
        return httpx.Response(200, json=completion())

    provider, budget = small(receipt, handler, input_usd_per_million=1, output_usd_per_million=2)
    assert provider.complete([{"role": "user", "content": "JSON"}], {}) == {"action": "final"}
    assert budget.calls == 2
    calls = receipt[0].calls(receipt[1])
    assert len(calls) == 2
    assert calls[0]["status"] == "http_503"
    assert calls[0]["input_tokens"] is None
    assert receipt[0].summary(receipt[1])["cost_usd"] is None
    assert receipt[0].summary(receipt[1])["known_cost_usd"] == pytest.approx(0.00004)
    body = json.loads(seen[-1].content)
    assert seen[-1].url.path == "/v1/chat/completions"
    assert body["max_tokens"] == 3000
    assert body["response_format"] == {"type": "json_object"}


def test_auth_failure_is_not_retried_and_error_has_no_body(receipt):
    provider, budget = small(
        receipt,
        lambda request: httpx.Response(401, json={"error": "do-not-log-this"}),
    )
    with pytest.raises(ProviderError, match="^provider_http_401$"):
        provider.complete([], {})
    assert budget.calls == 1
    assert receipt[0].calls(receipt[1])[0]["status"] == "http_401"


def test_request_budget_counts_failed_retry_attempts(receipt):
    provider, budget = small(
        receipt,
        lambda request: httpx.Response(429, json={"error": "rate limited"}),
        Limits(max_calls=1, max_retries=3),
    )
    with pytest.raises(BudgetExceeded, match="request_budget_exhausted"):
        provider.complete([], {})
    assert budget.calls == 1
    assert len(receipt[0].calls(receipt[1])) == 1


def test_missing_usage_remains_unknown_even_for_successful_response(receipt):
    provider, budget = small(
        receipt, lambda request: httpx.Response(200, json=completion(usage=False))
    )
    assert provider.complete([], {}) == {"action": "final"}
    assert budget.tokens > 0
    call = receipt[0].calls(receipt[1])[0]
    assert call["input_tokens"] is None
    assert call["output_tokens"] is None
    assert receipt[0].summary(receipt[1])["total_tokens"] is None


def test_call_tracks_reported_model_when_requested_alias_resolves(receipt):
    payload = completion()
    payload["model"] = "event-small-model-2026-11-14"
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=payload)

    provider, _ = small(receipt, handler)
    provider.complete([], {})
    assert seen[0]["model"] == "event-small-model"
    assert receipt[0].calls(receipt[1])[0]["model"] == "event-small-model-2026-11-14"


@pytest.mark.parametrize("reported", [None, 7, "", "   "])
def test_call_falls_back_to_requested_model_when_response_omits_valid_id(receipt, reported):
    payload = completion()
    payload["model"] = reported
    provider, _ = small(receipt, lambda request: httpx.Response(200, json=payload))
    provider.complete([], {})
    assert receipt[0].calls(receipt[1])[0]["model"] == "event-small-model"


def test_bad_usage_is_not_treated_as_zero(receipt):
    payload = completion()
    payload["usage"] = {"prompt_tokens": True, "completion_tokens": -2}
    provider, _ = small(receipt, lambda request: httpx.Response(200, json=payload))
    provider.complete([], {})
    call = receipt[0].calls(receipt[1])[0]
    assert call["input_tokens"] is None
    assert call["output_tokens"] is None


def test_malformed_model_output_records_returned_usage_without_http_retry(receipt):
    payload = completion()
    payload["choices"][0]["message"]["content"] = "this is not JSON"
    provider, budget = small(receipt, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ModelOutputError):
        provider.complete([], {})
    assert budget.calls == 1
    call = receipt[0].calls(receipt[1])[0]
    assert call["status"] == "invalid_response"
    assert call["input_tokens"] == 20


def test_truncated_model_output_is_rejected_and_usage_kept(receipt):
    provider, _ = small(
        receipt, lambda request: httpx.Response(200, json=completion(finish="length"))
    )
    with pytest.raises(ModelOutputError, match="model_output_truncated"):
        provider.complete([], {})
    assert receipt[0].calls(receipt[1])[0]["output_tokens"] == 10


def test_transport_retries_are_counted_with_unknown_usage(receipt):
    def handler(request):
        raise httpx.ReadTimeout("synthetic timeout with private endpoint", request=request)

    # A slow endpoint gets one more try, however many retries a lost connection may have.
    provider, budget = small(receipt, handler, Limits(http_retries=4))
    with pytest.raises(ProviderError, match="^provider_timeout$"):
        provider.complete([], {})
    assert budget.calls == 2
    calls = receipt[0].calls(receipt[1])
    assert all(call["input_tokens"] is None and call["status"] == "timeout" for call in calls)


def test_response_decoding_error_is_sanitized_without_retry(receipt):
    def handler(request):
        raise httpx.DecodingError("private decoding details", request=request)

    provider, budget = small(receipt, handler)
    with pytest.raises(ProviderError, match="^provider_content_decoding_error$"):
        provider.complete([], {})
    assert budget.calls == 1
    assert receipt[0].calls(receipt[1])[0]["status"] == "invalid_response"


def test_invalid_endpoint_is_sanitized_with_attempt_receipt(receipt):
    provider, budget = small(receipt, lambda request: pytest.fail("must not send"))
    provider.endpoint = "http://localhost:invalid"
    with pytest.raises(ProviderError, match="^provider_endpoint_invalid$"):
        provider.complete([], {})
    assert budget.calls == 1
    assert receipt[0].calls(receipt[1])[0]["status"] == "invalid_request"


def test_cost_budget_stops_before_any_network_request(receipt):
    requested = []
    provider, budget = small(
        receipt,
        lambda request: requested.append(request),
        Limits(max_cost_usd=0.00000001),
        input_usd_per_million=1,
        output_usd_per_million=2,
    )
    with pytest.raises(BudgetExceeded, match="cost_budget_exhausted_before_request"):
        provider.complete([], {})
    assert requested == []
    assert budget.calls == 0
    assert receipt[0].calls(receipt[1]) == []


def test_token_budget_reserves_output_before_network_request(receipt):
    provider, budget = small(
        receipt,
        lambda request: pytest.fail("must not send"),
        Limits(max_total_tokens=128, max_output_tokens=128),
    )
    with pytest.raises(BudgetExceeded, match="token_budget_exhausted_before_request"):
        provider.complete([], {})
    assert budget.calls == 0


def test_reported_tokens_over_budget_stop_subsequent_work(receipt):
    payload = completion()
    payload["usage"]["completion_tokens"] = 30000
    provider, _ = small(
        receipt,
        lambda request: httpx.Response(200, json=payload),
        Limits(max_total_tokens=16000),
    )
    with pytest.raises(BudgetExceeded, match="reported_token_usage_exceeded_budget"):
        provider.complete([], {})
    assert receipt[0].summary(receipt[1])["total_tokens"] == 30020


def test_clock_budget_blocks_request_without_network():
    now = [0.0]
    budget = Budget(Limits(task_timeout_seconds=2), clock=lambda: now[0])
    now[0] = 3.0
    with pytest.raises(BudgetExceeded, match="task_time_budget_exhausted"):
        budget.reserve({}, 1, None, None)
    assert budget.calls == 0


def test_native_jev_contract_batches_noul_questions(receipt):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {
                    "grounded": {"type": "noul", "noul": 0.9},
                    "constraints": {"type": "noul", "noul": 0.85},
                },
                "usage": {"input_tokens": 52, "output_tokens": 6},
            },
        )

    limits = Limits()
    budget = Budget(limits)
    provider = JevProvider(
        JevConfig(),
        limits,
        budget,
        *receipt,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    questions = {
        "grounded": {"type": "noul", "instructions": "Claim follows from quotes."},
        "constraints": {"type": "noul", "instructions": "Explicit constraints hold."},
    }
    assert provider.judge({"answer": "synthetic"}, questions) == {
        "grounded": 0.9,
        "constraints": 0.85,
    }
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/systemone"
    assert set(body) == {"model", "state", "questions"}
    assert body["model"] == "jev-1.14.0"
    assert body["questions"] == questions
    assert receipt[0].summary(receipt[1])["total_tokens"] == 58


@pytest.mark.parametrize(
    "base_url",
    ["http://gateway:8080", "http://gateway:8080/v1/", "http://gateway:8080/v1/systemone"],
)
def test_a_jev_gateway_is_accepted_as_host_prefix_or_full_path(receipt, base_url):
    limits = Limits()
    jev = JevProvider(JevConfig(base_url=base_url), limits, Budget(limits), *receipt)
    try:
        assert jev.endpoint == "http://gateway:8080/v1/systemone"
    finally:
        jev.close()


def test_repeated_jev_failures_open_an_outage_and_a_success_closes_it(receipt):
    reachable = False

    def handler(request):
        if not reachable:
            raise httpx.ConnectError("no route", request=request)
        answers = {"q": {"type": "noul", "noul": 0.9}}
        return httpx.Response(200, json={"model": "jev-1.14.0", "answers": answers})

    limits = Limits(max_retries=0, max_calls=20)
    jev = JevProvider(
        JevConfig(enabled=True),
        limits,
        Budget(limits),
        *receipt,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    question = {"q": {"type": "noul", "instructions": "`x` is true."}}
    assert jev_usable(JevConfig(enabled=True)) and not jev_usable(JevConfig())
    for _ in range(2):
        with pytest.raises(ProviderError, match="provider_transport_error"):
            jev.judge({"x": 1}, question)
    assert jev_usable(JevConfig(enabled=True))
    reachable = True
    assert jev.judge({"x": 1}, question) == {"q": 0.9}
    reachable = False
    for _ in range(3):
        with pytest.raises(ProviderError):
            jev.judge({"x": 1}, question)
    assert not jev_usable(JevConfig(enabled=True))
    # Every failed attempt still has its receipt.
    statuses = [call["status"] for call in receipt[0].calls(receipt[1])]
    assert statuses.count("transport_error") == 5 and statuses.count("ok") == 1


def test_a_replay_miss_is_not_counted_as_a_jev_outage(receipt, tmp_path):
    limits = Limits()
    jev = JevProvider(
        JevConfig(enabled=True),
        limits,
        Budget(limits),
        *receipt,
        cache=ResponseCache(tmp_path / "cache.sqlite", "replay"),
    )
    try:
        for _ in range(4):
            with pytest.raises(ProviderError, match="cache_miss_in_replay_mode"):
                jev.judge({"x": 1}, {"q": {"type": "noul", "instructions": "`x` is true."}})
    finally:
        jev.close()
    assert jev_usable(JevConfig(enabled=True))


@pytest.mark.parametrize("payload", [[], ["not-an-envelope"], 5, "text"])
def test_non_object_api_envelope_has_bounded_error(receipt, payload):
    provider, budget = small(receipt, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError, match="provider_response_must_be_object"):
        provider.complete([], {})
    assert budget.calls == 1
    assert receipt[0].calls(receipt[1])[0]["status"] == "invalid_response"


def test_non_object_choice_is_repairable_model_error(receipt):
    provider, _ = small(receipt, lambda request: httpx.Response(200, json={"choices": [None]}))
    with pytest.raises(ModelOutputError, match="model_choice_must_be_object"):
        provider.complete([], {})


def test_jev_non_object_envelope_has_bounded_error(receipt):
    limits = Limits()
    provider = JevProvider(
        JevConfig(),
        limits,
        Budget(limits),
        *receipt,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        ),
    )
    with pytest.raises(ProviderError, match="provider_response_must_be_object"):
        provider.judge({}, {"claim": {"type": "noul", "instructions": "Check."}})
    assert receipt[0].calls(receipt[1])[0]["status"] == "invalid_response"


def test_retry_after_beyond_task_deadline_does_not_retry_early(receipt, monkeypatch):
    provider, budget = small(
        receipt,
        lambda request: httpx.Response(429, json={}, headers={"Retry-After": "30"}),
        Limits(task_timeout_seconds=10),
    )
    monkeypatch.setattr("hacklab.providers.time.sleep", lambda _: pytest.fail("must not wait"))
    with pytest.raises(BudgetExceeded, match="task_time_budget_exhausted_before_retry"):
        provider.complete([], {})
    assert budget.calls == 1


def test_retry_after_is_honored_without_capping_to_two_seconds(receipt, monkeypatch):
    clock = [0.0]
    sleeps = []
    count = [0]

    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    def handler(request):
        count[0] += 1
        if count[0] == 1:
            return httpx.Response(429, json={}, headers={"Retry-After": "4"})
        return httpx.Response(200, json=completion())

    monkeypatch.setattr("hacklab.providers.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("hacklab.providers.time.sleep", sleep)
    provider, _ = small(receipt, handler)
    provider.complete([], {})
    assert sum(sleeps) == 4
    assert max(sleeps) <= 1
    assert count[0] == 2


@pytest.mark.parametrize("value", [True, -0.1, 1.1, "0.9", float("nan")])
def test_invalid_jev_probability_is_rejected(receipt, value):
    # json.dumps allows NaN specifically to exercise defensive provider parsing.
    payload = json.dumps({"answers": {"claim": {"type": "noul", "noul": value}}, "usage": {}})
    limits = Limits()
    provider = JevProvider(
        JevConfig(),
        limits,
        Budget(limits),
        *receipt,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=payload))
        ),
    )
    with pytest.raises(ProviderError, match="jev_probability_invalid"):
        provider.judge({}, {"claim": {"type": "noul", "instructions": "Check."}})
    assert len(receipt[0].calls(receipt[1])) == 1


@pytest.mark.parametrize(
    "content",
    [
        '{"action":"final"}',
        '```json\n{"action":"final"}\n```',
        '```\n{"action":"final"}\n```',
        'Here is my decision:\n{"action":"final"}\nI hope this helps.',
        '<think>The user wants {"x": 1} maybe.</think>\n{"action":"final"}',
        'Sure. [1, 2] then {"action":"final"} and {"action":"read"}',
        # Gemma 4: an empty thought channel with thinking off, a filled one with it on.
        '<|channel>thought\n<channel|>{"action":"final"}',
        '<|channel>thought\nMaybe {"action":"read"} first?<channel|>{"action":"final"}',
    ],
)
def test_decision_is_recovered_from_wrapped_model_output(content):
    from hacklab.providers import decision_object

    assert decision_object(content) == {"action": "final"}


@pytest.mark.parametrize("content", ["", "no json here", "[1, 2, 3]", '"text"', "{broken"])
def test_output_without_a_json_object_is_a_repairable_model_error(content):
    from hacklab.providers import decision_object

    with pytest.raises(ModelOutputError):
        decision_object(content)


def test_endpoint_specific_token_parameter_and_omitted_temperature(receipt):
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    provider, _ = small(
        receipt, handler, max_tokens_parameter="max_completion_tokens", temperature="omit"
    )
    provider.complete([{"role": "user", "content": "x"}], {})
    assert bodies[0]["max_completion_tokens"] == Limits().max_output_tokens
    assert "max_tokens" not in bodies[0] and "temperature" not in bodies[0]
    default, _ = small(receipt, handler)
    default.complete([{"role": "user", "content": "x"}], {})
    assert bodies[1]["max_tokens"] == Limits().max_output_tokens and bodies[1]["temperature"] == 0


def test_gateway_specific_auth_and_request_fields_come_from_configuration(receipt):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=completion())

    provider, _ = small(
        receipt,
        handler,
        auth_header="api-key",
        auth_scheme="",
        extra_body={
            "chat_template_kwargs": {"enable_thinking": False},
            "seed": 7,
            "model": "must-not-override",
        },
    )
    provider.complete([{"role": "user", "content": "JSON"}], {})
    body = json.loads(seen[0].content)
    assert seen[0].headers["api-key"] == "synthetic-test-key"
    assert "authorization" not in seen[0].headers
    assert body["chat_template_kwargs"] == {"enable_thinking": False} and body["seed"] == 7
    assert body["model"] == "event-small-model"


def test_reply_spent_on_hidden_reasoning_is_named_and_token_details_are_kept(receipt):
    payload = {
        "choices": [
            {"message": {"content": "", "reasoning_content": "thinking…"}, "finish_reason": "stop"}
        ],
        "usage": {
            "prompt_tokens": 900,
            "completion_tokens": 400,
            "prompt_tokens_details": {"cached_tokens": 800},
            "completion_tokens_details": {"reasoning_tokens": 390},
        },
    }
    provider, _ = small(receipt, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ModelOutputError, match="^model_returned_reasoning_only$"):
        provider.complete([], {})
    call = receipt[0].calls(receipt[1])[0]
    assert call["metadata"]["cached_tokens"] == 800
    assert call["metadata"]["reasoning_tokens"] == 390
    parts = completion()
    parts["choices"][0]["message"]["content"] = [{"type": "text", "text": '{"action":"final"}'}]
    provider, _ = small(receipt, lambda request: httpx.Response(200, json=parts))
    assert provider.complete([], {}) == {"action": "final"}


def cached(receipt, tmp_path, handler, mode="readwrite", salt="", allowance=100, **config):
    tracker, run_id = receipt
    limits = Limits()
    return SmallModelProvider(
        ModelConfig(
            provider="openai",
            base_url="http://127.0.0.1:1234/v1",
            model="event-small-model",
            input_usd_per_million=1,
            output_usd_per_million=2,
            **config,
        ),
        limits,
        Budget(limits),
        tracker,
        run_id,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        cache=ResponseCache(tmp_path / "private" / "cache.sqlite", mode),
        cache_salt=salt,
        spend=DevSpend(DevBudget(max_calls=allowance), tracker),
    )


def test_cache_replays_identical_requests_without_spending(receipt, tmp_path):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=completion({"action": "final", "n": len(requests)}))

    ask = [{"role": "user", "content": "same question"}]
    first = cached(receipt, tmp_path, handler).complete(ask, {})
    again = cached(receipt, tmp_path, handler).complete(ask, {})
    assert first == again == {"action": "final", "n": 1} and len(requests) == 1
    live, replay = receipt[0].calls(receipt[1])
    assert live["status"] == "ok" and replay["status"] == "cache_hit"
    assert replay["metadata"]["cache_hit"] is True and replay["input_tokens"] == 20
    summary = receipt[0].summary(receipt[1])
    # The replay keeps its measured usage, so per-task cost stays what a live run costs.
    assert summary["calls"] == 2 and summary["cache_hits"] == 1
    assert summary["cost_usd"] == pytest.approx(2 * 0.00004)
    # Only the live request counts against the team's allowance.
    assert receipt[0].spend() == {
        "calls": 1,
        "tokens": 30,
        "usd": pytest.approx(0.00004),
        "reported_cost_calls": 0,
        "unknown_cost_calls": 0,
    }
    # A different prompt, model setting or repetition is a different request.
    cached(receipt, tmp_path, handler).complete([{"role": "user", "content": "other"}], {})
    cached(receipt, tmp_path, handler, salt="1").complete(ask, {})
    cached(receipt, tmp_path, handler, temperature=0.5).complete(ask, {})
    assert len(requests) == 4
    mode = stat.S_IMODE((tmp_path / "private" / "cache.sqlite").stat().st_mode)
    assert mode == 0o600


def test_replay_mode_never_sends_and_a_reply_needing_repair_replays_as_such(receipt, tmp_path):
    def invalid(request):
        payload = completion()
        payload["choices"][0]["message"]["content"] = "not json at all"
        return httpx.Response(200, json=payload)

    ask = [{"role": "user", "content": "q"}]
    with pytest.raises(ModelOutputError):
        cached(receipt, tmp_path, invalid).complete(ask, {})

    def offline(request):
        raise AssertionError("replay mode must not reach the network")

    with pytest.raises(ModelOutputError, match="model_content_must_be_json_text"):
        cached(receipt, tmp_path, offline, mode="replay").complete(ask, {})
    with pytest.raises(ProviderError, match="^cache_miss_in_replay_mode$"):
        cached(receipt, tmp_path, offline, mode="replay").complete([], {})
    cache = ResponseCache(tmp_path / "private" / "cache.sqlite", "readwrite")
    assert cache.stats()["entries"] == 1 and cache.clear() == 1 and cache.stats()["entries"] == 0
    with pytest.raises(ValueError):
        ResponseCache(tmp_path / "private" / "cache.sqlite", "off")


def test_failed_replies_are_not_cached(receipt, tmp_path):
    seen = []

    def handler(request):
        seen.append(1)
        return httpx.Response(401, json={"error": "denied"})

    for _ in range(2):
        with pytest.raises(ProviderError, match="provider_http_401"):
            cached(receipt, tmp_path, handler).complete([], {})
    assert len(seen) == 2


def test_development_allowance_stops_live_requests_across_runs(receipt, tmp_path):
    tracker, run_id = receipt
    seen = []

    def handler(request):
        seen.append(1)
        return httpx.Response(200, json=completion())

    def ask(text):
        provider = cached(receipt, tmp_path, handler, allowance=3)
        return provider.complete([{"role": "user", "content": text}], {})

    for index in range(3):
        ask(str(index))
    with pytest.raises(BudgetExceeded, match="^development_budget_exhausted$"):
        ask("new")
    assert len(seen) == 3
    # What was already paid for still replays.
    assert ask("0") == {"action": "final"}
    spend = DevSpend(DevBudget(max_tokens=10_000, max_usd=0.00015), tracker)
    spend.check()
    spend.add(20, 10, 0.00004)
    with pytest.raises(BudgetExceeded):
        spend.check()


def test_make_providers_builds_cache_and_allowance_from_configuration(receipt, tmp_path):
    tracker, run_id = receipt
    config = Config.model_validate(
        {
            "model": {"provider": "openai", "base_url": "http://localhost/v1", "model": "m"},
            "jev": {"enabled": True},
            "cache": {"mode": "replay", "path": str(tmp_path / "c" / "cache.sqlite")},
            "budget": {"max_calls": 5},
        }
    )
    small_model, jev = make_providers(config, Budget(config.limits), tracker, run_id, "3")
    try:
        assert small_model.cache is jev.cache and small_model.cache.mode == "replay"
        assert small_model.cache_salt == "3" and small_model.spend.budget.max_calls == 5
    finally:
        small_model.close()
        jev.close()
    plain = Config.model_validate(
        {"model": {"provider": "openai", "base_url": "http://localhost/v1", "model": "m"}}
    )
    only, none = make_providers(plain, Budget(plain.limits), tracker, run_id)
    only.close()
    assert only.cache is None and only.spend is None and none is None


def test_jev_choice_returns_option_and_confidence(receipt):
    tracker, run_id = receipt
    limits = Limits()
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        answer = {
            "type": "choice",
            "choice": "decode",
            "confidence": 0.7,
            "probabilities": {"decode": 0.8, "timeline": 0.2},
        }
        return httpx.Response(
            200,
            json={
                "model": "jev-1.14.0",
                "answers": {"choice": answer},
                "usage": {"input_tokens": 40, "output_tokens": 2},
            },
        )

    jev = JevProvider(
        JevConfig(enabled=True),
        limits,
        Budget(limits),
        tracker,
        run_id,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    criteria = {"decode": "Asks what a payload contains", "timeline": "Asks when or in what order"}
    assert jev.choose(
        {"task": "Decode channel ops-5"}, "Which kind of task is `task`?", criteria
    ) == (
        "decode",
        0.7,
    )
    assert seen[0]["questions"]["choice"] == {
        "type": "choice",
        "instructions": "Which kind of task is `task`?",
        "criteria": criteria,
    }
    with pytest.raises(ProviderError, match="jev_choice_requires"):
        jev.choose({}, "Which?", {"only": "one"})

    def wrong(request):
        answer = {"type": "choice", "choice": "invented", "confidence": 0.9, "probabilities": {}}
        return httpx.Response(
            200,
            json={
                "model": "j",
                "answers": {"choice": answer},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    jev.client = httpx.Client(transport=httpx.MockTransport(wrong))
    with pytest.raises(ProviderError, match="^jev_choice_invalid$"):
        jev.choose({}, "Which?", criteria)


def test_retries_wait_longer_each_time_and_count_against_the_request_budget(receipt, monkeypatch):
    clock = [0.0]
    sleeps = []
    count = [0]

    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    def handler(request):
        count[0] += 1
        if count[0] <= 3:
            return httpx.Response(429 if count[0] == 1 else 503, json={})
        return httpx.Response(200, json=completion())

    monkeypatch.setattr("hacklab.providers.RETRY_PAUSES", (0.5, 1.0, 2.0, 4.0))
    monkeypatch.setattr("hacklab.providers.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("hacklab.providers.time.sleep", sleep)
    # A repair turn costs tokens and stays at one; waiting out a rate limit is nearly free.
    provider, budget = small(receipt, handler, Limits(max_retries=1, http_retries=3))
    assert provider.complete([], {}) == {"action": "final"}
    assert count[0] == 4 and budget.calls == 4
    # 0.5 s, 1 s and 2 s, slept in steps of at most one second.
    assert sum(sleeps) == 3.5 and max(sleeps) <= 1
    statuses = [call["status"] for call in receipt[0].calls(receipt[1])]
    assert statuses == ["http_429", "http_503", "http_503", "ok"]
    # Without the setting, a request is retried as often as a reply is repaired.
    count[0] = 0
    provider, budget = small(receipt, handler, Limits(max_retries=1))
    with pytest.raises(ProviderError, match="^provider_http_503$"):
        provider.complete([], {})
    assert budget.calls == 2


def test_the_end_of_the_run_bounds_every_request(receipt):
    now = [100.0]
    limits = Limits(task_timeout_seconds=300)
    budget = Budget(limits, clock=lambda: now[0], deadline=130.0)
    # The task has five minutes, the run thirty seconds: the nearer end counts.
    assert budget.remaining_seconds() == 30
    budget.reserve({}, 1, None, None)
    now[0] = 130.0
    with pytest.raises(BudgetExceeded, match="^run_deadline_reached$"):
        budget.reserve({}, 1, None, None)
    assert budget.calls == 1
    assert Budget(limits, clock=lambda: now[0]).remaining_seconds() == 300


@pytest.mark.parametrize(
    ("base_url", "endpoint"),
    [
        ("https://host/v1", "https://host/v1/chat/completions"),
        ("https://host/v1/", "https://host/v1/chat/completions"),
        ("http://127.0.0.1:8080", "http://127.0.0.1:8080/chat/completions"),
        # Pasted complete, as it stands in most documentation.
        ("https://host/v1/chat/completions", "https://host/v1/chat/completions"),
        ("https://host/api/chat", "https://host/api/chat"),
        # A gateway that carries its API version in the query string.
        (
            "https://host/openai/deployments/small/chat/completions?api-version=2024-06-01",
            "https://host/openai/deployments/small/chat/completions?api-version=2024-06-01",
        ),
        ("https://host/route?key=value", "https://host/route?key=value"),
    ],
)
def test_the_endpoint_is_the_prefix_plus_the_chat_route_unless_already_complete(
    receipt, base_url, endpoint
):
    assert chat_endpoint(base_url) == endpoint
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=completion())

    tracker, run_id = receipt
    limits = Limits()
    provider = SmallModelProvider(
        ModelConfig(provider="openai", base_url=base_url, model="m"),
        limits,
        Budget(limits),
        tracker,
        run_id,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    provider.complete([], {})
    assert seen == [endpoint]


def kit(receipt, call, limits=None, **options):
    tracker, run_id = receipt
    limits = limits or Limits()
    budget = Budget(limits)
    config = ModelConfig(
        provider="openai", model="kit-model", input_usd_per_million=1, output_usd_per_million=2
    )
    return FunctionModel(call, config, limits, budget, tracker, run_id, **options), budget


def test_a_kit_function_is_metered_cached_and_bounded_like_the_http_adapter(receipt, tmp_path):
    asked = []

    def chat(messages):
        asked.append(messages)
        return completion({"action": "final", "n": len(asked)})

    ask = [{"role": "user", "content": "same question"}]
    cache = {"cache": ResponseCache(tmp_path / "private" / "cache.sqlite", "readwrite")}
    spend = DevSpend(DevBudget(max_calls=2), receipt[0])
    model, budget = kit(receipt, chat, spend=spend, **cache)
    assert model.complete(ask, {}) == {"action": "final", "n": 1}
    again, _ = kit(receipt, chat, spend=spend, **cache)
    assert again.complete(ask, {}) == {"action": "final", "n": 1} and len(asked) == 1
    live, replay = receipt[0].calls(receipt[1])
    assert (live["status"], live["input_tokens"], live["output_tokens"]) == ("ok", 20, 10)
    assert live["model"] == "kit-model" and replay["status"] == "cache_hit"
    # Only the live call counts against the team's allowance.
    assert receipt[0].spend()["calls"] == 1 and budget.calls == 1
    # A reply the harness cannot read is a repairable model error, not a crash.
    for reply in (None, 7, {"choices": []}, "no json here"):
        broken, _ = kit(receipt, lambda messages, reply=reply: reply)
        with pytest.raises(ModelOutputError):
            broken.complete(ask, {})
    truncated, _ = kit(receipt, lambda messages: completion(finish="length"))
    with pytest.raises(ModelOutputError, match="^model_output_truncated$"):
        truncated.complete(ask, {})
    # In replay mode nothing is asked.
    replay_only, _ = kit(
        receipt,
        lambda messages: pytest.fail("must not call"),
        cache=ResponseCache(tmp_path / "private" / "cache.sqlite", "replay"),
    )
    assert replay_only.complete(ask, {}) == {"action": "final", "n": 1}
    with pytest.raises(ProviderError, match="^cache_miss_in_replay_mode$"):
        replay_only.complete([{"role": "user", "content": "new"}], {})


def test_a_kit_function_that_hangs_or_fails_is_given_up_within_bounds(receipt):
    import threading

    release = threading.Event()
    started = []

    def hangs(messages):
        started.append(1)
        release.wait(30)
        return completion()

    # No timeout of its own: the harness stops waiting, tries once more, gives up.
    model, budget = kit(receipt, hangs, Limits(timeout_seconds=0.05, http_retries=4))
    try:
        with pytest.raises(ProviderError, match="^provider_timeout$"):
            model.complete([], {})
    finally:
        release.set()
    assert len(started) == 2 and budget.calls == 2
    assert [call["status"] for call in receipt[0].calls(receipt[1])] == ["timeout", "timeout"]

    attempts = []

    def flaky(messages):
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError("rate limited, with a private detail")
        return "```json\n" + json.dumps({"action": "final"}) + "\n```"

    model, budget = kit(receipt, flaky, Limits(http_retries=3))
    assert model.complete([], {}) == {"action": "final"} and len(attempts) == 3
    down, _ = kit(receipt, lambda messages: 1 / 0, Limits(http_retries=1))
    with pytest.raises(ProviderError, match="^kit_model_call_failed$"):
        down.complete([], {})
    # Usage the kit did not report stays unknown instead of being guessed.
    assert receipt[0].calls(receipt[1])[-1]["input_tokens"] is None


def embedder(receipt, handler, limits=None, model=None, **config):
    limits = limits or Limits()
    settings = {"enabled": True, "model": "Qwen/Qwen3-Embedding-0.6B", **config}
    return EmbeddingProvider(
        EmbeddingConfig(**settings),
        model or ModelConfig(provider="openai", base_url="http://gateway:8000/v1", model="gemma"),
        limits,
        Budget(limits),
        *receipt,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_embedding_contract_instructs_the_query_only_and_bills_input_alone(receipt):
    seen = []

    def handler(request):
        seen.append(request)
        # Out of order on purpose: `index` decides, not the position.
        data = [
            {"index": 2, "embedding": [0.0, 2.0]},
            {"index": 0, "embedding": [3.0, 0.0]},
            {"index": 1, "embedding": [1.0, 0]},
        ]
        usage = {"prompt_tokens": 40, "total_tokens": 40}
        return httpx.Response(200, json={"model": "qwen3-embedding", "data": data, "usage": usage})

    provider = embedder(receipt, handler, dimensions=256, input_usd_per_million=0.01)
    assert provider.similarity("Who was quarantined?", ["Agent a7.", "Lunch."]) == [1.0, 0.0]
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/embeddings"
    assert seen[0].headers["Authorization"] == "Bearer synthetic-test-key"
    assert body["model"] == "Qwen/Qwen3-Embedding-0.6B" and body["dimensions"] == 256
    assert body["input"][0].startswith("Instruct: Given a question about a security incident")
    assert body["input"][0].endswith("\nQuery:Who was quarantined?")
    assert body["input"][1:] == ["Agent a7.", "Lunch."]
    call = receipt[0].calls(receipt[1])[0]
    assert (call["provider"], call["model"]) == ("embedding", "qwen3-embedding")
    assert (call["input_tokens"], call["output_tokens"]) == (40, 0)
    assert call["cost_usd"] == pytest.approx(40 * 0.01 / 1_000_000)
    # Without `dimensions` the field is left out: not every endpoint accepts it.
    plain = embedder(receipt, handler)
    plain.similarity("q", ["a", "b"])
    assert "dimensions" not in json.loads(seen[1].content)
    assert receipt[0].calls(receipt[1])[1]["cost_usd"] is None


@pytest.mark.parametrize(
    "data",
    [
        [{"index": 0, "embedding": [1.0]}],
        [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [1.0]}],
        [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": []}],
        [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": ["x"]}],
        [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1.0, 2.0]}],
        [{"index": 0, "embedding": [1.0]}, {"index": 1}],
    ],
)
def test_a_malformed_embedding_reply_is_a_provider_error(receipt, data):
    provider = embedder(receipt, lambda request: httpx.Response(200, json={"data": data}))
    with pytest.raises(ProviderError):
        provider.embed(["one", "two"])
    with pytest.raises(ProviderError):
        provider.embed([])


@pytest.mark.parametrize(
    ("base_url", "endpoint"),
    [
        ("http://gateway:8000/v1", "http://gateway:8000/v1/embeddings"),
        ("http://gateway:8000/v1/", "http://gateway:8000/v1/embeddings"),
        ("http://gateway:8000/v1/chat/completions", "http://gateway:8000/v1/embeddings"),
        ("http://gateway:8000/v1/embeddings", "http://gateway:8000/v1/embeddings"),
        ("https://host/embed?api-version=1", "https://host/embed?api-version=1"),
    ],
)
def test_the_embedding_endpoint_is_derived_from_a_prefix_or_the_chat_route(base_url, endpoint):
    assert embeddings_endpoint(base_url) == endpoint


def test_the_embedding_model_borrows_address_and_key_and_is_left_out_after_an_outage(
    receipt, monkeypatch
):
    def config(**embedding):
        return Config.model_validate(
            {
                "model": {"provider": "openai", "base_url": "http://gateway/v1", "model": "gemma"},
                "embedding": {"enabled": True, **embedding},
            }
        )

    named = config(model="qwen3-embedding")
    # Off, or without a model name, nothing is asked.
    assert not embedding_usable(Config()) and not embedding_usable(config())
    assert make_embedder(Config(), Budget(Limits()), *receipt) is None
    assert embedding_usable(named)
    own = make_embedder(named, Budget(named.limits), *receipt)
    assert own.endpoint == "http://gateway/v1/embeddings" and own.key == "synthetic-test-key"
    # It waits a quarter of what the generator may take, and never longer than configured.
    assert (named.limits.timeout_seconds, own.limits.timeout_seconds) == (60, 15)
    assert embedder(receipt, None, limits=Limits(timeout_seconds=5)).limits.timeout_seconds == 5
    own.close()
    monkeypatch.setenv("EMBEDDING_API_KEY", "own-synthetic-key")
    separate = make_embedder(
        config(model="qwen3-embedding", base_url="http://embed:9000/v1"),
        Budget(named.limits),
        *receipt,
    )
    assert separate.endpoint == "http://embed:9000/v1/embeddings"
    assert separate.key == "own-synthetic-key"
    separate.close()

    def unreachable(request):
        raise httpx.ConnectError("no route", request=request)

    limits = Limits(max_retries=0, max_calls=20)
    provider = embedder(receipt, unreachable, limits=limits)
    for _ in range(3):
        with pytest.raises(ProviderError, match="provider_transport_error"):
            provider.embed(["one"])
    assert not embedding_usable(named)
    # Jev's counter is its own.
    assert jev_usable(JevConfig(enabled=True))


# -- what a request cost: the provider's own figure beside our estimate ------------


def priced(receipt, handler, limits=None, **options):
    """The small model at $1 and $2 per million tokens: 20 in and 10 out are $0.00004."""
    return small(
        receipt, handler, limits, input_usd_per_million=1, output_usd_per_million=2, **options
    )


def costing(cost, status=200, **headers):
    payload = completion()
    payload["usage"]["cost"] = cost
    # json.dumps writes NaN and Infinity, as a careless gateway might.
    return httpx.Response(status, content=json.dumps(payload), headers=headers)


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        (0.0012, 0.0012),
        (0, 0.0),  # A free reply is a known cost, not a missing one.
        (3, 3.0),
        (None, None),
        ("0.0012", None),
        (-0.01, None),
        (True, None),
        (float("nan"), None),
        (float("inf"), None),
        ({"usd": 1}, None),
    ],
)
def test_a_reported_cost_is_kept_beside_the_estimate_and_never_guessed(receipt, given, kept):
    provider, budget = priced(receipt, lambda request: costing(given))
    provider.complete([], {})
    call = receipt[0].calls(receipt[1])[0]
    # The estimate from tokens and prices is the same whatever the provider reports.
    assert call["cost_usd"] == pytest.approx(0.00004) and call["reported_cost_usd"] == kept
    summary = receipt[0].summary(receipt[1])
    assert summary["cost_usd"] == pytest.approx(0.00004)
    assert summary["reported_cost_usd"] == kept
    assert summary["reported_cost_calls"] == (kept is not None)
    # Spend is the reported figure where there is one, else the estimate.
    spent = 0.00004 if kept is None else kept
    assert summary["spent_usd"] == pytest.approx(spent) and summary["unknown_spend_calls"] == 0
    assert budget.cost_usd == pytest.approx(spent)
    assert receipt[0].spend()["usd"] == pytest.approx(spent)


def test_a_reply_without_usage_or_cost_stays_unknown_not_free(receipt):
    reply = httpx.Response(200, json=completion(usage=False))
    provider, _ = small(receipt, lambda request: reply)
    provider.complete([], {})
    summary = receipt[0].summary(receipt[1])
    assert summary["cost_usd"] is None and summary["reported_cost_usd"] is None
    assert summary["spent_usd"] is None and summary["known_spent_usd"] == 0
    assert summary["unknown_spend_calls"] == 1
    assert receipt[0].spend()["unknown_cost_calls"] == 1


def test_the_task_cost_limit_and_the_allowance_count_what_was_reported(receipt):
    tracker, run_id = receipt
    costs = [0.0006, 0.02]
    limits = Limits(max_cost_usd=0.015)
    provider, budget = priced(receipt, lambda request: costing(costs.pop(0)), limits)
    provider.spend = DevSpend(DevBudget(max_usd=0.0005), tracker)
    provider.complete([], {})
    # Estimated from its tokens the reply cost $0.00004; the provider says $0.0006.
    assert budget.cost_usd == pytest.approx(0.0006) and provider.spend.usd == pytest.approx(0.0006)
    with pytest.raises(BudgetExceeded, match="^development_budget_exhausted$"):
        provider.complete([], {})
    provider.spend = None
    # By the estimate this reply would fit the task's limit many times over.
    with pytest.raises(BudgetExceeded, match="^reported_cost_exceeded_budget$"):
        provider.complete([], {})
    assert [call["reported_cost_usd"] for call in tracker.calls(run_id)] == [0.0006, 0.02]


def test_a_replay_carries_its_cost_and_is_not_spent_again(receipt, tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return costing(0.002)

    ask = [{"role": "user", "content": "same question"}]
    first = cached(receipt, tmp_path, handler)
    first.complete(ask, {})
    again = cached(receipt, tmp_path, handler)
    again.complete(ask, {})
    assert len(requests) == 1
    live, replay = receipt[0].calls(receipt[1])
    # The receipt of the replay says what the reply cost when it was live.
    assert live["reported_cost_usd"] == replay["reported_cost_usd"] == 0.002
    assert replay["metadata"]["cache_hit"] is True and "cache_hit" not in live["metadata"]
    summary = receipt[0].summary(receipt[1])
    # What the run would cost without a cache counts both; what was paid, one.
    assert summary["reported_cost_usd"] == pytest.approx(0.004)
    assert summary["spent_usd"] == pytest.approx(0.002) and summary["cache_hits"] == 1
    assert receipt[0].spend()["usd"] == pytest.approx(0.002)
    assert receipt[0].spend()["reported_cost_calls"] == 1
    assert first.spend.usd == pytest.approx(0.002) and again.spend.calls == 1
    # No body, in either direction, reached the tracking database.
    stored = receipt[0].path.read_bytes()
    assert b"same question" not in stored and b"final" not in stored


def test_optional_models_report_their_cost_the_same_way(receipt, monkeypatch):
    tracker, run_id = receipt
    limits = Limits()
    budget = Budget(limits)
    routed = {
        "answers": {"claim": {"noul": 0.9}},
        "usage": {"prompt_tokens": 400, "completion_tokens": 0, "cost": 0.00002},
    }
    own = {"answers": {"claim": {"noul": 0.9}}, "usage": {"input_tokens": 400, "output_tokens": 0}}
    replies = [routed, own]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=replies.pop(0)))
    )
    jev = JevProvider(JevConfig(enabled=True), limits, budget, tracker, run_id, client=client)
    question = {"claim": {"type": "noul", "instructions": "Check."}}
    jev.judge({"text": "one"}, question)
    jev.judge({"text": "two"}, question)
    vectors = {
        "data": [{"index": 0, "embedding": [1.0, 0.0]}],
        "usage": {"prompt_tokens": 9, "total_tokens": 9, "cost": 0},
    }
    embedder = EmbeddingProvider(
        EmbeddingConfig(enabled=True, model="qwen"),
        ModelConfig(provider="openai", base_url="http://127.0.0.1:1/v1", model="m"),
        limits,
        budget,
        tracker,
        run_id,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=vectors))
        ),
    )
    embedder.embed(["text"])
    calls = tracker.calls(run_id)
    assert [(call["provider"], call["reported_cost_usd"]) for call in calls] == [
        ("jev", 0.00002),
        ("jev", None),  # TypeSafe's own API reports tokens only: unknown, not free.
        ("embedding", 0.0),
    ]
    summary = tracker.summary(run_id)
    assert summary["known_reported_cost_usd"] == pytest.approx(0.00002)
    assert summary["reported_cost_usd"] is None and summary["unknown_spend_calls"] == 1


def test_a_kit_function_reply_reports_its_cost_too(receipt):
    tracker, run_id = receipt
    limits = Limits()
    reply = completion()
    reply["usage"]["cost"] = 0.0007
    model = FunctionModel(
        lambda messages: reply, ModelConfig(model="kit"), limits, Budget(limits), tracker, run_id
    )
    model.complete([], {})
    assert tracker.calls(run_id)[0]["reported_cost_usd"] == 0.0007


# -- which failures pass ------------------------------------------------------------


def clocked(monkeypatch):
    """A clock that only moves when the provider sleeps, and the sleeps it made."""
    clock, sleeps = [0.0], []

    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr("hacklab.providers.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("hacklab.providers.time.sleep", sleep)
    return clock, sleeps


def test_a_request_timeout_and_a_full_in_flight_budget_are_tried_again(receipt, monkeypatch):
    clock, sleeps = clocked(monkeypatch)
    replies = [
        httpx.Response(408, json={}),
        # OpenRouter: credit is left, too much of it is reserved by requests in flight.
        httpx.Response(402, json={"error": {"message": "x"}}, headers={"Retry-After": "7"}),
        costing(0.001),
    ]
    provider, budget = priced(
        receipt, lambda request: replies.pop(0), Limits(http_retries=3, max_calls=4)
    )
    assert provider.complete([], {}) == {"action": "final"}
    # A short pause after the 408, the seven seconds the 402 asked for.
    assert sum(sleeps) == pytest.approx(7.0) and clock[0] == pytest.approx(7.0)
    calls = receipt[0].calls(receipt[1])
    assert [call["status"] for call in calls] == ["http_408", "http_402", "ok"]
    assert [call["metadata"]["attempt"] for call in calls] == [1, 2, 3]
    # Every attempt is counted; what the failed ones cost is not known.
    assert budget.calls == 3
    summary = receipt[0].summary(receipt[1])
    assert summary["unknown_cost_calls"] == 2 and summary["cost_usd"] is None
    assert summary["known_reported_cost_usd"] == pytest.approx(0.001)
    assert summary["spent_usd"] is None and summary["unknown_spend_calls"] == 2


@pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
def test_a_failure_that_does_not_pass_is_not_tried_again(receipt, monkeypatch, status):
    _, sleeps = clocked(monkeypatch)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json={"error": {"message": "Key limit exceeded"}})

    provider, budget = small(receipt, handler, Limits(http_retries=5))
    with pytest.raises(ProviderError, match=f"^provider_http_{status}$"):
        provider.complete([], {})
    assert len(seen) == 1 and budget.calls == 1 and sleeps == []


def test_a_403_is_final_even_when_it_names_a_wait(receipt, monkeypatch):
    _, sleeps = clocked(monkeypatch)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(403, json={}, headers={"Retry-After": "1"})

    provider, _ = small(receipt, handler, Limits(http_retries=5))
    with pytest.raises(ProviderError, match="^provider_http_403$"):
        provider.complete([], {})
    assert len(seen) == 1 and sleeps == []


def test_the_wait_a_server_asks_for_is_bounded_and_retries_run_out(receipt, monkeypatch):
    clock, sleeps = clocked(monkeypatch)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(402, json={}, headers={"Retry-After": "3600"})

    limits = Limits(http_retries=2, task_timeout_seconds=1800)
    provider, budget = small(receipt, handler, limits)
    with pytest.raises(ProviderError, match="^provider_http_402$"):
        provider.complete([], {})
    # Three attempts, and two waits of two minutes instead of an hour each.
    assert len(seen) == 3 and budget.calls == 3 and sum(sleeps) == pytest.approx(240.0)
    assert [call["status"] for call in receipt[0].calls(receipt[1])] == ["http_402"] * 3


def test_a_wait_that_outlasts_the_run_ends_the_request_at_once(receipt, monkeypatch):
    clock, sleeps = clocked(monkeypatch)
    tracker, run_id = receipt
    limits = Limits(http_retries=3)
    budget = Budget(limits, clock=lambda: clock[0], deadline=20.0)
    provider = SmallModelProvider(
        ModelConfig(provider="openai", base_url="http://127.0.0.1:1234/v1", model="m"),
        limits,
        budget,
        tracker,
        run_id,
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(402, json={}, headers={"Retry-After": "30"})
            )
        ),
    )
    with pytest.raises(BudgetExceeded, match="^task_time_budget_exhausted_before_retry$"):
        provider.complete([], {})
    assert sleeps == [] and budget.calls == 1
    # Past the end of the run no further request is started at all.
    clock[0] = 21.0
    with pytest.raises(BudgetExceeded, match="^run_deadline_reached$"):
        provider.complete([], {})
    assert len(tracker.calls(run_id)) == 1


# -- a picture shown to the small model --------------------------------------------


def reading(text="BOARD", finish="stop", **usage):
    message = {"content": text}
    counted = {"prompt_tokens": 300, "completion_tokens": 12, **usage}
    return {"choices": [{"message": message, "finish_reason": finish}], "usage": counted}


def test_a_picture_is_one_tracked_request_with_its_own_allowances(receipt):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=reading(cost=0.0003))

    picture = b"\x89PNG" + bytes(900_000)
    extra = {"usage": {"include": True}, "provider": {"order": ["deepinfra/fp8"]}}
    provider, budget = priced(receipt, handler, Limits(max_total_tokens=20_000), extra_body=extra)
    assert provider.describe(picture, "image/png", "the date") == "BOARD"
    body = json.loads(seen[0].content)
    # The same endpoint, key and routing as a decision; plain text back, 700 tokens at most.
    assert str(seen[0].url) == "http://127.0.0.1:1234/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer synthetic-test-key"
    assert body["usage"] == {"include": True} and body["provider"] == {"order": ["deepinfra/fp8"]}
    assert body["max_tokens"] == 700 and body["temperature"] == 0.0
    assert "response_format" not in body and body["model"] == "event-small-model"
    # The megabyte of base64 is not held against the token budget; a fixed allowance is.
    assert len(seen[0].content) > 1_200_000 and budget.calls == 1
    assert budget.tokens == 312 and budget.cost_usd == pytest.approx(0.0003)
    call = receipt[0].calls(receipt[1])[0]
    assert (call["provider"], call["status"]) == ("openai", "ok")
    assert (call["input_tokens"], call["output_tokens"]) == (300, 12)
    assert call["reported_cost_usd"] == 0.0003
    with pytest.raises(ProviderError, match="^image_type_unsupported$"):
        provider.describe(picture, "image/gif")
    assert len(seen) == 1


def test_what_the_model_says_about_a_picture_is_taken_as_text_or_refused(receipt):
    replies = [
        httpx.Response(429, json={}),
        httpx.Response(200, json=reading("<think>hm</think>  Line one\nLine two ")),
        httpx.Response(200, json=reading("Cut off he", finish="length")),
        httpx.Response(200, json=reading([{"type": "text", "text": "typed"}, {"type": "x"}])),
        httpx.Response(200, json=reading("")),
        httpx.Response(200, json=reading(None)),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(400, json={"error": {"message": "this endpoint takes no images"}}),
    ]
    provider, budget = small(receipt, lambda request: replies.pop(0), Limits(http_retries=1))
    picture = b"\xff\xd8\xff" + bytes(200)
    # A rate limit passes, as for any request; hidden thinking is not part of the reading.
    assert provider.describe(picture, "image/jpeg") == "Line one\nLine two"
    assert provider.describe(picture, "image/jpeg") == "Cut off he …"
    assert provider.describe(picture, "image/jpeg") == "typed"
    for code in ("model_read_nothing_in_the_image",) * 2 + ("provider_response_schema_invalid",):
        with pytest.raises(ProviderError, match=f"^{code}$"):
            provider.describe(picture, "image/jpeg")
    with pytest.raises(ProviderError, match="^provider_http_400$"):
        provider.describe(picture, "image/jpeg")
    statuses = [call["status"] for call in receipt[0].calls(receipt[1])]
    assert statuses == ["http_429", "ok", "ok", "ok"] + ["invalid_response"] * 3 + ["http_400"]
    assert budget.calls == 8


def test_a_picture_is_not_requested_past_the_end_of_the_run(receipt):
    tracker, run_id = receipt
    limits = Limits()
    now = [100.0]
    budget = Budget(limits, clock=lambda: now[0], deadline=130.0)
    provider = SmallModelProvider(
        ModelConfig(provider="openai", base_url="http://127.0.0.1:1234/v1", model="m"),
        limits,
        budget,
        tracker,
        run_id,
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: pytest.fail("must not be sent"))
        ),
    )
    now[0] = 131.0
    with pytest.raises(BudgetExceeded, match="^run_deadline_reached$"):
        provider.describe(b"\x89PNG" + bytes(100), "image/png")
    assert tracker.calls(run_id) == []
