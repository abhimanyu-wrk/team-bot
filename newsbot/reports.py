import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .ranking import _same_story, _tokens


def evaluate_quality(article, already_selected, minimum_score=48):
    reasons = []
    if getattr(article, "promotional_score", 0.0) >= 50.0:
        reasons.append(f"Promotional content score {article.promotional_score:.1f}/100 exceeds acceptable threshold.")
    if any("Promotional content" in r for r in article.rejection_reasons):
        reasons.append("Promotional content detected by editorial policy.")
    if any("Historical article" in r or "archival content" in r for r in article.rejection_reasons):
        reasons.append("Historical article published prior to current intelligence window.")
    if getattr(article, "editorial_category", "") == "Breaking Developments" and getattr(article, "breakthrough_level", "") == "Routine release or announcement":
        reasons.append("Routine release or announcement does not qualify for Breaking Developments.")
    if article.category in {"Other", "Industry Technology"}:
        reasons.append("The available text does not establish a specific technology development category.")
    if not article.url.startswith("https://"):
        reasons.append("Original article URL is not HTTPS.")
    if article.url_valid is False:
        reasons.append("Original article returned an invalid or unavailable response.")
    text = article.content or article.summary
    if len(text.strip()) < 90:
        reasons.append("Too little article text is available to support a factual summary.")
    title_terms = _tokens(article.title)
    if len(title_terms) >= 3 and len(title_terms & _tokens(text)) < 2:
        reasons.append("Available article text does not substantively support the headline.")
    if article.score < minimum_score:
        reasons.append(f"Score {article.score:.1f} is below the editorial threshold of {minimum_score}.")
    for selected in already_selected:
        if getattr(article, "event_id", "") and getattr(article, "event_id", "") == getattr(selected, "event_id", ""):
            reasons.append("Duplicates an event already selected for this digest.")
            break
        if _same_story(article, selected):
            reasons.append("Duplicates a story already selected for this digest.")
            break
    return list(dict.fromkeys(reasons))


def select_balanced_articles(candidates, max_articles=5, min_score=48, source_cap=2):
    """
    Selects up to `max_articles` per category (Breaking Developments and Technical Intelligence)
    without forcing a fixed count or lowering quality standards.
    """
    selected = []
    rejected = []
    source_counts = {}

    def try_add(article):
        if article in selected:
            return False
        quality_reasons = evaluate_quality(article, selected, min_score)
        if quality_reasons:
            article.rejection_reasons.extend(quality_reasons)
            rejected.append(article)
            return False
        reporting_sources = [name for name in article.sources if not any(tag in name.lower() for tag in ("hacker news", "reddit", "github trending", "google news"))]
        reporting_sources = reporting_sources or [article.source]
        credit_source = min(reporting_sources, key=lambda name: (source_counts.get(name, 0), name != article.source, name))
        if source_counts.get(credit_source, 0) >= source_cap:
            article.rejection_reasons.append(f"Skipped by source-diversity cap ({source_cap} stories credited to {credit_source}).")
            rejected.append(article)
            return False
        selected.append(article)
        source_counts[credit_source] = source_counts.get(credit_source, 0) + 1
        article.selection_reasons.append(f"Source-diversity credit assigned to {credit_source}.")
        return True

    breaking_candidates = [a for a in candidates if getattr(a, "editorial_category", "") == "Breaking Developments"]
    technical_candidates = [a for a in candidates if getattr(a, "editorial_category", "") == "Technical Intelligence"]

    # Select up to max_articles for Breaking Developments
    breaking_count = 0
    for article in breaking_candidates:
        if breaking_count >= max_articles:
            break
        if try_add(article):
            breaking_count += 1

    # Select up to max_articles for Technical Intelligence
    technical_count = 0
    for article in technical_candidates:
        if technical_count >= max_articles:
            break
        if try_add(article):
            technical_count += 1

    return selected, rejected


def _article_record(article, rank=None, status="candidate"):
    return {
        "rank": rank,
        "status": status,
        "headline": article.title,
        "editorial_category": getattr(article, "editorial_category", "Breaking Developments"),
        "event_id": getattr(article, "event_id", ""),
        "breakthrough_level": getattr(article, "breakthrough_level", "Significant development"),
        "original_publisher": article.source,
        "author": article.author or None,
        "original_url": article.original_url or article.url,
        "published_at": article.published.astimezone(timezone.utc).isoformat(),
        "first_published_at": (article.first_published_at or article.published).astimezone(timezone.utc).isoformat(),
        "latest_update_at": (article.latest_update_at or article.published).astimezone(timezone.utc).isoformat(),
        "category": article.category,
        "source_category": article.source_category,
        "score": article.score,
        "promotional_score": getattr(article, "promotional_score", 0.0),
        "promotional_reasons": getattr(article, "promotional_reasons", []),
        "score_breakdown": article.score_breakdown,
        "sources": list(article.sources),
        "supporting_details": getattr(article, "supporting_details", []),
        "analysis": getattr(article, "analysis", {}),
        "independent_coverage_count": len(article.source_domains),
        "engagement_metrics": article.popularity_metrics,
        "content_status": article.content_status,
        "summary_evidence": (article.content or article.summary)[:1200],
        "why_selected": article.selection_reasons,
        "why_rejected": article.rejection_reasons,
    }


def write_daily_report(report_dir, now, candidates, selected, rejected, discovered_count):
    target = Path(report_dir)
    target.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d-%H%M%S")
    report = {
        "generated_at": now.isoformat(),
        "discovered_articles": discovered_count,
        "top_candidates": [_article_record(a, i + 1, "selected" if a in selected else "candidate") for i, a in enumerate(candidates[:20])],
        "selected": [_article_record(a, i + 1, "selected") for i, a in enumerate(selected)],
        "rejected": [_article_record(a, None, "rejected") for a in rejected],
    }
    json_path = target / f"tech-intelligence-{stamp}.json"
    md_path = target / f"tech-intelligence-{stamp}.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# Daily Tech Intelligence diagnostics — {now.isoformat()}", "", f"Discovered articles: {discovered_count}", "", "## Top 20 candidates", "", "| Rank | Headline | Section | Category | Quality Score | Promo Score | Sources |", "|---:|---|---|---|---:|---:|---|"]
    for index, article in enumerate(candidates[:20], 1):
        headline = article.title.replace("|", "\\|").replace("\n", " ")
        section = getattr(article, "editorial_category", "Breaking Developments")
        promo_score = getattr(article, "promotional_score", 0.0)
        lines.append(f"| {index} | [{headline}]({article.original_url or article.url}) | {section} | {article.category} | {article.score:.1f} | {promo_score:.1f} | {', '.join(article.sources)} |")
        lines.append(f"  - Score breakdown: `{json.dumps(article.score_breakdown, ensure_ascii=False)}`")
        lines.append(f"  - Why selected: {'; '.join(article.selection_reasons) or 'Candidate under review'}")
        lines.append(f"  - Rejection concerns: {'; '.join(article.rejection_reasons) or 'None identified'}")
    lines += ["", "## Rejected articles", ""]
    for article in rejected:
        section = getattr(article, "editorial_category", "Breaking Developments")
        promo_score = getattr(article, "promotional_score", 0.0)
        lines.append(f"- **{article.title}** — Source: {article.source}; Section: {section}; Category: {article.category}; Quality score: {article.score:.1f}/100; Promo score: {promo_score:.1f}/100. Rejection reasons: {'; '.join(article.rejection_reasons)}")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path, report


def update_editorial_history(path, now, selected, rejected, limit=2000):
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        history = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(history, list):
            history = []
    except (OSError, ValueError):
        history = []
    for status, items in (("selected", selected), ("rejected", rejected)):
        for article in items:
            history.append({
                "recorded_at": now.isoformat(), "status": status, "url": article.original_url or article.url,
                "headline": article.title,
                "editorial_category": getattr(article, "editorial_category", "Breaking Developments"),
                "category": article.category, "source": article.source,
                "score": article.score,
                "promotional_score": getattr(article, "promotional_score", 0.0),
                "score_breakdown": article.score_breakdown,
                "reasons": article.selection_reasons if status == "selected" else article.rejection_reasons,
            })
    file_path.write_text(json.dumps(history[-limit:], indent=2, ensure_ascii=False), encoding="utf-8")
