import json
import logging
from datetime import datetime, timezone

from newsbot.config import Config, load_json
from newsbot.extract import enrich_articles
from newsbot.ranking import rank_articles
from newsbot.reports import evaluate_quality, select_balanced_articles, update_editorial_history, write_daily_report
from newsbot.sources import fetch_all, make_session
from newsbot.state import add_trend_velocity, filter_sent, load_sent, save_sent
from newsbot.teams import build_card, post_card

LOG = logging.getLogger(__name__)
HARD_REJECTION_PREFIXES = (
    "Title matches exclusion", "Available text matches exclusion", "Trending repository description",
    "Manually rejected", "Domain ", "Source ", "Promotional content", "Show HN submission",
    "Archival or historical", "Historical article", "Product landing page"
)


def _hard_rejected(article):
    return any(reason.startswith(HARD_REJECTION_PREFIXES) for reason in article.rejection_reasons)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    registry = load_json(cfg.sources_path)
    editorial = load_json(cfg.editorial_path)
    lookback_hours = int(editorial.get("lookback_hours", cfg.lookback_hours))
    # The explicit environment value remains the operator override.
    if "NEWS_LOOKBACK_HOURS" in __import__("os").environ:
        lookback_hours = cfg.lookback_hours
    session = make_session()
    discovered = fetch_all(session, registry, lookback_hours)
    now = datetime.now(timezone.utc)
    add_trend_velocity(discovered, cfg.trend_path, now)
    ranked = rank_articles(discovered, now, lookback_hours, registry, editorial)

    # Fetch article text only for the strongest candidates and only from public HTTPS pages.
    for article in ranked:
        if _hard_rejected(article):
            continue
        if article.rejection_reasons and all("below configured minimum" in reason for reason in article.rejection_reasons):
            continue
        # Retain source diversity in the extraction pass; ranking already clusters same-event reports.
        article.content_status = "queued"
    enrich_articles(session, [a for a in ranked if a.content_status == "queued"], limit=cfg.candidate_limit + 15)
    ranked = rank_articles(ranked, now, lookback_hours, registry, editorial)

    sent_state = load_sent(cfg.state_path)
    fresh, sent_state = filter_sent(ranked, sent_state, now)
    fresh_keys = {id(a) for a in fresh}
    rejected = []
    for article in ranked:
        if id(article) not in fresh_keys:
            if not article.rejection_reasons:
                article.rejection_reasons.append("This story was delivered in a recent digest.")
            rejected.append(article)
        elif article.rejection_reasons:
            rejected.append(article)

    candidates = [a for a in fresh if not a.rejection_reasons]
    min_score = float(editorial.get("minimum_quality_score", 48))
    source_cap = max(1, int(editorial.get("max_articles_per_source", 2)))
    selected, balanced_rejected = select_balanced_articles(candidates, cfg.max_articles, min_score, source_cap)
    for a in balanced_rejected:
        if a not in rejected:
            rejected.append(a)

    top20 = fresh[:cfg.candidate_limit]
    selected_ids = {id(article) for article in selected}
    rejected_ids = {id(article) for article in rejected}
    for article in top20:
        if id(article) not in selected_ids and not article.rejection_reasons:
            article.rejection_reasons.append("Ranked below the final selection cutoff or remaining slots were filled by higher-scoring stories.")
            if id(article) not in rejected_ids:
                rejected.append(article)
                rejected_ids.add(id(article))
    for article in selected:
        if not article.selection_reasons:
            article.selection_reasons.append(f"Passed the editorial quality gate with a score of {article.score:.1f}/100 and ranked in the final selection.")

    for article in rejected:
        promo = getattr(article, "promotional_score", 0.0)
        sec = getattr(article, "editorial_category", "Unknown")
        eid = getattr(article, "event_id", "unknown")
        unw = (article.score_breakdown or {}).get("unweighted_factors", {})
        pub_date = article.published.strftime("%Y-%m-%d %H:%M") if article.published else "N/A"
        LOG.info(
            "REJECTED: '%s' | Source: %s | Published: %s | Section: %s | Category: %s | Event: %s | Rel: %.1f | Sig: %.1f | Nov: %.1f | Promo: %.1f | Score: %.1f | Reasons: %s",
            article.title, article.source, pub_date, sec, article.category, eid,
            unw.get("audience_relevance", unw.get("relevance", 0.0)),
            unw.get("technical_significance", unw.get("significance", 0.0)),
            unw.get("novelty_and_originality", unw.get("originality", 0.0)),
            promo, article.score, "; ".join(article.rejection_reasons)
        )
    for article in selected:
        sec = getattr(article, "editorial_category", "Unknown")
        eid = getattr(article, "event_id", "unknown")
        unw = (article.score_breakdown or {}).get("unweighted_factors", {})
        pub_date = article.published.strftime("%Y-%m-%d %H:%M") if article.published else "N/A"
        rating = (article.score_breakdown or {}).get("editorial_rating", "Notable")
        LOG.info(
            "SELECTED: '%s' | Source: %s | Published: %s | Section: %s | Category: %s | Event: %s | Rating: %s | Rel: %.1f | Sig: %.1f | Nov: %.1f | Score: %.1f | Reasons: %s",
            article.title, article.source, pub_date, sec, article.category, eid, rating,
            unw.get("audience_relevance", unw.get("relevance", 0.0)),
            unw.get("technical_significance", unw.get("significance", 0.0)),
            unw.get("novelty_and_originality", unw.get("originality", 0.0)),
            article.score, "; ".join(article.selection_reasons)
        )

    json_path, md_path, _ = write_daily_report(cfg.report_dir, now, top20, selected, rejected, len(discovered))
    update_editorial_history(cfg.history_path, now, selected, rejected)
    LOG.info("Discovered %d items; %d candidate rows; %d passed editorial review; reports: %s and %s",
             len(discovered), len(top20), len(selected), json_path, md_path)

    if cfg.diagnostic_only:
        LOG.info("Diagnostic-only run: no Teams message sent.")
        return 0
    if cfg.dry_run:
        print(json.dumps(build_card(selected, cfg.timezone, now), indent=2, ensure_ascii=False))
        LOG.info("Dry run: Teams and sent URL state unchanged.")
        return 0
    if not cfg.webhook_url:
        raise RuntimeError("TEAMS_WEBHOOK_URL is required unless DRY_RUN=true or DIAGNOSTIC_ONLY=true")

    post_card(session, cfg.webhook_url, selected, cfg.timezone)
    if selected:
        save_sent(cfg.state_path, sent_state, selected, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
