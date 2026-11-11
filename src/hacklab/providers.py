"""Small, explicit HTTP adapters. Every request attempt has a receipt.

No provider-specific SDK retry layer or cache is hidden underneath this module.
Cost limits reserve a conservative request allowance before sending. A remote
invoice cannot be guaranteed when the remote service omits usage or times out.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from hacklab.config import Config, JevConfig, Limits, ModelConfig, read_secret

MAX_RESPONSE_BYTES = 1_000_000


class ProviderError(RuntimeError):
    """A fixed, non-sensitive provider failure suitable for logs and repair prompts."""


class BudgetExceeded(ProviderError):
    pass


class ModelOutputError(ProviderError):
    """A generator response that may benefit from one bounded repair prompt."""


@dataclass(frozen=True)
class Reservation:
    input_tokens: int
    output_tokens: int
    cost_usd: float | None


class Budget:
    """One task's request, elapsed-time, token and optional price allowance.

    Request UTF-8 bytes are used as a conservative preflight token allowance,
    not reported as measured tokens. Unknown usage consumes the reservation
    locally and remains unknown in the tracking database.
    """

    def __init__(self, limits: Limits, *, clock: Callable[[], float] = time.monotonic):
        self.limits = limits
        self.clock = clock
        self.started = clock()
        self.calls = 0
        self.tokens = 0
        self.cost_usd = 0.0

    def remaining_seconds(self) -> float:
        return self.limits.task_timeout_seconds - (self.clock() - self.started)

    def check_time(self) -> None:
        if self.remaining_seconds() <= 0:
            raise BudgetExceeded("task_time_budget_exhausted")

    def reserve(
        self,
        body: dict[str, Any],
        output_tokens: int,
        input_rate: float | None,
        output_rate: float | None,
    ) -> Reservation:
        self.check_time()
        if self.calls >= self.limits.max_calls:
            raise BudgetExceeded("request_budget_exhausted")
        estimated_input = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        reservation = Reservation(
            input_tokens=estimated_input,
            output_tokens=output_tokens,
            cost_usd=(
                (estimated_input * input_rate + output_tokens * output_rate) / 1_000_000
                if input_rate is not None and output_rate is not None
                else None
            ),
        )
        if self.tokens + estimated_input + output_tokens > self.limits.max_total_tokens:
            raise BudgetExceeded("token_budget_exhausted_before_request")
        if self.limits.max_cost_usd is not None:
            if reservation.cost_usd is None:
                raise BudgetExceeded("cost_budget_requires_known_prices")
            if self.cost_usd + reservation.cost_usd > self.limits.max_cost_usd:
                raise BudgetExceeded("cost_budget_exhausted_before_request")
        self.calls += 1
        return reservation

    def settle(
        self,
        reservation: Reservation,
        input_tokens: int | None,
        output_tokens: int | None,
        input_rate: float | None,
        output_rate: float | None,
    ) -> None:
        accounted_input = input_tokens if input_tokens is not None else reservation.input_tokens
        accounted_output = output_tokens if output_tokens is not None else reservation.output_tokens
        self.tokens += accounted_input + accounted_output
        if input_rate is not None and output_rate is not None:
            self.cost_usd += (
                accounted_input * input_rate + accounted_output * output_rate
            ) / 1_000_000

    def check_consumed(self) -> None:
        self.check_time()
        if self.tokens > self.limits.max_total_tokens:
            raise BudgetExceeded("reported_token_usage_exceeded_budget")
        if self.limits.max_cost_usd is not None and self.cost_usd > self.limits.max_cost_usd:
            raise BudgetExceeded("reported_cost_exceeded_budget")


def _token(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _usage(payload: Any, provider: str) -> tuple[int | None, int | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("usage"), dict):
        return None, None
    usage = payload["usage"]
    if provider == "jev":
        return _token(usage.get("input_tokens")), _token(usage.get("output_tokens"))
    return _token(usage.get("prompt_tokens")), _token(usage.get("completion_tokens"))


def _retry_after(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError, OSError):
            return 0.0
    return max(0.0, delay) if math.isfinite(delay) else 0.0


class _HTTPProvider:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        endpoint: str,
        key: str,
        input_rate: float | None,
        output_rate: float | None,
        limits: Limits,
        budget: Budget,
        tracker: Any,
        run_id: str,
        client: httpx.Client | None = None,
    ):
        self.provider = provider
        self.model = model
        self.endpoint = endpoint
        self.key = key
        self.input_rate = input_rate
        self.output_rate = output_rate
        self.limits = limits
        self.budget = budget
        self.tracker = tracker
        self.run_id = run_id
        self.client = client or httpx.Client(follow_redirects=False)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def request(
        self,
        body: dict[str, Any],
        parse: Callable[[Any], Any],
        *,
        output_allowance: int,
    ) -> Any:
        for attempt in range(self.limits.max_retries + 1):
            reservation = self.budget.reserve(
                body, output_allowance, self.input_rate, self.output_rate
            )
            started = time.monotonic()
            payload: Any = None
            input_tokens: int | None = None
            output_tokens: int | None = None
            retry = False
            retry_delay = 0.0
            error: ProviderError | None = None
            result: Any = None
            status = "transport_error"
            try:
                timeout = min(self.limits.timeout_seconds, self.budget.remaining_seconds())
                with self.client.stream(
                    "POST",
                    self.endpoint,
                    json=body,
                    headers={"Authorization": f"Bearer {self.key}"},
                    timeout=max(0.001, timeout),
                ) as response:
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        self.budget.check_time()
                        chunks.extend(chunk)
                        if len(chunks) > MAX_RESPONSE_BYTES:
                            raise ProviderError("provider_response_too_large")
                    try:
                        payload = json.loads(chunks)
                    except (ValueError, UnicodeDecodeError):
                        payload = None
                    input_tokens, output_tokens = _usage(payload, self.provider)
                    if not 200 <= response.status_code < 300:
                        status = f"http_{response.status_code}"
                        error = ProviderError(f"provider_http_{response.status_code}")
                        retry = response.status_code == 429 or response.status_code >= 500
                        retry_delay = _retry_after(response.headers.get("Retry-After"))
                    else:
                        status = "invalid_response"
                        if payload is None:
                            raise ProviderError("provider_returned_invalid_json")
                        if not isinstance(payload, dict):
                            raise ProviderError("provider_response_must_be_object")
                        result = parse(payload)
                        status = "ok"
            except httpx.TransportError:
                error = ProviderError("provider_transport_error")
                retry = True
            except httpx.DecodingError:
                error = ProviderError("provider_content_decoding_error")
                status = "invalid_response"
            except httpx.InvalidURL:
                error = ProviderError("provider_endpoint_invalid")
                status = "invalid_request"
            except BudgetExceeded as exc:
                error = exc
                status = "task_timeout"
            except ProviderError as exc:
                error = exc
                if status == "transport_error":
                    status = "invalid_response"
            except (ValueError, KeyError, TypeError, IndexError):
                error_type = ModelOutputError if self.provider == "openai" else ProviderError
                error = error_type("provider_response_schema_invalid")
                status = "invalid_response"
            finally:
                self.budget.settle(
                    reservation, input_tokens, output_tokens, self.input_rate, self.output_rate
                )
                reported_model = payload.get("model") if isinstance(payload, dict) else None
                model_reported = isinstance(reported_model, str) and bool(reported_model.strip())
                self.tracker.record_call(
                    self.run_id,
                    provider=self.provider,
                    model=reported_model if model_reported else self.model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=(time.monotonic() - started) * 1000,
                    status=status,
                    input_rate=self.input_rate,
                    output_rate=self.output_rate,
                    attempt=attempt + 1,
                    requested_model=self.model,
                    model_reported=model_reported,
                )
            self.budget.check_consumed()
            if error is None:
                return result
            if not retry or attempt >= self.limits.max_retries:
                raise error
            if retry_delay:
                self.budget.check_time()
                if retry_delay >= self.budget.remaining_seconds():
                    raise BudgetExceeded("task_time_budget_exhausted_before_retry")
                wait_until = time.monotonic() + retry_delay
                while (remaining_wait := wait_until - time.monotonic()) > 0:
                    self.budget.check_time()
                    time.sleep(min(1.0, remaining_wait))
        raise ProviderError("request_failed")  # pragma: no cover


class SmallModelProvider(_HTTPProvider):
    """An explicitly selected OpenAI-compatible Chat Completions adapter."""

    def __init__(
        self,
        config: ModelConfig,
        limits: Limits,
        budget: Budget,
        tracker: Any,
        run_id: str,
        *,
        client: httpx.Client | None = None,
    ):
        if not config.base_url or not config.model:
            raise ProviderError("configure_small_model_base_url_and_name")
        key = read_secret(config.api_key_env)
        if not key:
            raise ProviderError("small_model_key_missing")
        endpoint = config.base_url.rstrip("/")
        if not endpoint.endswith("/chat/completions"):
            endpoint += "/chat/completions"
        self.config = config
        super().__init__(
            provider="openai",
            model=config.model,
            endpoint=endpoint,
            key=key,
            input_rate=config.input_usd_per_million,
            output_rate=config.output_usd_per_million,
            limits=limits,
            budget=budget,
            tracker=tracker,
            run_id=run_id,
            client=client,
        )

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.limits.max_output_tokens,
        }
        if self.config.response_format == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif self.config.response_format == "json_schema":
            # The client applies the strict Pydantic contract. Not every event
            # endpoint implements the server-side strict JSON-schema subset.
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "agent_decision", "schema": schema, "strict": False},
            }

        def parse(payload: Any) -> dict[str, Any]:
            choice = payload["choices"][0]
            if not isinstance(choice, dict):
                raise ModelOutputError("model_choice_must_be_object")
            if choice.get("finish_reason") == "length":
                raise ModelOutputError("model_output_truncated")
            content = choice["message"]["content"]
            if not isinstance(content, str):
                raise ModelOutputError("model_content_must_be_json_text")
            content = content.strip()
            if content.startswith("```json\n") and content.endswith("\n```"):
                content = content[8:-4]
            decision = json.loads(content)
            if not isinstance(decision, dict):
                raise ModelOutputError("model_decision_must_be_object")
            return decision

        return self.request(body, parse, output_allowance=self.limits.max_output_tokens)


class JevProvider(_HTTPProvider):
    """Native TypeSafe System One Noul adapter; Jev never generates text."""

    def __init__(
        self,
        config: JevConfig,
        limits: Limits,
        budget: Budget,
        tracker: Any,
        run_id: str,
        *,
        client: httpx.Client | None = None,
    ):
        key = read_secret(config.api_key_env)
        if not key:
            raise ProviderError("typesafe_key_missing")
        super().__init__(
            provider="jev",
            model=config.model,
            endpoint=config.base_url,
            key=key,
            input_rate=config.input_usd_per_million,
            output_rate=config.output_usd_per_million,
            limits=limits,
            budget=budget,
            tracker=tracker,
            run_id=run_id,
            client=client,
        )

    def judge(self, state: Any, questions: dict[str, Any]) -> dict[str, float]:
        if (
            not questions
            or len(questions) > 16
            or any(
                not isinstance(question, dict)
                or question.get("type") != "noul"
                or not isinstance(question.get("instructions"), str)
                for question in questions.values()
            )
        ):
            raise ProviderError("jev_requires_one_to_sixteen_noul_questions")
        body = {"model": self.model, "state": state, "questions": questions}

        def parse(payload: Any) -> dict[str, float]:
            answers = payload.get("answers")
            if not isinstance(answers, dict) or set(answers) != set(questions):
                raise ProviderError("jev_answer_ids_invalid")
            result = {}
            for key, answer in answers.items():
                if not isinstance(answer, dict) or answer.get("type") != "noul":
                    raise ProviderError("jev_answer_type_invalid")
                probability = answer.get("noul")
                if (
                    type(probability) not in (int, float)
                    or not math.isfinite(probability)
                    or not 0 <= probability <= 1
                ):
                    raise ProviderError("jev_probability_invalid")
                result[key] = float(probability)
            return result

        return self.request(body, parse, output_allowance=64 * len(questions))


def make_providers(
    config: Config, budget: Budget, tracker: Any, run_id: str
) -> tuple[SmallModelProvider, JevProvider | None]:
    small = SmallModelProvider(config.model, config.limits, budget, tracker, run_id)
    try:
        jev = (
            JevProvider(config.jev, config.limits, budget, tracker, run_id)
            if config.jev.enabled
            else None
        )
    except Exception:
        small.close()
        raise
    return small, jev
