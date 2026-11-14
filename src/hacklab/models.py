"""Shared, strict task and output contracts. Benchmark labels never enter a Task."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ModelReply(BaseModel):
    """What the small model writes. Unknown keys are dropped instead of costing a repair turn."""

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class Task(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=12000)
    documents: list[str] = Field(default_factory=list, max_length=2000)
    constraints: dict[str, Any] = Field(default_factory=dict)


class Citation(StrictModel):
    document: str
    locator: str
    quote: str = Field(min_length=1, max_length=2000)


class Action(ModelReply):
    """One containment step, such as revoking a token or quarantining an agent."""

    type: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=500)
    reason: str = Field(default="", max_length=500)


# The event's result codes; "" when the model named none.
OUTCOMES = ("OK", "DENIED_SECURITY", "NEEDS_CLARIFICATION", "UNSUPPORTED")


class Answer(StrictModel):
    status: Literal["ok", "abstain", "error"]
    answer: str = Field(max_length=12000)
    # The event grades a typed answer: `value` is the list, number or object
    # the model wrote, or the values of a list result; None for plain text.
    value: Any = None
    outcome: str = Field(default="", max_length=40)
    citations: list[Citation] = Field(default_factory=list, max_length=30)
    actions: list[Action] = Field(default_factory=list, max_length=2000)
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = Field(default="", max_length=2000)
    # Set when the harness, not the model, ended the task: a fixed code such as
    # token_budget_exhausted_before_request, provider_http_429 or step_limit_reached.
    cause: str = Field(default="", max_length=100)


class ActionReply(ModelReply):
    """A containment step as the model writes it: one target, or a whole list result."""

    type: str = Field(min_length=1, max_length=100)
    target: str = Field(default="", max_length=500)
    # One list result or several: "r2", "r2, r3" or ["r2", "r3"].
    targets_from: str | None = Field(default=None, max_length=200)
    reason: str = Field(default="", max_length=500)

    @field_validator("target", "reason", mode="before")
    @classmethod
    def _text(cls, value: Any) -> Any:
        return "" if value is None else str(value)[:500]

    @field_validator("targets_from", mode="before")
    @classmethod
    def _references(cls, value: Any) -> Any:
        return ", ".join(map(str, value)) if isinstance(value, list) else value


class FinalResult(ModelReply):
    """The model names evidence by ID; code fills in document, locator and quote.

    Small models vary the shape of a reply. Variations whose meaning is clear
    are accepted here, because rejecting them costs a paid repair turn.
    """

    status: Literal["ok", "abstain", "error"] = "ok"
    answer: str = Field(default="", max_length=12000)
    # The answer as the model wrote it when it is not text: a list, number or object.
    value: Any = None
    outcome: str = Field(default="", max_length=40)
    # A list result such as "r3": code writes all of its values as the answer.
    answer_from: str | None = Field(default=None, max_length=200)
    evidence: list[str] = Field(default_factory=list, max_length=30)
    actions: list[ActionReply] = Field(default_factory=list, max_length=200)
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = Field(default="", max_length=2000)

    @field_validator("answer_from", mode="before")
    @classmethod
    def _answer_references(cls, value: Any) -> Any:
        return ", ".join(map(str, value)) if isinstance(value, list) else value

    @model_validator(mode="before")
    @classmethod
    def _typed_answer(cls, data: Any) -> Any:
        """Keep a list, number or object answer as written, beside its text form."""
        if isinstance(data, dict) and not isinstance(data.get("answer"), (str, type(None))):
            data = {**data, "value": data["answer"]}
        return data

    @field_validator("outcome", mode="before")
    @classmethod
    def _outcome_code(cls, value: Any) -> Any:
        code = str(value or "").strip().upper().replace(" ", "_")
        return code if code in OUTCOMES else ""

    @field_validator("answer", mode="before")
    @classmethod
    def _answer_text(cls, value: Any) -> Any:
        if isinstance(value, list):
            return ", ".join(
                item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
                for item in value
            )[:12000]
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)[:12000]
        if isinstance(value, (bool, int, float)):
            return json.dumps(value)
        return "" if value is None else value

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, (str, int)):
            value = [value]
        if isinstance(value, list):
            return [f"e{item}" if isinstance(item, int) else item for item in value]
        return value

    @field_validator("actions", mode="before")
    @classmethod
    def _actions_list(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence_number(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.0
        return min(1.0, max(0.0, float(value)))

    @field_validator("reason", mode="before")
    @classmethod
    def _reason_text(cls, value: Any) -> Any:
        return "" if value is None else str(value)[:2000]


_ACTION_KEYS = ("action", "tool", "name", "function")
_ARGUMENT_KEYS = ("arguments", "args", "parameters", "input")
_RESULT_KEYS = {"status", "answer", "answer_from", "evidence", "actions"}


class Decision(ModelReply):
    action: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: FinalResult | None = None

    @model_validator(mode="before")
    @classmethod
    def _usual_shapes(cls, value: Any) -> Any:
        """Accept the common variants: `tool`/`args` names, flat arguments, a flat final."""
        if not isinstance(value, dict):
            return value
        action = next((value[key] for key in _ACTION_KEYS if isinstance(value.get(key), str)), None)
        arguments = next(
            (value[key] for key in _ARGUMENT_KEYS if isinstance(value.get(key), dict)), None
        )
        result = value.get("result")
        known = {*_ACTION_KEYS, *_ARGUMENT_KEYS, "result", "thought"}
        rest = {key: item for key, item in value.items() if key not in known}
        if action == "final" or (action is None and _RESULT_KEYS & set(rest)):
            if not isinstance(result, dict):
                # The answer fields sit beside "action" or inside "arguments".
                flat = arguments if arguments and _RESULT_KEYS & set(arguments) else rest
                result = flat if _RESULT_KEYS & set(flat) else result
            return {"action": "final", "result": result}
        if arguments is None:
            arguments = rest
        return {"action": action, "arguments": arguments, "result": None}
