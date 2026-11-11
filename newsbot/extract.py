import logging
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

LOG = logging.getLogger(__name__)

try:
    import trafilatura
except ImportError:  # RSS descriptions remain the deterministic fallback.
    trafilatura = None


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0
        self.in_article = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style", "nav", "footer", "header", "aside", "noscript", "form"}:
            self.skip += 1
        if tag in {"article", "main"}:
            self.in_article = True
        if tag in {"p", "h1", "h2", "h3", "li"} and not self.skip:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "nav", "footer", "header", "aside", "noscript", "form"} and self.skip:
            self.skip -= 1
        if tag in {"p", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")
        if tag in {"article", "main"}:
            self.in_article = False

    def handle_data(self, data):
        if not self.skip and (self.in_article or len(self.parts) < 40):
            text = re.sub(r"\s+", " ", data).strip()
            if text:
                self.parts.append(text)


def _fallback_extract(html):
    parser = _TextExtractor()
    parser.feed(html)
    return re.sub(r"\n\s*\n+", "\n", " ".join(parser.parts)).strip()


def enrich_articles(session, articles, limit=35):
    """Fetch a bounded number of public article pages; keep RSS metadata on blocks/paywalls."""
    fetch_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 (compatible; DailyTechIntelligence/2.0)",
    }
    for article in articles[:limit]:
        parsed = urlparse(article.url)
        if parsed.scheme != "https" or not parsed.netloc:
            article.content_status = "invalid_url"
            article.url_valid = False
            continue
        try:
            response = session.get(article.url, timeout=15, allow_redirects=True, headers=fetch_headers, stream=True)
            article.url_valid = response.status_code < 400 or response.status_code in (401, 403, 429)
            if response.status_code in (401, 403, 429):
                article.content_status = "blocked_or_paywalled"
                response.close()
                continue
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").lower()
            if "html" not in content_type and "xhtml" not in content_type:
                article.content_status = "not_html"
                response.close()
                continue
            max_bytes = 2_000_000
            raw = bytearray()
            for chunk in response.iter_content(65536):
                if not chunk:
                    continue
                raw.extend(chunk)
                if len(raw) >= max_bytes:
                    break
            response.close()
            html = bytes(raw).decode(response.encoding or "utf-8", errors="replace")
            extracted = trafilatura.extract(html, include_comments=False, include_tables=False, favor_precision=True) if trafilatura else ""
            if not extracted:
                extracted = _fallback_extract(html)
            article.content = re.sub(r"\s+", " ", extracted).strip()[:16000]
            article.story_ids = tuple(dict.fromkeys((*article.story_ids, *re.findall(r"CVE-\d{4}-\d+", article.content[:5000], re.I))))
            final_url = response.url
            if "news.google.com" in final_url:
                canonical_match = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', html, re.I)
                if canonical_match and not canonical_match.group(1).startswith("https://news.google.com"):
                    final_url = canonical_match.group(1)
            article.original_url = final_url
            article.url = final_url

            pub_match = re.search(r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|pubdate|date|sailthru\.date)["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
            if not pub_match:
                pub_match = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:article:published_time|pubdate|date|sailthru\.date)["\']', html, re.I)
            if pub_match:
                try:
                    raw_dt = pub_match.group(1).replace("Z", "+00:00")
                    dt = datetime.fromisoformat(raw_dt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if article.first_published_at is None or dt < article.first_published_at:
                        article.first_published_at = dt
                except Exception:
                    pass

            article.content_status = "extracted" if len(article.content) > 250 else "thin_or_blocked"
        except requests.HTTPError as exc:
            host = parsed.netloc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            article.content_status = "http_error" if status is None else f"http_error_{status}"
            article.url_valid = status in (401, 403, 429) if status is not None else None
            LOG.info("Article content unavailable from %s (HTTP %s)", host, status or "error")
        except Exception as exc:
            host = parsed.netloc
            article.content_status = "fetch_failed"
            article.url_valid = None
            LOG.info("Article content unavailable from %s (%s)", host, type(exc).__name__)
    for article in articles[limit:]:
        article.content_status = "not_fetched_limit"
    return articles
