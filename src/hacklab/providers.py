"""Small, explicit HTTP adapters. Every request attempt has a receipt.

No provider-specific SDK retry layer is hidden underneath this module. The
optional development cache is explicit: a replayed reply gets its own receipt
marked as a cache hit. Cost limits reserve a conservative request allowance
before sending. A remote invoice cannot be guaranteed when the remote service
omits usage or times out.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx

from hacklab.cache import ResponseCache
from hacklab.config import Config, DevBudget, JevConfig, Limits, ModelConfig, read_secret

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


class DevSpend:
    """Live spend recorded in one tracking database, held against the team's allowance.

    The baseline is read once per task; requests of this task are added as
    they happen. Replayed replies cost nothing and are not counted.
    """

    def __init__(self, budget: DevBudget, tracker: Any):
        self.budget = budget
        spent = tracker.spend()
        self.calls: int = spent["calls"]
        self.tokens: int = spent["tokens"]
        self.usd: float = spent["usd"]

    def check(self) -> None:
        limits = (
            (self.budget.max_calls, self.calls),
            (self.budget.max_tokens, self.tokens),
            (self.budget.max_usd, self.usd),
        )
        if any(limit is not None and used >= limit for limit, used in limits):
            raise BudgetExceeded("development_budget_exhausted")

    def add(self, input_tokens: int | None, output_tokens: int | None, cost: float | None) -> None:
        self.calls += 1
        self.tokens += (input_tokens or 0) + (output_tokens or 0)
        self.usd += cost or 0.0


def decision_object(content: str) -> dict[str, Any]:
    """Find the one JSON object in a small model's reply.

    Small models often wrap the object in a code fence, reasoning tags or a
    sentence. Recovering it locally costs nothing; a repair turn costs a call.
    """
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE).strip()
    try:
        value = json.loads(text)
    except ValueError:
        value = None
        decoder = json.JSONDecoder()
        for match in re.finditer(r"\{", text):
            try:
                value, _ = decoder.raw_decode(text, match.start())
            except ValueError:
                continue
            if isinstance(value, dict):
                break
            value = None
        if value is None:
            raise ModelOutputError("model_content_must_be_json_text") from None
    if not isinstance(value, dict):
        raise ModelOutputError("model_decision_must_be_object")
    return value


def _token(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _usage(payload: Any, provider: str) -> tuple[int | None, int | None]:
    if not isinstance(payload, dict) or not isinstance(payload.get("usage"), dict):
        return None, None
    usage = payload["usage"]
    if provider == "jev":
        return _token(usage.get("input_tokens")), _token(usage.get("output_tokens"))
    return _token(usage.get("prompt_tokens")), _token(usage.get("completion_tokens"))


def _usage_details(payload: Any) -> dict[str, int]:
    """Cached prompt tokens and hidden reasoning tokens, where an endpoint reports them."""
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return {}
    details = {}
    for name, section, key in (
        ("cached_tokens", "prompt_tokens_details", "cached_tokens"),
        ("reasoning_tokens", "completion_tokens_details", "reasoning_tokens"),
    ):
        value = usage.get(section)
        if isinstance(value, dict) and _token(value.get(key)) is not None:
            details[name] = value[key]
    return details


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
        cache: ResponseCache | None = None,
        cache_salt: str = "",
        spend: DevSpend | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
    ):
        self.cache = cache
        self.cache_salt = cache_salt
        self.spend = spend
        self.auth_header = auth_header
        self.auth_scheme = auth_scheme
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
        key = None
        if self.cache is not None:
            key = self.cache.key(self.provider, self.endpoint, body, self.cache_salt)
            cached = self.cache.get(key)
            if cached is not None:
                return self._replay(body, cached, parse, output_allowance)
            if self.cache.mode == "replay":
                raise ProviderError("cache_miss_in_replay_mode")
        for attempt in range(self.limits.max_retries + 1):
            if self.spend is not None:
                self.spend.check()
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
                    headers={self.auth_header: f"{self.auth_scheme} {self.key}".strip()},
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
                        if self.cache is not None and key is not None:
                            # Stored before parsing: a reply that needs a repair
                            # turn replays as the same reply.
                            self.cache.put(key, self.provider, payload)
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
                    **_usage_details(payload),
                )
                if self.spend is not None:
                    known = None not in (input_tokens, output_tokens, self.input_rate)
                    self.spend.add(
                        input_tokens,
                        output_tokens,
                        (input_tokens * self.input_rate + output_tokens * (self.output_rate or 0.0))
                        / 1_000_000
                        if known
                        else None,
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

    def _replay(
        self, body: dict[str, Any], payload: dict, parse: Callable[[Any], Any], allowance: int
    ) -> Any:
        """Serve a stored reply. It counts against the task budget like a live one."""
        reservation = self.budget.reserve(body, allowance, self.input_rate, self.output_rate)
        input_tokens, output_tokens = _usage(payload, self.provider)
        error: ProviderError | None = None
        result: Any = None
        try:
            result = parse(payload)
        except ProviderError as exc:
            error = exc
        except (ValueError, KeyError, TypeError, IndexError):
            error_type = ModelOutputError if self.provider == "openai" else ProviderError
            error = error_type("provider_response_schema_invalid")
        self.budget.settle(
            reservation, input_tokens, output_tokens, self.input_rate, self.output_rate
        )
        reported = payload.get("model")
        self.tracker.record_call(
            self.run_id,
            provider=self.provider,
            model=reported if isinstance(reported, str) and reported.strip() else self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=0.0,
            status="cache_hit",
            input_rate=self.input_rate,
            output_rate=self.output_rate,
            cache_hit=True,
            **_usage_details(payload),
        )
        self.budget.check_consumed()
        if error is not None:
            raise error
        return result


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
        cache: ResponseCache | None = None,
        cache_salt: str = "",
        spend: DevSpend | None = None,
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
            cache=cache,
            cache_salt=cache_salt,
            spend=spend,
            auth_header=config.auth_header,
            auth_scheme=config.auth_scheme,
        )

    def complete(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            **self.config.extra_body,
            "model": self.model,
            "messages": messages,
            self.config.max_tokens_parameter: self.limits.max_output_tokens,
        }
        if self.config.temperature != "omit":
            body["temperature"] = self.config.temperature
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
            message = choice["message"]
            content = message["content"]
            if isinstance(content, list):
                # Some gateways return the reply as typed parts.
                content = "".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
            if not isinstance(content, str) or not content.strip():
                if message.get("reasoning_content") or message.get("reasoning"):
                    # The whole allowance went into hidden thinking.
                    raise ModelOutputError("model_returned_reasoning_only")
                raise ModelOutputError("model_content_must_be_json_text")
            return decision_object(content)

        return self.request(body, parse, output_allowance=self.limits.max_output_tokens)


class _Outage:
    """Consecutive failed Jev requests in this process.

    Jev reviews and filters; the small model answers. If the evaluation gives
    the container no route to Jev, asking again for every task would only
    spend the time limit, so after `limit` failures in a row it is left out.
    """

    limit = 3

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures = 0

    def record(self, ok: bool) -> None:
        with self._lock:
            self._failures = 0 if ok else self._failures + 1

    def reached(self) -> bool:
        with self._lock:
            return self._failures >= self.limit


JEV_OUTAGE = _Outage()


def jev_usable(config: JevConfig) -> bool:
    """Whether a task run should ask Jev at all: switched on, a key present, no outage."""
    return config.enabled and bool(read_secret(config.api_key_env)) and not JEV_OUTAGE.reached()


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
        cache: ResponseCache | None = None,
        cache_salt: str = "",
        spend: DevSpend | None = None,
    ):
        key = read_secret(config.api_key_env)
        if not key:
            raise ProviderError("typesafe_key_missing")
        # A gateway may be given as a host or an API prefix instead of the full path.
        endpoint = config.base_url.rstrip("/")
        if not endpoint.endswith("/systemone"):
            endpoint += "/systemone" if endpoint.endswith("/v1") else "/v1/systemone"
        super().__init__(
            provider="jev",
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
            cache=cache,
            cache_salt=cache_salt,
            spend=spend,
        )

    def request(
        self, body: dict[str, Any], parse: Callable[[Any], Any], *, output_allowance: int
    ) -> Any:
        try:
            result = super().request(body, parse, output_allowance=output_allowance)
        except BudgetExceeded:
            raise  # The task ran out of allowance; that says nothing about Jev.
        except ProviderError as exc:
            # A miss in replay mode is a missing recording, not a missing Jev.
            if str(exc) != "cache_miss_in_replay_mode":
                JEV_OUTAGE.record(False)
            raise
        JEV_OUTAGE.record(True)
        return result

    def choose(self, state: Any, instructions: str, criteria: dict[str, str]) -> tuple[str, float]:
        """One Choice question: the selected option and Jev's confidence in it.

        The confidence derives from the probabilities of the options; it is
        not a second, independent check.
        """
        if not 2 <= len(criteria) <= 255 or not instructions.strip():
            raise ProviderError("jev_choice_requires_instructions_and_two_to_255_options")
        question = {"type": "choice", "instructions": instructions, "criteria": criteria}
        body = {"model": self.model, "state": state, "questions": {"choice": question}}

        def parse(payload: Any) -> tuple[str, float]:
            answer = payload.get("answers", {}).get("choice")
            if not isinstance(answer, dict) or answer.get("type") != "choice":
                raise ProviderError("jev_answer_type_invalid")
            choice, confidence = answer.get("choice"), answer.get("confidence")
            if choice not in criteria:
                raise ProviderError("jev_choice_invalid")
            if (
                type(confidence) not in (int, float)
                or not math.isfinite(confidence)
                or not 0 <= confidence <= 1
            ):
                raise ProviderError("jev_probability_invalid")
            return choice, float(confidence)

        return self.request(body, parse, output_allowance=64)

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
    config: Config, budget: Budget, tracker: Any, run_id: str, cache_salt: str = ""
) -> tuple[SmallModelProvider, JevProvider | None]:
    cache = (
        ResponseCache(Path(config.cache.path), config.cache.mode)
        if config.cache.mode != "off"
        else None
    )
    limited = any(
        value is not None
        for value in (config.budget.max_calls, config.budget.max_tokens, config.budget.max_usd)
    )
    shared: dict[str, Any] = {
        "cache": cache,
        "cache_salt": cache_salt,
        "spend": DevSpend(config.budget, tracker) if limited else None,
    }
    small = SmallModelProvider(config.model, config.limits, budget, tracker, run_id, **shared)
    try:
        jev = (
            JevProvider(config.jev, config.limits, budget, tracker, run_id, **shared)
            if config.jev.enabled
            else None
        )
    except Exception:
        small.close()
        raise
    return small, jev
