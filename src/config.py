"""
Central configuration loading for doctl-eval.

Design: pydantic-settings reads environment variables first, then the config.yaml
provides non-secret structured config (models, pricing, inference params). Separating
secrets (API keys) from config (model list, pricing) means config.yaml can be committed
to git without any sensitive data.

The CONCURRENCY env var is the runtime knob per Principle 3 — never baked into the image.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class InferenceConfig:
    """Parsed inference section from config.yaml."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.temperature: float = data.get("temperature", 0.0)
        self.max_tokens: int = data.get("max_tokens", 256)
        self.prompt_version: str = data.get("prompt_version", "v1")
        self._concurrency: int = data.get("concurrency", 10)


class ModelsConfig:
    """Parsed models section from config.yaml."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.sweep: list[str] = data.get("sweep", [])
        self.default_a: str = data.get("default_a", "")
        self.default_b: str = data.get("default_b", "")


class GithubConfig:
    """Parsed github section from config.yaml."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.repo: str = data.get("repo", "digitalocean/doctl")
        self.per_page: int = data.get("per_page", 100)


class Config(BaseSettings):
    """
    Application configuration combining env vars and config.yaml.

    Priority order (highest first):
    1. Environment variables (MODEL_ACCESS_KEY, GITHUB_TOKEN, CONCURRENCY)
    2. .env file (loaded by python-dotenv)
    3. config.yaml defaults
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Secrets — from env only, never in config.yaml
    model_access_key: str = Field(description="DigitalOcean Serverless Inference API key")
    github_token: str | None = Field(
        default=None,
        description="GitHub PAT (optional — avoids 60/hr unauthenticated rate limit)",
    )

    # Runtime override for concurrency — Principle 3
    concurrency_override: int | None = Field(
        default=None,
        alias="CONCURRENCY",
        description="Parallel inference requests. Overrides config.yaml if set.",
    )

    # These are loaded post-init from config.yaml
    _yaml_config: dict[str, Any] = {}
    _inference: InferenceConfig | None = None
    _models: ModelsConfig | None = None
    _github: GithubConfig | None = None
    _pricing: dict[str, dict[str, float]] = {}

    def model_post_init(self, __context: Any) -> None:
        """Load config.yaml after pydantic-settings populates env vars."""
        config_path = Path("config.yaml")
        if config_path.exists():
            with config_path.open() as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        object.__setattr__(self, "_yaml_config", data)
        object.__setattr__(self, "_inference", InferenceConfig(data.get("inference", {})))
        object.__setattr__(self, "_models", ModelsConfig(data.get("models", {})))
        object.__setattr__(self, "_github", GithubConfig(data.get("github", {})))
        object.__setattr__(self, "_pricing", data.get("pricing", {}))

    @property
    def inference(self) -> InferenceConfig:
        assert self._inference is not None
        return self._inference

    @property
    def models(self) -> ModelsConfig:
        assert self._models is not None
        return self._models

    @property
    def github(self) -> GithubConfig:
        assert self._github is not None
        return self._github

    @property
    def pricing(self) -> dict[str, dict[str, float]]:
        return self._pricing

    @property
    def concurrency(self) -> int:
        """
        Concurrency with env var override taking precedence over config.yaml.
        This is Principle 3: CONCURRENCY must be a runtime env var.
        """
        if self.concurrency_override is not None:
            return self.concurrency_override
        return self.inference._concurrency

    def get_pricing(self, model_slug: str) -> dict[str, float]:
        """
        Returns {input: float, output: float} rates per 1M tokens for the given model.

        Raises ValueError with a helpful message if the slug isn't in the pricing table.
        This is intentionally strict — silently using wrong rates would corrupt all
        cost calculations without any visible error.
        """
        pricing = self._pricing
        if model_slug not in pricing:
            available = ", ".join(sorted(pricing.keys()))
            raise ValueError(
                f"Model '{model_slug}' not in pricing table. "
                f"Add it to config.yaml or run scripts/verify_models.py to find valid slugs. "
                f"Currently configured: {available}"
            )
        return pricing[model_slug]


@lru_cache(maxsize=1)
def get_config() -> Config:
    """
    Cached singleton config. Cached so we don't re-parse config.yaml on every call.
    Call get_config.cache_clear() in tests that need fresh config.
    """
    return Config()  # type: ignore[call-arg]
