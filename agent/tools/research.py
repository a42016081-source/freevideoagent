"""Free research tools (no API keys): web search, page fetch, summary, Google Trends RSS."""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

from agent.providers.llm import complete

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (compatible; FreeVideoAgent/1.0)"}


def is_public_url(url: str) -> bool:
    """SSRF guard: only http(s) URLs whose host resolves to public IP addresses."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            return False
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                    or ip.is_multicast or ip.is_unspecified):
                return False
        return True
    except Exception:  # noqa: BLE001
        return False


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> str:
    try:
        import trafilatura  # optional, better extraction
        text = trafilatura.extract(html) or ""
        if text:
            return text
    except Exception:  # noqa: BLE001
        pass
    p = _TextExtractor()
    p.feed(html)
    return " ".join(p.parts)


def fetch_page(url: str, timeout: int = 20, max_chars: int = 8000) -> str:
    """Fetch readable text from a public web page ('' if blocked or failing)."""
    try:
        for _ in range(4):                       # follow redirects manually, re-checking every hop
            if not is_public_url(url):
                return ""
            r = requests.get(url, timeout=timeout, headers=UA, allow_redirects=False, stream=True)
            if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, r.headers.get("Location", ""))
                continue
            r.raise_for_status()
            body = r.raw.read(1_500_000, decode_content=True)
            return html_to_text(body.decode(r.encoding or "utf-8", errors="ignore"))[:max_chars]
    except Exception as e:  # noqa: BLE001
        log.info("fetch_page(%s) failed: %s", url, e)
    return ""


def web_search(query: str, n: int = 5) -> list[dict]:
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS  # old package name
        with DDGS() as d:
            return list(d.text(query, max_results=n))
    except Exception as e:  # noqa: BLE001
        log.warning("web search failed: %s", e)
        return []


def summarize(text: str, max_words: int = 120) -> str:
    try:
        return complete("Summarize in 4-6 short bullet points. Same language as the user's topic. "
                        "Be factual; do not invent anything that is not in the text.", text[:6000], timeout=45)
    except Exception:  # noqa: BLE001
        words = text.split()
        return " ".join(words[:max_words]) + ("..." if len(words) > max_words else "")


def research_topic(topic: str) -> dict:
    results = web_search(topic, 5)
    combined = ""
    for r in results[:3]:
        body = fetch_page(r.get("href", "")) or r.get("body", "")
        combined += f"\n\n# {r.get('title', '')}\n{body[:2000]}"
    summary = summarize(combined) if combined.strip() else "No search results (search provider unavailable)."
    return {"topic": topic, "summary": summary, "sources": [r.get("href", "") for r in results[:3]]}


def google_trends(geo: str = "IN", limit: int = 10) -> list[str]:
    """Daily trending searches from Google's public Trends RSS feed."""
    try:
        r = requests.get("https://trends.google.com/trending/rss", params={"geo": geo.upper()},
                         headers=UA, timeout=20)
        r.raise_for_status()
        root = ET.fromstring(r.content)
        titles = [(i.findtext("title") or "").strip() for i in root.iter("item")]
        return [t for t in titles if t][:limit]
    except Exception as e:  # noqa: BLE001
        log.warning("Google Trends failed: %s", e)
        return []


def strip_urls(text: str) -> str:
    return re.sub(r"https?://\S+", "", text)
