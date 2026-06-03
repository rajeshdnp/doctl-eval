"""
Stage 2: GitHub issue ingestion with PR filtering, pagination, and caching.

Design decisions:
- Cache-first (Principle 2): data/issues.json is the stable corpus. Once fetched,
  we never re-fetch unless --refresh. This keeps the dataset fingerprint stable
  across all runs, which is required for RunManifest reproducibility.
- PR filtering is critical: GitHub's /issues endpoint returns both issues AND PRs.
  Failing to filter would corrupt the dataset with irrelevant content.
- Body truncation at 2000 chars: doctl issues can have very long bodies with code
  dumps. Extra body length adds tokens without adding classification signal.
- Rate limit handling: GitHub gives 5000 req/hr authenticated, 60/hr unauthenticated.
  We honor X-RateLimit-Remaining and sleep until reset when near the limit.

CLI:
    python -m src.ingestion.github [--refresh]
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path

import click
import httpx
from rich.console import Console

from src.models import Issue

console = Console()

GITHUB_API_BASE = "https://api.github.com"
ISSUES_PATH = Path("data/issues.json")
FINGERPRINT_PATH = Path("data/issues.sha256")
BODY_MAX_CHARS = 2000  # Trim bodies to this — extra length adds tokens, not signal


def compute_fingerprint(issues: list[Issue]) -> str:
    """SHA-256 of the serialized issue list — stable corpus identifier for RunManifest."""
    payload = json.dumps(
        [i.model_dump() for i in issues],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


async def fetch_all_issues(token: str | None, repo: str, per_page: int = 100) -> list[Issue]:
    """
    Paginate through all GitHub issues for the given repo, filtering out PRs.

    Pagination: we follow the Link header's rel="next" URL. This is more robust than
    computing page numbers because GitHub can change the page structure; the Link header
    is the authoritative next-page pointer.
    """
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        console.print(
            "[yellow]Warning:[/yellow] No GITHUB_TOKEN set. Rate limit: 60 req/hr. "
            "Set GITHUB_TOKEN for 5000/hr."
        )

    issues: list[Issue] = []
    pr_count = 0
    page = 1

    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30.0)) as client:
        url: str | None = (
            f"{GITHUB_API_BASE}/repos/{repo}/issues"
            f"?state=all&per_page={per_page}"
        )

        while url:
            console.print(f"Fetching page {page}... ({len(issues)} issues so far)")
            resp = await client.get(url)
            resp.raise_for_status()

            raw_items = resp.json()

            # Rate limit check — sleep if near the wall
            remaining = int(resp.headers.get("X-RateLimit-Remaining", 100))
            reset_ts = int(resp.headers.get("X-RateLimit-Reset", 0))
            if remaining < 10 and reset_ts > 0:
                import time

                wait = max(0, reset_ts - int(time.time())) + 1
                console.print(
                    f"[yellow]Rate limit low ({remaining} remaining). "
                    f"Sleeping {wait}s until reset...[/yellow]"
                )
                await asyncio.sleep(wait)

            for item in raw_items:
                # Critical: GitHub's /issues endpoint returns PRs too.
                # PRs have a "pull_request" key; real issues do not.
                if "pull_request" in item:
                    pr_count += 1
                    continue

                # Truncate body — long code dumps add tokens without adding signal
                body = item.get("body") or None
                if body:
                    body = body[:BODY_MAX_CHARS]

                issue = Issue(
                    id=item["id"],
                    number=item["number"],
                    title=item["title"],
                    body=body,
                    labels=[lbl["name"] for lbl in item.get("labels", [])],
                    state=item["state"],
                    created_at=item["created_at"],
                    updated_at=item["updated_at"],
                )
                issues.append(issue)

            # Follow Link header for next page
            link_header = resp.headers.get("Link", "")
            url = _parse_next_link(link_header)
            page += 1

    console.print(f"[green]Filtered out {pr_count} pull requests.[/green]")
    return issues


def _parse_next_link(link_header: str) -> str | None:
    """
    Parse the GitHub Link header to find the rel="next" URL.

    Format: <https://...?page=2>; rel="next", <https://...?page=10>; rel="last"
    Returns None if there is no next page (we're on the last page).
    """
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            # Extract URL between < and >
            url_part = part.split(";")[0].strip()
            return url_part[1:-1]  # Strip < and >
    return None


def load_or_fetch_sync(
    token: str | None,
    repo: str,
    per_page: int = 100,
    refresh: bool = False,
) -> tuple[list[Issue], str]:
    """
    Synchronous wrapper: returns (issues, fingerprint).

    Cache logic (Principle 2):
    - If data/issues.json exists AND refresh=False: load from disk, instant, $0
    - Otherwise: fetch from GitHub API, save, compute fingerprint
    """
    ISSUES_PATH.parent.mkdir(parents=True, exist_ok=True)

    if ISSUES_PATH.exists() and not refresh:
        console.print(f"Loading issues from cache: {ISSUES_PATH}")
        raw = json.loads(ISSUES_PATH.read_text())
        issues = [Issue(**item) for item in raw]
        fingerprint = compute_fingerprint(issues)
        console.print(
            f"[green]Loaded {len(issues)} issues from cache.[/green] "
            f"Fingerprint: {fingerprint[:16]}... "
            f"(Use --refresh to re-fetch)"
        )
        return issues, fingerprint

    console.print(f"[yellow]Fetching issues from GitHub API for {repo}...[/yellow]")
    issues = asyncio.run(fetch_all_issues(token, repo, per_page))

    # Save corpus
    ISSUES_PATH.write_text(
        json.dumps([i.model_dump() for i in issues], indent=2)
    )
    fingerprint = compute_fingerprint(issues)
    FINGERPRINT_PATH.write_text(fingerprint)

    console.print(f"[green]Fetched {len(issues)} issues.[/green] Saved to {ISSUES_PATH}")
    console.print(f"Fingerprint: {fingerprint}")

    # Top labels — useful for validating ground truth label mapping
    all_labels = [lbl for issue in issues for lbl in issue.labels]
    top_labels = Counter(all_labels).most_common(10)
    console.print("\nTop 10 GitHub labels found:")
    for label, count in top_labels:
        console.print(f"  {label}: {count}")

    return issues, fingerprint


@click.command()
@click.option("--refresh", is_flag=True, help="Re-fetch from GitHub API even if cache exists")
@click.option("--token", envvar="GITHUB_TOKEN", default=None, help="GitHub PAT")
@click.option("--repo", default="digitalocean/doctl", help="GitHub repo (owner/name)")
@click.option("--per-page", default=100, help="Issues per page (max 100)")
def main(refresh: bool, token: str | None, repo: str, per_page: int) -> None:
    """Fetch and cache GitHub issues for LLM evaluation."""
    from dotenv import load_dotenv

    load_dotenv()
    import os

    token = token or os.environ.get("GITHUB_TOKEN")
    load_or_fetch_sync(token, repo, per_page, refresh)


if __name__ == "__main__":
    main()
