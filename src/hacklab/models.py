"""Shared, strict task and output contracts. Benchmark labels never enter a Task."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Task(StrictModel):
    id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=12000)
    documents: list[str] = Field(default_factory=list, max_length=200)
    constraints: dict[str, Any] = Field(default_factory=dict)


class Citation(StrictModel):
    document: str
    locator: str
    quote: str = Field(min_length=1, max_length=2000)


class Answer(StrictModel):
    status: Literal["ok", "abstain", "error"]
    answer: str = Field(max_length=12000)
    citations: list[Citation] = Field(default_factory=list, max_length=30)
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = Field(default="", max_length=2000)


class Decision(StrictModel):
    action: Literal["search", "read", "calculate", "table", "final"]
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Answer | None = None
