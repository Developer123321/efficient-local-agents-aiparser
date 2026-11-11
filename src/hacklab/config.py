"""Non-secret TOML configuration; credentials are resolved only at runtime."""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from hacklab.models import StrictModel


class ModelConfig(StrictModel):
    provider: Literal["mock", "openai"] = "mock"
    base_url: str = ""
    model: str = ""
    api_key_env: str = "SMALL_MODEL_API_KEY"
    response_format: Literal["json_schema", "json_object", "none"] = "json_object"
    input_usd_per_million: float | None = Field(default=None, ge=0)
    output_usd_per_million: float | None = Field(default=None, ge=0)


class JevConfig(StrictModel):
    enabled: bool = False
    base_url: str = "https://api.typesafe.ai/v1/systemone"
    model: str = "jev-1.14.0"
    api_key_env: str = "TYPESAFE_API_KEY"
    input_usd_per_million: float | None = Field(default=None, ge=0)
    output_usd_per_million: float | None = Field(default=None, ge=0)
    threshold: float = Field(default=0.8, ge=0, le=1)


class Limits(StrictModel):
    max_steps: int = Field(default=6, ge=1, le=30)
    max_calls: int = Field(default=10, ge=1, le=100)
    max_retries: int = Field(default=1, ge=0, le=3)
    timeout_seconds: float = Field(default=25, gt=0, le=120)
    task_timeout_seconds: float = Field(default=180, gt=0, le=1800)
    max_context_chars: int = Field(default=24000, ge=2000, le=200000)
    max_output_tokens: int = Field(default=1400, ge=128, le=8192)
    max_total_tokens: int = Field(default=16000, ge=128)
    max_cost_usd: float | None = Field(default=None, gt=0)


class Config(StrictModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    jev: JevConfig = Field(default_factory=JevConfig)
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def priced_budget(self):
        if self.limits.max_cost_usd is not None:
            providers = [self.model] + ([self.jev] if self.jev.enabled else [])
            if self.model.provider != "mock" and any(
                p.input_usd_per_million is None or p.output_usd_per_million is None
                for p in providers
            ):
                raise ValueError(
                    "A cost budget requires explicit prices for every enabled provider"
                )
        return self


def load_config(path: Path | None = None) -> Config:
    data = tomllib.loads(path.read_text()) if path else {}
    model = data.setdefault("model", {})
    for env, key in [("SMALL_MODEL_BASE_URL", "base_url"), ("SMALL_MODEL_NAME", "model")]:
        if os.environ.get(env):
            model[key] = os.environ[env]
    return Config.model_validate(data)


def read_secret(env_name: str) -> str:
    """Support secret-file channels without placing secret values in Nix or arguments."""
    if file_name := os.environ.get(env_name + "_FILE"):
        return Path(file_name).read_text().strip()
    return os.environ.get(env_name, "").strip()
