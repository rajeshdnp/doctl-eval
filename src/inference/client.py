"""
Stage 5: Async inference client with cache-first, retry, and cost accounting.

Design decisions:
- Cache key includes model slug + prompt hash: ensures re-runs with a different
  prompt or model never return stale results. The hash is truncated to 16 chars
  for readable filenames.
- Retry only transient errors (429, 5xx, timeout). Never retry:
  - Parse errors: the model gave bad output; retrying with the same prompt costs
    money and likely fails the same way. The repair prompt handles this case.
  - 4xx errors (except 429): these are our problem, not transient.
- One repair attempt on ParseError: many models occasionally add fences or extra
  text despite instruction. A single correction attempt recovers this cheaply.
  If repair also fails, error_type="parse_error" and label=None.
- Always save to cache on completion, even for errors: prevents re-running
  permanent failures (e.g., a specific issue that always causes a refusal).
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import time

import httpx
import openai
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.config import Config
from src.inference.prompt import (
    PROMPT_VERSION,
    REPAIR_PROMPT,
    ParseError,
    build_messages,
    get_prompt_hash,
    parse_response,
)
from src.models import Classification, Issue, LabelEnum

logger = logging.getLogger(__name__)

DO_BASE_URL = "https://inference.do-ai.run/v1"


class InferenceClient:
    """
    Single-model async inference client.

    One instance per model per run — create new instances for each model in the sweep.
    Thread-safe for async use; do not share across event loops.
    """

    def __init__(self, model: str, config: Config) -> None:
        self.model = model
        self.config = config
        self.pricing = config.get_pricing(model)
        self._client = openai.AsyncOpenAI(
            base_url=DO_BASE_URL,
            api_key=config.model_access_key,
            timeout=30.0,
        )
        self._cache_dir = pathlib.Path("data/cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._attempt_counts: dict[int, int] = {}  # issue_id → attempts

    def _cache_path(self, issue_id: int) -> pathlib.Path:
        """
        Cache key: issue_id + model slug (sanitized) + prompt version hash.

        Using all three ensures:
        - Different models don't share cache entries
        - Editing the prompt without bumping version still busts the cache
        - Slashes in model slugs don't create subdirectories
        """
        slug_safe = self.model.replace("/", "-").replace(":", "_")
        key = f"{issue_id}__{slug_safe}__{get_prompt_hash()}"
        return self._cache_dir / f"{key}.json"

    async def classify(
        self,
        issue: Issue,
        semaphore: asyncio.Semaphore,
    ) -> Classification:
        """
        Classify one issue. Cache hit → returns instantly at $0 cost.

        The semaphore is acquired AFTER the cache check: cached responses don't
        need concurrency limiting since they make no API calls.
        """
        # Cache check — free, instant, deterministic (Principle 2)
        cache_file = self._cache_path(issue.id)
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                # Reconstruct with from_cache=True regardless of stored value
                return Classification(**{**data, "from_cache": True})
            except Exception:
                # Corrupt cache file — fall through to re-fetch
                pass

        # Acquire semaphore only for real API calls
        async with semaphore:
            return await self._call_with_retry(issue, cache_file)

    @retry(
        # Only retry on transient network/server errors
        retry=retry_if_exception_type(
            (
                openai.RateLimitError,
                httpx.TimeoutException,
                httpx.NetworkError,
            )
        ),
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(4),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _call_with_retry(
        self,
        issue: Issue,
        cache_file: pathlib.Path,
    ) -> Classification:
        """
        Make the actual API call. Wrapped by tenacity for transient error retry.

        Error taxonomy (Principle 6):
        - rate_limit: 429 — retried with exponential backoff + Retry-After header
        - timeout: httpx.TimeoutException — retried
        - server_error: 5xx — retried
        - parse_error: bad JSON or unknown label — NOT retried (repair attempt instead)
        - refusal: model declined to classify — NOT retried
        """
        self._attempt_counts[issue.id] = self._attempt_counts.get(issue.id, 0) + 1

        start = time.monotonic()
        prompt_tokens = 0
        completion_tokens = 0
        cost_usd = 0.0
        raw_text = ""
        error_type = None
        label: LabelEnum | None = None
        reasoning: str | None = None

        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=build_messages(issue),  # type: ignore[arg-type]
                temperature=self.config.inference.temperature,
                max_completion_tokens=self.config.inference.max_tokens,
            )
        except openai.APIStatusError as exc:
            if exc.status_code == 429:
                # Honor Retry-After header if present before tenacity retry
                retry_after = None
                if hasattr(exc, "response") and exc.response is not None:
                    retry_after = exc.response.headers.get("retry-after")
                if retry_after:
                    await asyncio.sleep(float(retry_after))
                error_type = "rate_limit"
                raise  # Let tenacity retry
            elif exc.status_code >= 500:
                error_type = "server_error"
                raise  # Let tenacity retry
            else:
                # 4xx other than 429 — our problem, don't retry
                error_type = "server_error"
                latency_ms = (time.monotonic() - start) * 1000
                result = self._make_error_result(
                    issue, raw_text, 0, 0, 0.0, latency_ms, error_type
                )
                cache_file.write_text(result.model_dump_json())
                return result
        except httpx.TimeoutException:
            error_type = "timeout"
            raise  # Let tenacity retry

        latency_ms = (time.monotonic() - start) * 1000
        raw_text = response.choices[0].message.content or ""

        # Cost accounting — traceable to token counts (Principle 5)
        prompt_tokens = response.usage.prompt_tokens if response.usage else 0
        completion_tokens = response.usage.completion_tokens if response.usage else 0
        cost_usd = (
            prompt_tokens * self.pricing["input"]
            + completion_tokens * self.pricing["output"]
        ) / 1_000_000

        # Parse response — attempt repair on first ParseError
        try:
            label, reasoning = parse_response(raw_text)
        except ParseError:
            # One repair attempt — send a correction prompt back to the model
            try:
                repair_resp = await self._client.chat.completions.create(
                    model=self.model,
                    messages=build_messages(issue)  # type: ignore[arg-type]
                    + [
                        {"role": "assistant", "content": raw_text},
                        {"role": "user", "content": REPAIR_PROMPT},
                    ],
                    temperature=0.0,  # Always deterministic for repair
                    max_completion_tokens=100,
                )
                repair_text = repair_resp.choices[0].message.content or ""
                label, reasoning = parse_response(repair_text)

                # Add repair call tokens to cost — full cost transparency
                if repair_resp.usage:
                    cost_usd += (
                        repair_resp.usage.prompt_tokens * self.pricing["input"]
                        + repair_resp.usage.completion_tokens * self.pricing["output"]
                    ) / 1_000_000
                    prompt_tokens += repair_resp.usage.prompt_tokens
                    completion_tokens += repair_resp.usage.completion_tokens

            except ParseError:
                # Repair failed — record parse_error, label stays None
                error_type = "parse_error"

        # Check for refusal patterns (model refuses to classify)
        if label is None and error_type is None:
            refusal_patterns = ["cannot", "unable to", "i can't", "i won't", "as an ai"]
            if any(p in raw_text.lower() for p in refusal_patterns):
                error_type = "refusal"

        result = Classification(
            issue_id=issue.id,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            label=label,
            reasoning=reasoning,
            raw_response=raw_text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            attempt_count=self._attempt_counts.get(issue.id, 1),
            error_type=error_type,
            from_cache=False,
        )

        # Always save to cache — even errors — so we don't retry permanent failures
        cache_file.write_text(result.model_dump_json())
        return result

    def _make_error_result(
        self,
        issue: Issue,
        raw_response: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        latency_ms: float,
        error_type: str,
    ) -> Classification:
        """Create a Classification with label=None for error cases."""
        return Classification(
            issue_id=issue.id,
            model=self.model,
            prompt_version=PROMPT_VERSION,
            label=None,
            reasoning=None,
            raw_response=raw_response,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
            attempt_count=self._attempt_counts.get(issue.id, 1),
            error_type=error_type,  # type: ignore[arg-type]
            from_cache=False,
        )
