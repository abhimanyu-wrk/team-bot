import logging
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urlunparse

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Article

LOG = logging.getLogger(__name__)
DEFAULT_REGISTRY = "config/sources.json"
TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid", "ocid", "ref", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}


def make_session():
    session = requests.Session()
    retry = Retry(total=3, connect=3, read=3, backoff_factor=0.5,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset({"GET"}), respect_retry_after_header=True)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 (compatible; DailyTechIntelligence/2.0; tech briefing bot)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def canonical_url(url):
    try:
        p = urlparse(url.strip())
        host = p.netloc.lower().removeprefix("www.")
        path = re.sub(r"/+", "/", p.path).rstrip("/")
        query = "&".join(x for x in p.query.split("&") if x and x.split("=", 1)[0].lower() not in TRACKING)
        return urlunparse((p.scheme.lower() or "https", host, path, "", query, ""))
    except Exception:
        return url.strip()


def _date(entry):
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime(*parsed[:6], tzinfo=timezone.utc)
    for key in ("published", "updated", "date"):
        value = entry.get(key)
        if value:
            try:
                dt = parsedate_to_datetime(value)
                return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
    return datetime.now(timezone.utc)


def _updated_date(entry, published):
    parsed = entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc)
    value = entry.get("updated")
    if value:
        try:
            date = parsedate_to_datetime(value)
            return date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            pass
    return published


def _plain(value):
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", value).strip()


def _article(entry, source, source_url=""):
    link = entry.get("link") or entry.get("url")
    title = _plain(entry.get("title"))
    if not link or not title:
        return None
    description = _plain(entry.get("summary") or entry.get("description") or entry.get("content", [{}])[0].get("value", ""))
    publisher = source.get("name", "Unknown source")
    origin = entry.get("source", {})
    if isinstance(origin, dict) and origin.get("title") and source.get("category") == "Aggregator":
        publisher = _plain(origin["title"])
    domain = urlparse(link).netloc.lower().removeprefix("www.")
    metrics = {}
    for field, keys in {"points": ("points", "score"), "comments": ("num_comments", "comments")}.items():
        for key in keys:
            try:
                value = entry.get(key)
                if value is not None:
                    metrics[field] = max(metrics.get(field, 0.0), float(value))
            except (TypeError, ValueError):
                pass
    popularity = max(metrics.values(), default=0.0)
    published = _date(entry)
    first_pub = published
    year_match = re.search(r"\((\d{4})\)", title)
    if year_match:
        try:
            y = int(year_match.group(1))
            if y < published.year:
                first_pub = datetime(y, 1, 1, tzinfo=timezone.utc)
        except ValueError:
            pass
    url_date_match = re.search(r"/(\d{4})[/-](\d{1,2})[/-](\d{1,2})", link)
    if url_date_match:
        try:
            y, m, d = int(url_date_match.group(1)), int(url_date_match.group(2)), int(url_date_match.group(3))
            candidate_date = datetime(y, m, d, tzinfo=timezone.utc)
            if candidate_date < published:
                first_pub = candidate_date
        except ValueError:
            pass
    story_ids = tuple(dict.fromkeys(re.findall(r"CVE-\d{4}-\d+", f"{title} {description}", re.I)))
    is_show = bool(re.match(r"^\s*show\s+hn\b", title, re.I))
    return Article(title=title, url=link, original_url=link, source=publisher, published=published,
                   summary=description[:5000], popularity=popularity, popularity_metrics=metrics,
                   sources=(publisher,), source_domains=(domain,) if domain else (),
                   author=_plain(entry.get("author", "")), source_category=source.get("category", ""),
                   first_published_at=first_pub, latest_update_at=_updated_date(entry, published), story_ids=story_ids,
                   is_show_hn=is_show)


def fetch_feed(session, source):
    response = session.get(source["url"], timeout=20)
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Malformed feed: {parsed.bozo_exception}")
    return [item for entry in parsed.entries if (item := _article(entry, source))]


def _fetch_hn(session, source, lookback_hours):
    cutoff = int((datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).timestamp())
    url = source["url"] + f"&numericFilters=created_at_i>{cutoff},points>2"
    response = session.get(url, timeout=20)
    response.raise_for_status()
    result = []
    for hit in response.json().get("hits", []):
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        created = hit.get("created_at")
        try:
            published = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            published = datetime.now(timezone.utc)
        points = float(hit.get("points") or 0)
        comments = float(hit.get("num_comments") or 0)
        domain = urlparse(link).netloc.lower().removeprefix("www.")
        first_pub = published
        year_match = re.search(r"\((\d{4})\)", hit.get("title", ""))
        if year_match:
            try:
                y = int(year_match.group(1))
                if y < published.year:
                    first_pub = datetime(y, 1, 1, tzinfo=timezone.utc)
            except ValueError:
                pass
        url_date_match = re.search(r"/(\d{4})[/-](\d{1,2})[/-](\d{1,2})", link)
        if url_date_match:
            try:
                y, m, d = int(url_date_match.group(1)), int(url_date_match.group(2)), int(url_date_match.group(3))
                candidate_date = datetime(y, m, d, tzinfo=timezone.utc)
                if candidate_date < published:
                    first_pub = candidate_date
            except ValueError:
                pass
        is_show = bool(re.match(r"^\s*show\s+hn\b", hit.get("title", ""), re.I))
        result.append(Article(hit.get("title", ""), link, domain or "Unknown publisher", published,
                              f"Discussed on Hacker News. {hit.get('story_text') or ''}".strip(), points,
                              sources=(domain or "Unknown publisher", "Hacker News"),
                              popularity_metrics={"points": points, "comments": comments},
                              source_domains=(domain,) if domain else (), source_category="Community",
                              first_published_at=first_pub, latest_update_at=published,
                              is_show_hn=is_show))
    return result


def _fetch_github(session, source, lookback_hours):
    since = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).date().isoformat()
    response = session.get(source["url"], params={"q": f"created:>{since} stars:>25", "sort": "stars", "order": "desc", "per_page": 50}, timeout=20)
    response.raise_for_status()
    result = []
    for repo in response.json().get("items", []):
        created = repo.get("created_at")
        pushed = repo.get("pushed_at") or created
        try:
            published = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc)
            updated = datetime.fromisoformat(pushed.replace("Z", "+00:00")).astimezone(timezone.utc)
        except (TypeError, ValueError):
            published = updated = datetime.now(timezone.utc)
        stars = float(repo.get("stargazers_count") or 0)
        url = repo.get("html_url", "")
        result.append(Article(f"Open-source project: {repo.get('full_name', '')}", url, "GitHub Trending",
                              published, repo.get("description") or "", stars,
                              popularity_metrics={"stars": stars},
                              source_domains=(urlparse(url).netloc.lower().removeprefix("www."),), source_category="Open Source",
                              first_published_at=published, latest_update_at=updated))
    return result


def _fetch_github_releases(session, source, lookback_hours):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    articles = []
    for repository in source.get("repositories", []):
        try:
            response = session.get(f"https://api.github.com/repos/{repository}/releases/latest", timeout=15)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            release = response.json()
            published = datetime.fromisoformat((release.get("published_at") or release.get("created_at")).replace("Z", "+00:00")).astimezone(timezone.utc)
            if published < cutoff or release.get("draft") or release.get("prerelease"):
                continue
            url = release.get("html_url", "")
            description = _plain(release.get("body") or "")
            articles.append(Article(f"{repository} releases {release.get('tag_name', 'a new version')}: {release.get('name') or ''}".strip(),
                                    url, source["name"], published, description[:5000],
                                    source_domains=("github.com",), source_category=source.get("category", "Open Source")))
        except Exception as exc:
            LOG.info("Latest release unavailable for %s (%s)", repository, type(exc).__name__)
    return articles


def _nested(value, path):
    for part in path.split(".") if path else []:
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _fetch_json_api(session, source):
    headers = dict(source.get("headers", {}))
    token_env = source.get("token_env")
    if token_env and os.getenv(token_env):
        headers[source.get("token_header", "Authorization")] = source.get("token_prefix", "Bearer ") + os.getenv(token_env)
    response = session.get(source["url"], params=source.get("params", {}), headers=headers, timeout=20)
    response.raise_for_status()
    payload = response.json()
    rows = _nested(payload, source.get("items_path", "items")) or []
    fields = source.get("fields", {})
    articles = []
    for row in rows:
        title = _nested(row, fields.get("title", "title"))
        url = _nested(row, fields.get("url", "url"))
        if not title or not url:
            continue
        raw_date = _nested(row, fields.get("published", "published"))
        try:
            published = datetime.fromisoformat(str(raw_date).replace("Z", "+00:00")).astimezone(timezone.utc)
        except (ValueError, TypeError):
            published = datetime.now(timezone.utc)
        summary = _plain(str(_nested(row, fields.get("summary", "summary")) or ""))
        metrics = {}
        for name, path in source.get("metrics", {}).items():
            try:
                metrics[name] = float(_nested(row, path) or 0)
            except (TypeError, ValueError):
                continue
        domain = urlparse(str(url)).netloc.lower().removeprefix("www.")
        articles.append(Article(str(title), str(url), source["name"], published, summary,
                                max(metrics.values(), default=0), popularity_metrics=metrics,
                                source_domains=(domain,) if domain else (), source_category=source.get("category", "")))
    return articles


def _fetch_reddit(session, registry, lookback_hours):
    client_id = __import__("os").getenv("REDDIT_CLIENT_ID")
    client_secret = __import__("os").getenv("REDDIT_CLIENT_SECRET")
    user_agent = __import__("os").getenv("REDDIT_USER_AGENT", "DailyTechIntelligence/2.0")
    if not client_id or not client_secret:
        LOG.info("Reddit discovery skipped: optional API credentials are not configured")
        return []
    auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
    token_response = session.post("https://www.reddit.com/api/v1/access_token", auth=auth,
                                  data={"grant_type": "client_credentials"},
                                  headers={"User-Agent": user_agent}, timeout=20)
    token_response.raise_for_status()
    token = token_response.json()["access_token"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    result = []
    for subreddit in registry.get("reddit_subreddits", []):
        try:
            response = session.get(f"https://oauth.reddit.com/r/{subreddit}/new", params={"limit": 50},
                                   headers={"Authorization": f"Bearer {token}", "User-Agent": user_agent}, timeout=20)
            response.raise_for_status()
            for child in response.json().get("data", {}).get("children", []):
                data = child.get("data", {})
                published = datetime.fromtimestamp(float(data.get("created_utc", 0)), timezone.utc)
                if published < cutoff or data.get("stickied") or data.get("is_self") and len(data.get("selftext", "")) < 80:
                    continue
                url = data.get("url") or f"https://www.reddit.com{data.get('permalink', '')}"
                score = float(data.get("score") or 0)
                comments = float(data.get("num_comments") or 0)
                domain = urlparse(url).netloc.lower().removeprefix("www.")
                publisher = "Reddit" if domain.endswith("reddit.com") else (domain or "Unknown publisher")
                result.append(Article(data.get("title", ""), url, publisher, published,
                                      _plain(data.get("selftext", ""))[:2500], score,
                                      sources=(publisher, f"Reddit r/{subreddit}"),
                                      popularity_metrics={"score": score, "comments": comments},
                                      source_domains=(domain,) if domain else (), source_category="Community"))
        except Exception as exc:
            LOG.warning("Reddit r/%s failed: %s", subreddit, exc)
    return result


def fetch_all(session=None, registry=None, lookback_hours=36):
    session = session or make_session()
    if registry is None:
        from .config import load_json
        registry = load_json(DEFAULT_REGISTRY)
    articles = []
    for source in registry.get("sources", []):
        try:
            kind = source.get("type", "rss")
            if kind == "rss" or kind == "arxiv":
                found = fetch_feed(session, source)
            elif kind == "hackernews":
                found = _fetch_hn(session, source, lookback_hours)
            elif kind == "github_trending":
                found = _fetch_github(session, source, lookback_hours)
            elif kind == "github_releases":
                found = _fetch_github_releases(session, source, lookback_hours)
            elif kind == "json_api":
                found = _fetch_json_api(session, source)
            else:
                LOG.warning("Unsupported source type %r for %s", kind, source.get("name"))
                continue
            LOG.info("Fetched %d items from %s", len(found), source.get("name"))
            articles.extend(found)
        except Exception as exc:
            LOG.warning("Source %s failed: %s", source.get("name", "unknown"), exc)
    try:
        articles.extend(_fetch_reddit(session, registry, lookback_hours))
    except Exception as exc:
        LOG.warning("Reddit discovery unavailable: %s", exc)
    return articles
