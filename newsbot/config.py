import json
import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip("\"'")
            if k and k not in os.environ:
                os.environ[k] = v
    except Exception:
        pass


@dataclass(frozen=True)
class Config:
    webhook_url: str = ""
    max_articles: int = 5
    candidate_limit: int = 20
    timezone: str = "Asia/Kolkata"
    lookback_hours: int = 36
    dry_run: bool = False
    diagnostic_only: bool = False
    state_path: str = ".state/sent.json"
    history_path: str = ".state/editorial-history.json"
    trend_path: str = ".state/trend-metrics.json"
    report_dir: str = ".reports"
    sources_path: str = "config/sources.json"
    editorial_path: str = "config/editorial.json"

    @classmethod
    def from_env(cls):
        _load_dotenv()
        return cls(
            webhook_url=os.getenv("TEAMS_WEBHOOK_URL", ""),
            max_articles=max(1, int(os.getenv("MAX_ARTICLES", "5"))),
            candidate_limit=max(5, int(os.getenv("CANDIDATE_LIMIT", "20"))),
            timezone=os.getenv("TIMEZONE", "Asia/Kolkata"),
            lookback_hours=max(1, int(os.getenv("NEWS_LOOKBACK_HOURS", "36"))),
            dry_run=os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes"},
            diagnostic_only=os.getenv("DIAGNOSTIC_ONLY", "false").lower() in {"1", "true", "yes"},
            state_path=os.getenv("STATE_PATH", ".state/sent.json"),
            history_path=os.getenv("HISTORY_PATH", ".state/editorial-history.json"),
            trend_path=os.getenv("TREND_PATH", ".state/trend-metrics.json"),
            report_dir=os.getenv("REPORT_DIR", ".reports"),
            sources_path=os.getenv("SOURCES_CONFIG", "config/sources.json"),
            editorial_path=os.getenv("EDITORIAL_CONFIG", "config/editorial.json"),
        )


def load_json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        if fallback is not None:
            return fallback
        raise
