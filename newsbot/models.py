from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Article:
    title: str
    url: str
    source: str
    published: datetime
    summary: str = ""
    popularity: float = 0.0
    sources: tuple[str, ...] = ()
    score: float = 0.0
    content: str = ""
    category: str = "Other"
    popularity_metrics: dict[str, float] = field(default_factory=dict)
    score_breakdown: dict[str, float] = field(default_factory=dict)
    rejection_reasons: list[str] = field(default_factory=list)
    selection_reasons: list[str] = field(default_factory=list)
    source_domains: tuple[str, ...] = ()
    original_url: str = ""
    content_status: str = "not_fetched"
    url_valid: bool | None = None
    author: str = ""
    source_category: str = ""
    first_published_at: datetime | None = None
    latest_update_at: datetime | None = None
    story_ids: tuple[str, ...] = ()
    editorial_category: str = "Breaking Developments"
    promotional_score: float = 0.0
    promotional_reasons: list[str] = field(default_factory=list)
    is_show_hn: bool = False
    event_id: str = ""
    breakthrough_level: str = "Significant development"
    supporting_details: list[str] = field(default_factory=list)
    analysis: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.sources:
            self.sources = (self.source,)
        if not self.original_url:
            self.original_url = self.url
