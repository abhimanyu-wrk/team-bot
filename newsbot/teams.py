import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .analysis import (
    explain_action_or_implications,
    explain_breaking_why_it_matters,
    explain_engineering_takeaway,
    explain_how_it_works,
    explain_technical_why_it_matters,
    explain_who_should_care,
    extract_technical_what_was_built,
    extract_what_happened,
)
from .ranking import (
    classify_category,
    classify_editorial_category,
    explain_implications,
    explain_why_it_matters,
    summarize,
    summarize_technical_built,
    summarize_what_changed,
)
from .sources import canonical_url

LOG = logging.getLogger(__name__)


def _format_item_blocks(article, index, section, tz):
    if article.category == "Other":
        article.category = classify_category(article)
    published = article.published.astimezone(tz).strftime("%d %b %Y, %I:%M %p %Z")
    updated = article.latest_update_at or article.published
    if updated > article.published:
        published += f" (Updated {updated.astimezone(tz).strftime('%d %b, %I:%M %p %Z')})"
    source_text = ", ".join(article.sources)
    blocks = [
        {"type": "TextBlock", "text": f"{index}. {article.title}", "weight": "Bolder", "size": "Medium", "wrap": True},
        {"type": "TextBlock", "text": f"**{article.category}** | {source_text} | {published}", "isSubtle": True, "wrap": True, "spacing": "Small"},
    ]
    if section == "breaking":
        what_happened = (article.analysis or {}).get("what_happened") or extract_what_happened(article)
        why_matters = (article.analysis or {}).get("why_it_matters") or explain_breaking_why_it_matters(article)
        who_cares = (article.analysis or {}).get("who_should_care") or explain_who_should_care(article)
        action_imp = (article.analysis or {}).get("action_or_implications") or explain_action_or_implications(article)
        blocks.extend([
            {"type": "TextBlock", "text": f"**What happened:**\n{what_happened}", "wrap": True, "spacing": "Small"},
            {"type": "TextBlock", "text": f"**Why it matters:**\n{why_matters}", "wrap": True, "spacing": "Small"},
            {"type": "TextBlock", "text": f"**Who should care:**\n{who_cares}", "wrap": True, "spacing": "Small"},
            {"type": "TextBlock", "text": f"**Action / implications:**\n{action_imp}", "wrap": True, "spacing": "Small"},
        ])
    else:
        what_built = (article.analysis or {}).get("what_was_built_or_discovered") or extract_technical_what_was_built(article)
        how_works = (article.analysis or {}).get("how_it_works") or explain_how_it_works(article)
        why_matters = (article.analysis or {}).get("why_it_matters") or explain_technical_why_it_matters(article)
        takeaway = (article.analysis or {}).get("engineering_takeaway") or explain_engineering_takeaway(article)
        blocks.extend([
            {"type": "TextBlock", "text": f"**What was built or discovered:**\n{what_built}", "wrap": True, "spacing": "Small"},
            {"type": "TextBlock", "text": f"**How it works:**\n{how_works}", "wrap": True, "spacing": "Small"},
            {"type": "TextBlock", "text": f"**Why it matters:**\n{why_matters}", "wrap": True, "spacing": "Small"},
            {"type": "TextBlock", "text": f"**Engineering takeaway:**\n{takeaway}", "wrap": True, "spacing": "Small"},
        ])

    rating = (article.score_breakdown or {}).get("editorial_rating")
    if not rating:
        if article.score >= 80.0:
            rating = "High"
        elif article.score >= 65.0:
            rating = "Medium"
        elif article.score >= 50.0:
            rating = "Notable"
        else:
            rating = "Low"
    clean_target_url = canonical_url(article.original_url or article.url)
    blocks.append(
        {"type": "TextBlock", "text": f"**Editorial assessment:** {rating} ({round(article.score)}/100) · [Original report]({clean_target_url})", "wrap": True, "spacing": "Small"}
    )
    return blocks


def build_card(articles, timezone_name="Asia/Kolkata", now=None):
    tz = ZoneInfo(timezone_name)
    now = now or datetime.now(timezone.utc)
    body = [
        {"type": "TextBlock", "text": "DAILY TECH INTELLIGENCE", "weight": "Bolder", "size": "Large", "wrap": True, "color": "Accent"},
        {"type": "TextBlock", "text": now.astimezone(tz).strftime("%A, %d %B %Y · %I:%M %p %Z"), "isSubtle": True, "spacing": "None", "wrap": True},
    ]
    if not articles:
        body.append({"type": "TextBlock", "text": "There were not enough verified, high-quality technology developments in the current window to prepare a reliable briefing.", "wrap": True, "spacing": "Medium"})
        card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4", "body": body, "msteams": {"width": "Full"}}
        return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}

    if 0 < len(articles) < 5:
        body.append({
            "type": "TextBlock",
            "text": f"Editorial note: {len(articles)} high-impact developments met today's strict quality and technical thresholds (target: up to 5). Promotional submissions, minor version bumps, and unverified claims were excluded.",
            "isSubtle": True,
            "wrap": True,
            "spacing": "Small",
        })

    breaking = []
    technical = []
    for article in articles:
        if not getattr(article, "editorial_category", None):
            article.editorial_category = classify_editorial_category(article)
        if article.editorial_category == "Technical Intelligence":
            technical.append(article)
        else:
            breaking.append(article)

    if breaking:
        body.append({
            "type": "TextBlock",
            "text": "BREAKING DEVELOPMENTS",
            "weight": "Bolder",
            "size": "Medium",
            "color": "Accent",
            "wrap": True,
            "spacing": "Medium",
        })
        for idx, article in enumerate(breaking, 1):
            body.extend(_format_item_blocks(article, idx, "breaking", tz))
            if idx < len(breaking):
                body.append({"type": "TextBlock", "text": "──────────", "isSubtle": True, "spacing": "Medium"})

    if technical:
        body.append({
            "type": "TextBlock",
            "text": "TECHNICAL INTELLIGENCE",
            "weight": "Bolder",
            "size": "Medium",
            "color": "Accent",
            "wrap": True,
            "spacing": "Large" if breaking else "Medium",
        })
        for idx, article in enumerate(technical, 1):
            body.extend(_format_item_blocks(article, idx, "technical", tz))
            if idx < len(technical):
                body.append({"type": "TextBlock", "text": "──────────", "isSubtle": True, "spacing": "Medium"})

    card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4", "body": body, "msteams": {"width": "Full"}}
    return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


def post_card(session, webhook_url, articles, timezone_name="Asia/Kolkata"):
    try:
        response = session.post(webhook_url, json=build_card(articles, timezone_name), timeout=25)
        response.raise_for_status()
    except Exception as exc:
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        body_text = f" - {resp.text[:300]}" if (resp is not None and resp.text) else ""
        detail = f"HTTP {status}{body_text}" if status else type(exc).__name__
        raise RuntimeError(f"Teams delivery failed ({detail}); webhook URL redacted") from None
    LOG.info("Teams digest delivered (HTTP %s)", response.status_code)
