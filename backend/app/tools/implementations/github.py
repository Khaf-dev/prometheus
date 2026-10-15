"""
GitHub Integration Tool — Prometheus Phase 2.

Capabilities:
- List PRs yang assigned ke lo / perlu review
- List open issues di repo
- Get PR details + diff untuk AI review
- Search issues by keyword
- Get repo info dan recent commits
- Summarize PR changes untuk agent context

Auth: GitHub Personal Access Token (classic)
Required scopes: repo, read:user
"""

import logging
import os
from typing import Any

import httpx

from app.tools.base import (
    BaseTool,
    PermissionLevel,
    ToolParameter,
    ToolResult,
    ToolSchema,
    ToolStatus,
)

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
MAX_DIFF_CHARS = 8000   # Limit diff size biar tidak overflow context window


def _github_client() -> httpx.AsyncClient:
    token = os.getenv("GITHUB_TOKEN", "")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(
        base_url=GITHUB_API,
        headers=headers,
        timeout=30.0,
    )


class GitHubListPRsTool(BaseTool):
    """List pull requests — assigned to me, created by me, atau review requested."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="github_list_prs",
            description=(
                "List GitHub Pull Requests. Bisa filter by repo, status, "
                "dan role lo (author, reviewer, assignee). "
                "Gunakan ini saat user tanya 'PR apa yang perlu gua review?' "
                "atau 'ada PR open di repo X?'"
            ),
            parameters=[
                ToolParameter(
                    name="repo",
                    type="string",
                    description=(
                        "Format: 'owner/repo' (contoh: 'kaff/prometheus'). "
                        "Kosongkan untuk search semua repo yang lo punya akses."
                    ),
                    required=False,
                    default="",
                ),
                ToolParameter(
                    name="state",
                    type="string",
                    description="Status PR: 'open', 'closed', atau 'all'.",
                    required=False,
                    default="open",
                    enum=["open", "closed", "all"],
                ),
                ToolParameter(
                    name="role",
                    type="string",
                    description=(
                        "Filter by role lo: "
                        "'author' (PR yang lo buat), "
                        "'review_requested' (PR yang minta review lo), "
                        "'assignee' (PR yang di-assign ke lo), "
                        "'all' (semua yang relevan)."
                    ),
                    required=False,
                    default="all",
                    enum=["author", "review_requested", "assignee", "all"],
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Jumlah PR maksimum yang dikembalikan (default: 10).",
                    required=False,
                    default=10,
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        repo: str = kwargs.get("repo", "")
        state: str = kwargs.get("state", "open")
        role: str = kwargs.get("role", "all")
        limit: int = min(kwargs.get("limit", 10), 30)

        try:
            async with _github_client() as client:
                if repo:
                    # Search di repo spesifik
                    resp = await client.get(
                        f"/repos/{repo}/pulls",
                        params={"state": state, "per_page": limit},
                    )
                    resp.raise_for_status()
                    prs = resp.json()
                else:
                    # Search di semua repo via search API
                    username = os.getenv("GITHUB_USERNAME", "")
                    qualifiers = [f"type:pr", f"state:{state}"]

                    if role == "author" and username:
                        qualifiers.append(f"author:{username}")
                    elif role == "review_requested" and username:
                        qualifiers.append(f"review-requested:{username}")
                    elif role == "assignee" and username:
                        qualifiers.append(f"assignee:{username}")
                    elif role == "all" and username:
                        qualifiers.append(
                            f"involves:{username}"
                        )

                    q = " ".join(qualifiers)
                    resp = await client.get(
                        "/search/issues",
                        params={"q": q, "per_page": limit, "sort": "updated"},
                    )
                    resp.raise_for_status()
                    prs = resp.json().get("items", [])

            results = []
            for pr in prs[:limit]:
                results.append({
                    "number": pr.get("number"),
                    "title": pr.get("title"),
                    "state": pr.get("state"),
                    "author": pr.get("user", {}).get("login"),
                    "repo": pr.get("repository_url", "").split("/repos/")[-1] if "repository_url" in pr else repo,
                    "url": pr.get("html_url"),
                    "created_at": pr.get("created_at", "")[:10],
                    "updated_at": pr.get("updated_at", "")[:10],
                    "draft": pr.get("draft", False),
                    "labels": [l.get("name") for l in pr.get("labels", [])],
                })

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output=results if results else "Tidak ada PR yang ditemukan.",
                metadata={"count": len(results)},
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                return ToolResult(
                    status=ToolStatus.ERROR,
                    output=None,
                    error="GitHub token tidak valid atau tidak ada. Set GITHUB_TOKEN di .env",
                )
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))
        except Exception as e:
            logger.exception("github_list_prs failed")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class GitHubGetPRTool(BaseTool):
    """Get detail PR + diff untuk AI review."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="github_get_pr",
            description=(
                "Get detail Pull Request termasuk description, file changes, dan diff. "
                "Gunakan ini untuk review PR atau summarize changes. "
                "Contoh: 'review PR #42 di repo kaff/prometheus'"
            ),
            parameters=[
                ToolParameter(
                    name="repo",
                    type="string",
                    description="Format: 'owner/repo' (contoh: 'kaff/prometheus').",
                ),
                ToolParameter(
                    name="pr_number",
                    type="integer",
                    description="Nomor PR yang ingin di-review.",
                ),
                ToolParameter(
                    name="include_diff",
                    type="boolean",
                    description="Sertakan diff code changes (default: true). Set false untuk summary saja.",
                    required=False,
                    default=True,
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        repo: str = kwargs["repo"]
        pr_number: int = kwargs["pr_number"]
        include_diff: bool = kwargs.get("include_diff", True)

        try:
            async with _github_client() as client:
                # Get PR details
                pr_resp = await client.get(f"/repos/{repo}/pulls/{pr_number}")
                pr_resp.raise_for_status()
                pr = pr_resp.json()

                # Get changed files
                files_resp = await client.get(
                    f"/repos/{repo}/pulls/{pr_number}/files",
                    params={"per_page": 30},
                )
                files_resp.raise_for_status()
                files = files_resp.json()

            # Format output
            result: dict[str, Any] = {
                "number": pr["number"],
                "title": pr["title"],
                "state": pr["state"],
                "author": pr["user"]["login"],
                "url": pr["html_url"],
                "base_branch": pr["base"]["ref"],
                "head_branch": pr["head"]["ref"],
                "created_at": pr["created_at"][:10],
                "updated_at": pr["updated_at"][:10],
                "description": pr.get("body", "")[:500] if pr.get("body") else "(no description)",
                "draft": pr.get("draft", False),
                "mergeable": pr.get("mergeable"),
                "additions": pr.get("additions", 0),
                "deletions": pr.get("deletions", 0),
                "changed_files": pr.get("changed_files", 0),
                "files": [],
            }

            # Add file changes
            total_diff_chars = 0
            for f in files:
                file_info: dict[str, Any] = {
                    "filename": f["filename"],
                    "status": f["status"],  # added/removed/modified/renamed
                    "additions": f["additions"],
                    "deletions": f["deletions"],
                }

                if include_diff and "patch" in f:
                    patch = f["patch"]
                    # Limit diff size per file
                    if total_diff_chars + len(patch) < MAX_DIFF_CHARS:
                        file_info["diff"] = patch
                        total_diff_chars += len(patch)
                    else:
                        file_info["diff"] = "(diff truncated — too large)"

                result["files"].append(file_info)

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output=result,
                metadata={"pr_number": pr_number, "repo": repo},
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return ToolResult(
                    status=ToolStatus.ERROR,
                    output=None,
                    error=f"PR #{pr_number} tidak ditemukan di repo '{repo}'.",
                )
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))
        except Exception as e:
            logger.exception("github_get_pr failed")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class GitHubListIssuesTool(BaseTool):
    """List GitHub issues di repo."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="github_list_issues",
            description=(
                "List GitHub Issues di repo. Bisa filter by label, assignee, state. "
                "Gunakan ini saat user tanya 'ada issue open tentang X?' "
                "atau 'issue apa yang di-assign ke gua?'"
            ),
            parameters=[
                ToolParameter(
                    name="repo",
                    type="string",
                    description="Format: 'owner/repo'.",
                ),
                ToolParameter(
                    name="state",
                    type="string",
                    description="'open', 'closed', atau 'all'.",
                    required=False,
                    default="open",
                    enum=["open", "closed", "all"],
                ),
                ToolParameter(
                    name="label",
                    type="string",
                    description="Filter by label (contoh: 'bug', 'enhancement'). Kosongkan untuk semua.",
                    required=False,
                    default="",
                ),
                ToolParameter(
                    name="assignee",
                    type="string",
                    description="Filter by assignee username. Pakai 'me' untuk filter ke diri sendiri.",
                    required=False,
                    default="",
                ),
                ToolParameter(
                    name="search",
                    type="string",
                    description="Keyword untuk search di title/body issue.",
                    required=False,
                    default="",
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Jumlah issue maksimum (default: 10).",
                    required=False,
                    default=10,
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        repo: str = kwargs["repo"]
        state: str = kwargs.get("state", "open")
        label: str = kwargs.get("label", "")
        assignee: str = kwargs.get("assignee", "")
        search: str = kwargs.get("search", "")
        limit: int = min(kwargs.get("limit", 10), 30)

        # Resolve "me" ke username
        if assignee == "me":
            assignee = os.getenv("GITHUB_USERNAME", "")

        try:
            async with _github_client() as client:
                if search:
                    # Pakai search API
                    q = f"repo:{repo} type:issue state:{state} {search}"
                    if label:
                        q += f" label:{label}"
                    if assignee:
                        q += f" assignee:{assignee}"
                    resp = await client.get(
                        "/search/issues",
                        params={"q": q, "per_page": limit, "sort": "updated"},
                    )
                else:
                    params: dict[str, Any] = {
                        "state": state,
                        "per_page": limit,
                        "sort": "updated",
                    }
                    if label:
                        params["labels"] = label
                    if assignee:
                        params["assignee"] = assignee
                    resp = await client.get(
                        f"/repos/{repo}/issues",
                        params=params,
                    )

                resp.raise_for_status()
                data = resp.json()
                issues = data.get("items", data) if isinstance(data, dict) else data

            # Filter out PRs (issues endpoint returns both)
            issues = [i for i in issues if "pull_request" not in i]

            results = []
            for issue in issues[:limit]:
                results.append({
                    "number": issue.get("number"),
                    "title": issue.get("title"),
                    "state": issue.get("state"),
                    "author": issue.get("user", {}).get("login"),
                    "assignees": [a.get("login") for a in issue.get("assignees", [])],
                    "labels": [l.get("name") for l in issue.get("labels", [])],
                    "url": issue.get("html_url"),
                    "created_at": issue.get("created_at", "")[:10],
                    "updated_at": issue.get("updated_at", "")[:10],
                    "comments": issue.get("comments", 0),
                    "body_preview": (issue.get("body") or "")[:200],
                })

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output=results if results else "Tidak ada issue yang ditemukan.",
                metadata={"count": len(results)},
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return ToolResult(
                    status=ToolStatus.ERROR,
                    output=None,
                    error=f"Repo '{repo}' tidak ditemukan atau tidak punya akses.",
                )
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))
        except Exception as e:
            logger.exception("github_list_issues failed")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class GitHubGetRepoTool(BaseTool):
    """Get repo info dan recent commits."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="github_get_repo",
            description=(
                "Get informasi repo: description, stats, recent commits, branches. "
                "Gunakan untuk memberikan context repo kepada agent sebelum review kode."
            ),
            parameters=[
                ToolParameter(
                    name="repo",
                    type="string",
                    description="Format: 'owner/repo'.",
                ),
                ToolParameter(
                    name="include_commits",
                    type="boolean",
                    description="Sertakan recent commits (default: true).",
                    required=False,
                    default=True,
                ),
                ToolParameter(
                    name="branch",
                    type="string",
                    description="Branch untuk recent commits (default: main/master).",
                    required=False,
                    default="",
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        repo: str = kwargs["repo"]
        include_commits: bool = kwargs.get("include_commits", True)
        branch: str = kwargs.get("branch", "")

        try:
            async with _github_client() as client:
                # Get repo info
                repo_resp = await client.get(f"/repos/{repo}")
                repo_resp.raise_for_status()
                repo_data = repo_resp.json()

                result: dict[str, Any] = {
                    "name": repo_data["name"],
                    "full_name": repo_data["full_name"],
                    "description": repo_data.get("description", ""),
                    "language": repo_data.get("language"),
                    "stars": repo_data.get("stargazers_count", 0),
                    "forks": repo_data.get("forks_count", 0),
                    "open_issues": repo_data.get("open_issues_count", 0),
                    "default_branch": repo_data.get("default_branch", "main"),
                    "url": repo_data.get("html_url"),
                    "created_at": repo_data.get("created_at", "")[:10],
                    "updated_at": repo_data.get("updated_at", "")[:10],
                    "topics": repo_data.get("topics", []),
                }

                if include_commits:
                    branch_ref = branch or repo_data.get("default_branch", "main")
                    commits_resp = await client.get(
                        f"/repos/{repo}/commits",
                        params={"sha": branch_ref, "per_page": 10},
                    )
                    if commits_resp.status_code == 200:
                        commits = commits_resp.json()
                        result["recent_commits"] = [
                            {
                                "sha": c["sha"][:7],
                                "message": c["commit"]["message"].split("\n")[0][:100],
                                "author": c["commit"]["author"]["name"],
                                "date": c["commit"]["author"]["date"][:10],
                            }
                            for c in commits
                        ]
                    else:
                        result["recent_commits"] = []

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output=result,
                metadata={"repo": repo},
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return ToolResult(
                    status=ToolStatus.ERROR,
                    output=None,
                    error=f"Repo '{repo}' tidak ditemukan atau tidak punya akses.",
                )
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))
        except Exception as e:
            logger.exception("github_get_repo failed")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class GitHubSearchCodeTool(BaseTool):
    """Search kode di GitHub repo."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="github_search_code",
            description=(
                "Search kode di GitHub repo by keyword. "
                "Berguna untuk cari implementasi fungsi, pattern, atau string tertentu di codebase."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Keyword yang dicari di kode.",
                ),
                ToolParameter(
                    name="repo",
                    type="string",
                    description="Batasi search ke repo tertentu (format: 'owner/repo'). Kosongkan untuk search semua.",
                    required=False,
                    default="",
                ),
                ToolParameter(
                    name="language",
                    type="string",
                    description="Filter by bahasa (contoh: 'python', 'typescript'). Kosongkan untuk semua.",
                    required=False,
                    default="",
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Jumlah hasil maksimum (default: 5).",
                    required=False,
                    default=5,
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        query: str = kwargs["query"]
        repo: str = kwargs.get("repo", "")
        language: str = kwargs.get("language", "")
        limit: int = min(kwargs.get("limit", 5), 10)

        q = query
        if repo:
            q += f" repo:{repo}"
        if language:
            q += f" language:{language}"

        try:
            async with _github_client() as client:
                resp = await client.get(
                    "/search/code",
                    params={"q": q, "per_page": limit},
                )
                resp.raise_for_status()
                data = resp.json()

            results = []
            for item in data.get("items", [])[:limit]:
                results.append({
                    "file": item.get("name"),
                    "path": item.get("path"),
                    "repo": item.get("repository", {}).get("full_name"),
                    "url": item.get("html_url"),
                })

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output=results if results else "Tidak ada kode yang ditemukan.",
                metadata={"query": query, "total": data.get("total_count", 0)},
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                return ToolResult(
                    status=ToolStatus.ERROR,
                    output=None,
                    error="Rate limit GitHub search. Coba lagi dalam beberapa detik.",
                )
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))
        except Exception as e:
            logger.exception("github_search_code failed")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))