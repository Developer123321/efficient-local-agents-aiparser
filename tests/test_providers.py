import json

import httpx
import pytest

from hacklab.config import JevConfig, Limits, ModelConfig
from hacklab.providers import (
    Budget,
    BudgetExceeded,
    JevProvider,
    ModelOutputError,
    ProviderError,
    SmallModelProvider,
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
    assert body["max_tokens"] == 1400
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

    provider, budget = small(receipt, handler)
    with pytest.raises(ProviderError, match="^provider_transport_error$"):
        provider.complete([], {})
    assert budget.calls == 2
    assert all(call["input_tokens"] is None for call in receipt[0].calls(receipt[1]))


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
    provider, _ = small(receipt, lambda request: httpx.Response(200, json=payload))
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
