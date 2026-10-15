"""
Browser Automation Tool — Prometheus Phase 2.

Capabilities:
- Fetch webpage content (clean text, no JS needed)
- Search web via DuckDuckGo (no API key needed)
- Screenshot halaman (optional)

Pakai httpx untuk simple fetches, Playwright untuk JS-heavy sites.
Install: pip install playwright && playwright install chromium
"""

import logging
import os
from typing import Any
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from app.tools.base import (
    BaseTool,
    PermissionLevel,
    ToolParameter,
    ToolResult,
    ToolSchema,
    ToolStatus,
)

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 8000
USER_AGENT = "Mozilla/5.0 (compatible; PrometheusBot/1.0)"


async def _fetch_url_simple(url: str) -> str:
    """Fetch URL pakai httpx (cepat, untuk static pages)."""
    async with httpx.AsyncClient(
        timeout=30.0,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def _fetch_url_playwright(url: str) -> str:
    """Fetch URL pakai Playwright (untuk JS-heavy pages)."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)
        content = await page.content()
        await browser.close()
        return content


def _extract_text(html: str, max_chars: int = MAX_CONTENT_CHARS) -> str:
    """Extract clean text dari HTML pakai BeautifulSoup."""
    soup = BeautifulSoup(html, "html.parser")

    # Hapus elemen yang tidak perlu
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "iframe", "noscript", "form"]):
        tag.decompose()

    # Ambil text
    text = soup.get_text(separator="\n", strip=True)

    # Clean up whitespace berlebihan
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    clean = "\n".join(lines)

    return clean[:max_chars]


class BrowserFetchTool(BaseTool):
    """Fetch dan extract konten dari URL."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="browser_fetch",
            description=(
                "Buka satu URL BARU dan ambil isinya. "
                "Hanya pakai kalau user memberikan alamat web pada saat ini. "
                "BUKAN untuk mencari dokumen yang sudah tersimpan — "
                "untuk itu gunakan rag_search."
            ),
            parameters=[
                ToolParameter(
                    name="url",
                    type="string",
                    description="URL yang ingin di-fetch.",
                ),
                ToolParameter(
                    name="use_playwright",
                    type="boolean",
                    description="Force pakai Playwright (untuk SPA/JS-heavy). Default: false (auto-detect).",
                    required=False,
                    default=False,
                ),
                ToolParameter(
                    name="max_chars",
                    type="integer",
                    description="Maksimum karakter yang dikembalikan (default: 8000).",
                    required=False,
                    default=8000,
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        url: str = kwargs["url"]
        use_playwright: bool = kwargs.get("use_playwright", False)
        max_chars: int = min(kwargs.get("max_chars", 8000), 16000)

        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        try:
            # Try simple fetch dulu
            if use_playwright:
                html = await _fetch_url_playwright(url)
            else:
                try:
                    html = await _fetch_url_simple(url)
                except Exception:
                    # Fallback ke Playwright
                    logger.info(f"Simple fetch failed for {url}, trying Playwright...")
                    html = await _fetch_url_playwright(url)

            text = _extract_text(html, max_chars)

            if not text.strip():
                return ToolResult(
                    status=ToolStatus.ERROR,
                    output=None,
                    error="Tidak ada konten yang bisa di-extract dari URL ini.",
                )

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output={
                    "url": url,
                    "content": text,
                    "char_count": len(text),
                    "truncated": len(text) >= max_chars,
                },
                metadata={"url": url},
            )

        except httpx.HTTPStatusError as e:
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=f"HTTP {e.response.status_code}: {url}",
            )
        except Exception as e:
            logger.exception(f"browser_fetch failed: {url}")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class BrowserSearchTool(BaseTool):
    """Search web via DuckDuckGo — no API key needed."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="browser_search",
            description=(
                "Search internet via DuckDuckGo. Tidak butuh API key. "
                "Gunakan untuk research, cari dokumentasi, atau cek informasi terkini. "
                "Return daftar hasil dengan title, URL, dan snippet."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Query pencarian.",
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Jumlah hasil maksimum (default: 5).",
                    required=False,
                    default=5,
                ),
                ToolParameter(
                    name="fetch_first",
                    type="boolean",
                    description="Auto-fetch konten dari hasil pertama (default: false).",
                    required=False,
                    default=False,
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        query: str = kwargs["query"]
        limit: int = min(kwargs.get("limit", 5), 10)
        fetch_first: bool = kwargs.get("fetch_first", False)

        try:
            # DuckDuckGo HTML search (tidak butuh API key)
            search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"

            async with httpx.AsyncClient(
                timeout=30.0,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as client:
                resp = await client.get(search_url)
                resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []

            for result in soup.select(".result")[:limit]:
                title_el = result.select_one(".result__title a")
                snippet_el = result.select_one(".result__snippet")

                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                # DuckDuckGo redirect URL — extract actual URL
                if "uddg=" in href:
                    from urllib.parse import urlparse, parse_qs
                    parsed = urlparse(href)
                    actual_url = parse_qs(parsed.query).get("uddg", [href])[0]
                else:
                    actual_url = href

                results.append({
                    "title": title,
                    "url": actual_url,
                    "snippet": snippet,
                })

            if not results:
                return ToolResult(
                    status=ToolStatus.SUCCESS,
                    output="Tidak ada hasil ditemukan untuk query ini.",
                )

            output: Any = results

            # Auto-fetch first result kalau diminta
            if fetch_first and results:
                try:
                    html = await _fetch_url_simple(results[0]["url"])
                    content = _extract_text(html, 4000)
                    output = {
                        "search_results": results,
                        "first_result_content": {
                            "url": results[0]["url"],
                            "content": content,
                        },
                    }
                except Exception:
                    pass  # Tetap return search results saja

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output=output,
                metadata={"query": query, "count": len(results)},
            )

        except Exception as e:
            logger.exception(f"browser_search failed: {query}")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class BrowserScreenshotTool(BaseTool):
    """Screenshot halaman web (butuh Playwright)."""

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="browser_screenshot",
            description=(
                "Ambil screenshot halaman web dan simpan ke file. "
                "Berguna untuk debug tampilan atau dokumentasi."
            ),
            parameters=[
                ToolParameter(
                    name="url",
                    type="string",
                    description="URL yang ingin di-screenshot.",
                ),
                ToolParameter(
                    name="output_path",
                    type="string",
                    description="Path file output (default: screenshot.png di working directory).",
                    required=False,
                    default="screenshot.png",
                ),
                ToolParameter(
                    name="full_page",
                    type="boolean",
                    description="Screenshot full page (default: true).",
                    required=False,
                    default=True,
                ),
            ],
            permission=PermissionLevel.READ_WRITE,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        url: str = kwargs["url"]
        output_path: str = kwargs.get("output_path", "screenshot.png")
        full_page: bool = kwargs.get("full_page", True)

        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=30000)
                await page.screenshot(path=output_path, full_page=full_page)
                await browser.close()

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output={
                    "url": url,
                    "saved_to": output_path,
                    "message": f"Screenshot berhasil disimpan ke '{output_path}'.",
                },
            )

        except Exception as e:
            logger.exception(f"browser_screenshot failed: {url}")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))