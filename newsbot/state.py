import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .sources import canonical_url
from .ranking import article_fingerprint

LOG = logging.getLogger(__name__)


def load_sent(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        LOG.warning("Could not read sent state: %s", exc)
        return {}


def filter_sent(articles, sent, now=None, retention_days=14):
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - retention_days * 86400
    sent = {url: ts for url, ts in sent.items() if isinstance(ts, (int, float)) and ts >= cutoff}
    return [a for a in articles if canonical_url(a.url) not in sent and "story:" + article_fingerprint(a) not in sent], sent


def save_sent(path, sent, articles, now=None):
    now = now or datetime.now(timezone.utc)
    sent = dict(sent)
    for article in articles:
        sent[canonical_url(article.url)] = now.timestamp()
        fingerprint = article_fingerprint(article)
        if fingerprint:
            sent["story:" + fingerprint] = now.timestamp()
    cutoff = now.timestamp() - 14 * 86400
    sent = {url: ts for url, ts in sent.items() if ts >= cutoff}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(sent, indent=2), encoding="utf-8")
    tmp.replace(target)
    return sent


def add_trend_velocity(articles, path, now=None, retention_days=14):
    """Compare real consecutive snapshots; do not infer growth from total counts."""
    now = now or datetime.now(timezone.utc)
    target = Path(path)
    try:
        history = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(history, dict):
            history = {}
    except (OSError, ValueError):
        history = {}
    cutoff = now.timestamp() - retention_days * 86400
    history = {url: value for url, value in history.items() if isinstance(value, dict) and value.get("observed_at", 0) >= cutoff}
    current = {}
    for article in articles:
        url = canonical_url(article.url)
        if not url or not article.popularity_metrics:
            continue
        raw_metrics = {key: value for key, value in article.popularity_metrics.items() if key != "velocity_per_hour"}
        previous = history.get(url, {})
        old_counts = previous.get("metrics", {})
        elapsed = (now.timestamp() - float(previous.get("observed_at", 0))) / 3600 if previous.get("observed_at") else 0
        velocities = []
        if 0.5 <= elapsed <= retention_days * 24:
            for metric, value in raw_metrics.items():
                try:
                    delta = float(value) - float(old_counts.get(metric, value))
                    if delta >= 0:
                        velocities.append(delta / elapsed)
                except (TypeError, ValueError):
                    continue
        if velocities:
            article.popularity_metrics["velocity_per_hour"] = max(velocities)
        combined_metrics = dict(current.get(url, {}).get("metrics", {}))
        for key, value in raw_metrics.items():
            combined_metrics[key] = max(float(combined_metrics.get(key, 0)), float(value))
        current[url] = {"observed_at": now.timestamp(), "metrics": combined_metrics}
    history.update(current)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(history, indent=2), encoding="utf-8")
    tmp.replace(target)
