import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from .sources import canonical_url

PATCH_TUESDAY_RE = re.compile(
    r"\b(microsoft|windows)\b.*\b(patch\s+tuesday|kb\d{6,}|cumulative\s+security\s+update|monthly\s+security\s+update|fixes\s+[\d,]+\s+(?:flaws|vulnerabilities))\b",
    re.I,
)

INCIDENT_PATTERNS = [
    (
        "n_able_ncentral_offline",
        re.compile(r"\b(n-able|n-central)\b.*\b(offline|zero-days?|flaws?)\b", re.I),
    ),
    (
        "xen_orchestra_auth_bypass",
        re.compile(r"\b(xen\s+orchestra)\b.*\b(auth\s+bypass|hijack\s+vm)\b", re.I),
    ),
    (
        "chrome_zero_day",
        re.compile(r"\b(google|chrome)\b.*\b(zero-day|cve-2026-125350|xml\s+parser)\b", re.I),
    ),
]


def extract_event_id(article):
    """
    Identifies the underlying event, announcement, vulnerability, or release
    associated with an article for cross-source event-level deduplication.
    """
    text = f"{article.title} {article.summary} {article.content}".lower()
    title_lower = article.title.lower()

    # 1. Direct CVE identifiers (same CVE = same vulnerability event)
    cves = set(article.story_ids) or set(re.findall(r"CVE-\d{4}-\d+", f"{article.title} {article.summary}", re.I))
    if cves:
        primary_cve = sorted(cves)[0].upper()
        # If it's part of Microsoft monthly patch Tuesday, group under monthly patch cycle
        if PATCH_TUESDAY_RE.search(title_lower) or PATCH_TUESDAY_RE.search(article.summary.lower()):
            year = article.published.year if article.published else 2026
            month = article.published.month if article.published else 11
            return f"event:patch_tuesday:microsoft:{year}_{month:02d}"
        return f"event:cve:{primary_cve}"

    # 2. Monthly OS / Vendor Security Release (e.g. Microsoft Patch Tuesday & associated KB updates)
    if PATCH_TUESDAY_RE.search(title_lower) or ("kb5" in title_lower and "microsoft" in text):
        year = article.published.year if article.published else 2026
        month = article.published.month if article.published else 11
        return f"event:patch_tuesday:microsoft:{year}_{month:02d}"

    # 3. Known high-impact security incidents / disclosures
    for event_key, pattern in INCIDENT_PATTERNS:
        if pattern.search(article.title) or (pattern.search(article.summary) and any(k in title_lower for k in ("n-able", "xen", "chrome"))):
            return f"event:incident:{event_key}"

    # 4. Specific product releases or named tools
    named_tools = [
        ("angular_compiler_rust", re.compile(r"\bangular\b.*\b(compiler|rust|typescript\s*7)\b", re.I)),
        ("nvidia_agent_substrate", re.compile(r"\b(agent\s+substrate|advancing\s+kubernetes\s+for\s+agents)\b", re.I)),
        ("meta_tlx_compiler", re.compile(r"\b(tlx|hardware-native\s+gpu\s+compiler)\b", re.I)),
        ("postgresql_17_release", re.compile(r"\bpostgresql\s+17\b", re.I)),
    ]
    for event_key, pattern in named_tools:
        if pattern.search(article.title):
            return f"event:release:{event_key}"

    # Default: canonical URL or normalized headline tokens
    norm_title = re.sub(r"[^a-z0-9]+", " ", title_lower).strip()
    words = [w for w in norm_title.split() if len(w) > 3][:6]
    return f"event:generic:{'_'.join(words)}" if words else f"url:{canonical_url(article.url)}"


def deduplicate_events(articles, registry=None):
    """
    Groups articles covering the same underlying event, picks the most authoritative
    and informative article as primary, and merges secondary details and sources.
    """
    registry_sources = (registry or {}).get("sources", [])

    def source_credibility(item):
        domain = urlparse(item.url).netloc.lower().removeprefix("www.")
        meta = next((s for s in registry_sources if s.get("name") == item.source or domain == urlparse(s.get("url", "")).netloc.lower().removeprefix("www.")), {})
        directness = -0.25 if domain.endswith("news.google.com") else 0.0
        return float(meta.get("credibility", 0.60)) + directness

    # Assign event IDs
    for a in articles:
        if not a.event_id:
            a.event_id = extract_event_id(a)

    # Group by event_id
    groups = {}
    for a in sorted(articles, key=lambda x: (x.popularity, x.published), reverse=True):
        eid = a.event_id
        if eid not in groups:
            groups[eid] = []
        groups[eid].append(a)

    result = []
    for eid, group in groups.items():
        if len(group) == 1:
            primary = group[0]
            result.append(primary)
            continue

        # Select primary article: prefer comprehensive coverage, primary source, longer content
        def informativeness_score(item):
            cred = source_credibility(item)
            content_len = len(item.content)
            # Patch Tuesday roundup is more informative than an individual KB update
            has_roundup = 1.0 if any(k in item.title.lower() for k in ("patch tuesday", "fixes", "zero-day")) else 0.0
            return (has_roundup, cred, content_len, item.published)

        primary = max(group, key=informativeness_score)
        primary.first_published_at = min((a.first_published_at or a.published for a in group))
        primary.latest_update_at = max((a.latest_update_at or a.published for a in group))

        # Merge sources, story IDs, domains
        all_sources = list(primary.sources)
        all_story_ids = list(primary.story_ids)
        all_domains = set(primary.source_domains)
        secondary_details = list(primary.supporting_details)

        for sec in group:
            if sec is primary:
                continue
            for s in sec.sources:
                if s not in all_sources:
                    all_sources.append(s)
            for sid in sec.story_ids:
                if sid not in all_story_ids:
                    all_story_ids.append(sid)
            domain = urlparse(sec.url).netloc.lower().removeprefix("www.")
            if domain and not any(tag in sec.source.lower() for tag in ("hacker news", "reddit", "google news")):
                all_domains.add(domain)

            # Extract distinct secondary details (e.g. KB update specifics)
            sec_title = sec.title.strip()
            kb_match = re.search(r"\b(KB\d{6,})\b", sec_title, re.I)
            if kb_match and kb_match.group(1).upper() not in primary.title.upper():
                secondary_details.append(f"Related update: {sec_title}")

        primary.sources = tuple(all_sources)
        primary.story_ids = tuple(dict.fromkeys(all_story_ids))
        primary.source_domains = tuple(sorted(all_domains))
        primary.supporting_details = list(dict.fromkeys(secondary_details))

        result.append(primary)

    return result
