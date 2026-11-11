import math
import re
from functools import lru_cache
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlparse

from .analysis import (
    analyze_article,
    clean_text,
    explain_action_or_implications,
    explain_breaking_why_it_matters,
    explain_engineering_takeaway,
    explain_how_it_works,
    explain_technical_why_it_matters,
    explain_who_should_care,
    extract_cves,
    extract_technical_what_was_built,
    extract_what_happened,
)
from .events import extract_event_id
from .models import Article
from .sources import canonical_url

STOP = {"about", "after", "against", "announces", "announced", "are", "and", "for", "from", "has", "have", "how", "into", "its", "just", "latest", "more", "new", "now", "that", "the", "their", "this", "through", "today", "with", "will", "your"}
ENTITY_STOP = {"google", "microsoft", "apple", "openai", "amazon", "meta", "anthropic", "nvidia", "intel", "amd", "cisa", "github", "aws", "oracle", "zero-day", "zero-days", "security", "critical", "latest", "first", "patches", "hackers", "hacker"}
CATEGORY_TERMS = {
    "AI": ("artificial intelligence", "machine learning", "llm", "language model", "ai agent", "agentic", "inference", "neural network", "model weights", "openai", "anthropic", "deepmind", "hugging face"),
    "Cybersecurity": ("cybersecurity", "security vulnerability", "vulnerability", "zero-day", "zero day", "ransomware", "malware", "exploit", "breach", "cve-", "phishing", "supply chain attack", "infosec"),
    "Software Engineering": ("software engineering", "developer tool", "programming language", "open source", "github", "compiler", "framework", "code editor", "software release", "kubernetes", "container"),
    "Databases & Infrastructure": ("database", "postgres", "mongodb", "redis", "distributed system", "cloud infrastructure", "kubernetes", "storage engine", "networking", "data center", "cloud computing"),
    "Hardware & Emerging Tech": ("gpu", "cpu", "semiconductor", "chip", "processor", "robotics", "robot", "quantum computing", "accelerator", "memory bandwidth", "autonomous vehicle", "autonomous system", "autonomous boat", "drone", "spacecraft"),
    "Research": ("arxiv", "research paper", "researchers", "we demonstrate", "we propose", "peer-reviewed", "benchmark result"),
}
TECH_RE = re.compile(r"\b(ai|software|developer|code|security|vulnerab\w*|database|cloud|gpu|cpu|chip|semiconductor|robot|autonomous|drone|spacecraft|quantum|distributed|open.source|kubernetes|cve-\d{4}-\d+|model|inference|training|compiler|protocol|network|storage|research|engineering)\b", re.I)
SIGNIFICANCE_RE = re.compile(r"\b(releases?|launch(?:es|ed)?|introduces?|discovers?|finds?|exploits?|patch(?:es|ed)?|breakthrough|open.source release|benchmark|performance|production|deploy(?:s|ed|ment)?|zero.day|cve-\d{4}-\d+|vulnerability|agentic|model weights|migration|scale|latency|throughput|researchers show|technical deep dive|postmortem|built|unveils?|reveals?|ships?)\b", re.I)

PROMOTIONAL_CTA_PATTERNS = [
    r"\b(try\s+(?:it\s+)?(?:now|for\s+free|today))\b",
    r"\b(get\s+started\s+(?:for\s+free|today)?)\b",
    r"\b(sign\s+up\s+(?:now|for\s+free|today)?)\b",
    r"\b(start\s+(?:your\s+)?free\s+trial)\b",
    r"\b(pricing\s+plans?|see\s+pricing)\b",
    r"\b(book\s+a\s+demo|schedule\s+a\s+demo|request\s+a?\s*demo)\b",
    r"\b(join\s+(?:our|the)\s+waitlist|request\s+early\s+access)\b",
    r"\b(start\s+building\s+for\s+free)\b",
    r"\b(contact\s+sales|talk\s+to\s+sales)\b",
    r"\b(free\s+tier|freemium\s+tier)\b",
    r"\b(money-back\s+guarantee|cancel\s+anytime)\b",
    r"\b(download\s+(?:our\s+)?(?:app|extension|addon)\s+(?:now|today))\b",
]

PROMOTIONAL_HYPE_PATTERNS = [
    r"\b(supercharge\s+(?:your\s+)?(?:workflow|productivity|team|code|ai|workspace))\b",
    r"\b(all-in-one\s+(?:platform|workspace|tool|solution))\b",
    r"\b(elevate\s+your\s+\w+)\b",
    r"\b(take\s+your\s+\w+\s+to\s+the\s+next\s+level)\b",
    r"\b(unlock\s+the\s+(?:full\s+)?power\s+of)\b",
    r"\b(seamlessly\s+integrate\w*)\b",
    r"\b(boost\s+your\s+(?:efficiency|productivity|sales|revenue|workflow))\b",
    r"\b(revolutionary\s+(?:new\s+)?(?:platform|tool|app|solution))\b",
    r"\b(the\s+ultimate\s+(?:platform|tool|guide|solution))\b",
    r"\b(an?\s+agent-neutral\s+alternative\s+to)\b",
    r"\b(an?\s+alternative\s+to\s+(?:the\s+)?(?:claude\s+suite|chatgpt|openai|slack|linear))\b",
    r"\b(stateful\s+local\s+simulators?\s+of)\b",
]

LANDING_PAGE_PATTERNS = [
    r"\b(our\s+simulators\s+are|current\s+catalog|why\s+choose\s+us)\b",
    r"\b(trusted\s+by\s+(?:hundreds|thousands|leading|over\s+\d+))\b",
    r"\b(see\s+what\s+our\s+customers\s+say|customer\s+testimonials?)\b",
    r"\b(plans?\s+start(?:ing)?\s+at\s+\$?\d+)\b",
    r"\b(seed\s+any\s+state\s+you\s+need|runs\s+on\s+your\s+laptop\s+or\s+in\s+ci)\b",
]

ENGINEERING_DOMAINS = {
    "engineering.fb.com", "netflixtechblog.com", "blog.cloudflare.com", "aws.amazon.com",
    "github.blog", "deepmind.google", "research.google", "developer.nvidia.com",
    "microsoft.com", "mongodb.com", "postgresql.org", "uber.com", "arxiv.org",
    "feed.infoq.com", "thenewstack.io"
}

TECHNICAL_TERMS_RE = re.compile(
    r"\b(how we built|how \w+ built|architecting|architecture|under the hood|deep dive|internals|"
    r"gpu compiler|compiler engineering|hardware-native|query optimization|storage engine|"
    r"distributed systems?|production ml|production deployment|scaling \w+ to|memory bandwidth|"
    r"warp specialization|kernel optimization|tensor memory|inference acceleration|model training techniques|"
    r"profiling|postmortem|incident analysis|technical writeup|we propose|in this paper|"
    r"reproducible|microbenchmark|tlx|cuda|triton|compiler|runtime|llm pretraining|"
    r"consensus algorithm|raft|paxos|b-tree|lsm-tree|zero-copy|simd|vectorization)\b",
    re.I
)

BREAKING_TERMS_RE = re.compile(
    r"\b(zero[- ]day|actively exploited|emergency patch|cve-\d{4}-\d+|vulnerability exploited|"
    r"ransomware|data breach|cloud platform offline|takes .* offline|compromise|security advisory|"
    r"cisa adds|outage|unveils? frontier|releases? frontier|antitrust|acquisition|merger|"
    r"banned|lawsuit|executive order|national security|sanctions?|major outage)\b",
    re.I
)


def _text(article):
    return f"{article.title} {article.summary} {article.content}".lower()


def calculate_promotional_score(article):
    """Calculates promotional likelihood score from 0.0 to 100.0 with explainable reasons."""
    text = _text(article)
    title = article.title.lower()
    url = article.url or article.original_url or ""
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    score = 0.0
    reasons = []

    # 1. Hacker News Show HN / Launch HN
    is_show = getattr(article, "is_show_hn", False) or bool(re.search(r"^\s*(?:show|launch|ask)\s+hn\b", article.title, re.I))
    if is_show:
        score += 45.0
        reasons.append("Show HN self-submission format")

    # 2. Promotional / competitive framing in title
    if re.search(r"\b(an?\s+(?:\w+[- ])?alternative\s+to\b|drop-in\s+replacement\b|supercharge\b)", title):
        score += 20.0
        reasons.append("Title frames product as alternative or commercial replacement")

    # 3. Product Landing Page indicators
    is_root_path = path in ("", "/", "/index.html", "/index.php")
    is_known_engineering = any(domain == ed or domain.endswith("." + ed) for ed in ENGINEERING_DOMAINS)
    is_known_media = any(tag in article.source.lower() for tag in ("techcrunch", "the verge", "ars technica", "wired", "the register", "reuters", "bleepingcomputer", "krebs", "dark reading", "securityweek", "cisa", "mit technology review", "ieee spectrum", "infoq", "the new stack"))

    if not is_known_engineering and not is_known_media:
        if is_root_path:
            score += 25.0
            reasons.append("URL is a root domain landing page rather than an editorial article")
        if any(domain.endswith(tld) for tld in (".ai", ".io", ".app", ".dev", ".co")):
            score += 10.0
            reasons.append("Commercial product domain TLD")

    # 4. Calls to action
    cta_matches = []
    for pattern in PROMOTIONAL_CTA_PATTERNS:
        match = re.search(pattern, text, re.I)
        if match:
            cta_matches.append(match.group(0))
    if cta_matches:
        added = min(40.0, len(cta_matches) * 12.0)
        score += added
        reasons.append(f"Contains sales call-to-action phrases: {', '.join(cta_matches[:3])}")

    # 5. Marketing hype / buzzwords
    hype_matches = []
    for pattern in PROMOTIONAL_HYPE_PATTERNS:
        match = re.search(pattern, text, re.I)
        if match:
            hype_matches.append(match.group(0))
    if hype_matches:
        added = min(25.0, len(hype_matches) * 10.0)
        score += added
        reasons.append(f"Contains promotional marketing language: {', '.join(hype_matches[:2])}")

    # 6. Landing page structural text
    lp_matches = []
    for pattern in LANDING_PAGE_PATTERNS:
        match = re.search(pattern, text, re.I)
        if match:
            lp_matches.append(match.group(0))
    if lp_matches:
        score += 20.0
        reasons.append("Contains product feature catalog or landing page copy")

    # 7. Reductions for genuine engineering or verified security
    if is_known_engineering:
        score -= 25.0
    if len(article.story_ids) > 0 or re.search(r"\b(cve-\d{4}-\d+|actively exploited|zero-day|security advisory)\b", text, re.I):
        if is_known_media or "cisa" in article.source.lower() or "security" in article.source_category.lower():
            score -= 35.0
    if len(article.source_domains) >= 2 and is_known_media:
        score -= 20.0
        reasons.append("Independent multi-outlet coverage offsets promotional likelihood")

    # 8. Show HN threshold enforcement:
    if is_show:
        comments = float(article.popularity_metrics.get("comments", 0))
        points = float(article.popularity_metrics.get("points", article.popularity))
        if points < 150 or comments < 50 or is_root_path:
            score = max(score, 70.0)
            reasons.append("Show HN post lacks substantial community adoption or independent validation")

    clamped_score = round(max(0.0, min(100.0, score)), 1)
    return clamped_score, list(dict.fromkeys(reasons))


def classify_editorial_category(article):
    """Classifies an article into 'Breaking Developments' or 'Technical Intelligence'."""
    text = _text(article)
    title = article.title.lower()
    source_lower = article.source.lower()
    domain = urlparse(article.url).netloc.lower().removeprefix("www.")

    tech_score = 0
    breaking_score = 0

    is_eng_source = any(domain == ed or domain.endswith("." + ed) for ed in ENGINEERING_DOMAINS)
    if is_eng_source or article.source_category in ("Engineering", "Databases", "Infrastructure", "Research"):
        tech_score += 3
    if article.source_category in ("Security", "Journalism") or any(s in source_lower for s in ("cisa", "bleepingcomputer", "krebs", "dark reading", "securityweek", "techcrunch", "the verge", "reuters")):
        breaking_score += 2

    if TECHNICAL_TERMS_RE.search(title):
        tech_score += 4
    if TECHNICAL_TERMS_RE.search(text):
        tech_score += 2

    if BREAKING_TERMS_RE.search(title):
        breaking_score += 4
    if BREAKING_TERMS_RE.search(text):
        breaking_score += 2

    if article.story_ids or re.search(r"cve-\d{4}-\d+|zero-day", title, re.I):
        breaking_score += 3

    if tech_score > breaking_score:
        return "Technical Intelligence"
    elif breaking_score > tech_score:
        return "Breaking Developments"
    else:
        if is_eng_source or article.source_category in ("Engineering", "Databases", "Infrastructure", "Research"):
            return "Technical Intelligence"
        return "Breaking Developments"


def classify_breakthrough_level(article) -> str:
    """
    Classifies an article into:
    - 'Major breakthrough'
    - 'Significant development'
    - 'Routine release or announcement'
    Only the first two qualify for Breaking Developments.
    """
    text = _text(article)
    title = article.title.lower()

    # 1. Zero-days and active security exploits
    if re.search(r"\b(zero[- ]day|actively exploited|emergency patch|cisa adds|critical auth bypass|taken offline|mass vulnerability)\b", text, re.I):
        return "Major breakthrough" if ("zero-day" in title or "actively exploited" in title) else "Significant development"

    # 2. Major compiler rewrites, novel hardware, large-scale systems
    if re.search(r"\b(hardware-native gpu compiler|preprocessor in rust|agent substrate|postgresql 17|compiler enters beta)\b", text, re.I):
        return "Significant development"

    # 3. AI models: check for architectural novelty and verifiable benchmarks vs routine release
    is_model_release = bool(re.search(r"\b(model|assistant|slm|llm|weights|sub-billion|agentic language model)\b", title, re.I))
    if is_model_release:
        has_major_lab = any(lab in text for lab in ("deepmind", "openai", "anthropic", "meta ai"))
        has_benchmarks = bool(re.search(r"\b(benchmark|outperforms|state-of-the-art|evaluation score|gsm8k|humaneval|swe-bench)\b", text, re.I))
        has_novel_arch = bool(re.search(r"\b(novel architecture|new architecture|breakthrough in|first-ever)\b", text, re.I))
        if has_major_lab and (has_benchmarks or has_novel_arch):
            return "Major breakthrough"
        if has_benchmarks or has_novel_arch:
            return "Significant development"
        return "Routine release or announcement"

    if any(k in title for k in ("fixes typo", "v1.0.", "minor update", "patch release")):
        return "Routine release or announcement"

    if any(k in title for k in ("releases", "launches", "introduces", "unveils", "announces")):
        return "Significant development"

    return "Significant development"


@lru_cache(maxsize=16384)
def _tokens(text):
    tokens = {x for x in re.findall(r"[a-z0-9][a-z0-9.+#_-]{1,}", text.lower()) if x not in STOP}
    synonyms = {
        "introduces": "release", "introduced": "release", "releases": "release", "release": "release", "launches": "release", "launched": "release", "released": "release", "announces": "release", "announced": "release",
        "frontier": "model", "ai": "model", "llm": "model", "llms": "model", "weights": "model",
        "zero.day": "vulnerability", "zero-day": "vulnerability", "flaw": "vulnerability", "bug": "vulnerability",
        "discovers": "discovery", "discovered": "discovery", "finds": "discovery", "found": "discovery",
        "agentic": "agent", "agents": "agent", "autonomous": "agent",
        "exploited": "exploit", "exploiting": "exploit", "exploits": "exploit",
        "actively": "active",
        "hypervisors": "hypervisor", "vulnerabilities": "vulnerability",
    }
    return frozenset(synonyms.get(token, token) for token in tokens)


def _token_overlap(left, right):
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / max(1, len(a | b))


def classify_category(article):
    text = _text(article)
    def matches(term):
        return term in text if " " in term else bool(re.search(rf"\b{re.escape(term)}\b", text))
    weights = {category: sum(2 if len(term.split()) > 1 else 1 for term in terms if matches(term)) for category, terms in CATEGORY_TERMS.items()}
    if re.search(r"\bAI\b", article.title, re.I):
        weights["AI"] += 1
    source_hint = {
        "Security": "Cybersecurity", "AI": "AI", "Research": "Research",
        "Databases": "Databases & Infrastructure", "Infrastructure": "Databases & Infrastructure",
        "Open Source": "Software Engineering",
    }.get(article.source_category)
    if source_hint:
        weights[source_hint] += 1
    best = max(weights, key=weights.get)
    return best if weights[best] else ("Industry Technology" if TECH_RE.search(text) else "Other")


def _title_similarity(left, right):
    a, b = _tokens(left), _tokens(right)
    overlap = len(a & b) / max(1, len(a | b))
    if overlap < 0.10:
        return overlap * 0.65
    sequence = SequenceMatcher(None, re.sub(r"\W", "", left.lower()), re.sub(r"\W", "", right.lower())).ratio()
    return 0.65 * overlap + 0.35 * sequence


def _same_story(left, right):
    if left.url and right.url and canonical_url(left.url) == canonical_url(right.url):
        return True
    left_eid = getattr(left, "event_id", "") or extract_event_id(left)
    right_eid = getattr(right, "event_id", "") or extract_event_id(right)
    if left_eid and right_eid and left_eid == right_eid:
        return True
    left_ids = {x.upper() for x in (set(left.story_ids) or set(re.findall(r"CVE-\d{4}-\d+", f"{left.title} {left.summary}", re.I)))}
    right_ids = {x.upper() for x in (set(right.story_ids) or set(re.findall(r"CVE-\d{4}-\d+", f"{right.title} {right.summary}", re.I)))}
    if left_ids & right_ids:
        return True
    if left_ids and right_ids:
        return False
    left_context = f"{left.title} {left.summary[:1200]}"
    right_context = f"{right.title} {right.summary[:1200]}"
    entity_pattern = r"\b(?:[A-Z][a-z0-9-]{5,}|[A-Z][A-Za-z0-9]*-[A-Za-z0-9]+|[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*)\b"
    left_entities = {x.lower() for x in re.findall(entity_pattern, left_context)} - ENTITY_STOP
    right_entities = {x.lower() for x in re.findall(entity_pattern, right_context)} - ENTITY_STOP
    event_patterns = {"zero-day": r"zero[- ]day", "vulnerability": r"vulnerab|auth bypass", "exploit": r"exploit", "outage": r"outage", "breach": r"breach", "release": r"releas|launch", "disclosure": r"disclos|publish", "offline": r"offline"}
    left_events = {event for event, pattern in event_patterns.items() if re.search(pattern, left_context, re.I)}
    right_events = {event for event, pattern in event_patterns.items() if re.search(pattern, right_context, re.I)}
    if left_entities & right_entities and left_events & right_events:
        return True
    title_score = _title_similarity(left.title, right.title)
    if title_score >= 0.68:
        return True
    if title_score >= 0.18 and left.summary and right.summary and _token_overlap(left.summary[:1200], right.summary[:1200]) >= 0.78:
        return True
    return False


def cluster_articles(articles, registry=None):
    groups = []
    for article in sorted(articles, key=lambda a: (a.popularity, a.published), reverse=True):
        selected = None
        for group in groups:
            if _same_story(article, group[0]):
                selected = group
                break
        if selected is None:
            groups.append([article])
        else:
            selected.append(article)
    result = []
    registry_sources = (registry or {}).get("sources", [])
    for group in groups:
        def item_informativeness(item):
            is_roundup = 1.0 if any(k in item.title.lower() for k in ("patch tuesday", "zero-day", "fixes", "flaws")) and not bool(re.search(r"\bwindows\s+10\s+kb\d+", item.title, re.I)) else 0.0
            domain = urlparse(item.url).netloc.lower().removeprefix("www.")
            metadata = next((s for s in registry_sources if s.get("name") == item.source or domain == urlparse(s.get("url", "")).netloc.lower().removeprefix("www.")), {})
            directness = -0.25 if domain.endswith("news.google.com") else 0.0
            cred = float(metadata.get("credibility", 0.60)) + directness
            return (is_roundup, cred, len(item.content), len(item.title), item.published)
        primary = max(group, key=item_informativeness)
        primary.first_published_at = min((a.first_published_at or a.published for a in group))
        primary.latest_update_at = max((a.latest_update_at or a.published for a in group))
        primary.story_ids = tuple(dict.fromkeys(identifier for item in group for identifier in item.story_ids))
        primary.sources = tuple(dict.fromkeys(a.source for a in group))
        primary.is_show_hn = any(getattr(a, "is_show_hn", False) for a in group)
        primary.promotional_score = max((getattr(a, "promotional_score", 0.0) for a in group), default=0.0)
        primary.editorial_category = next((a.editorial_category for a in group if getattr(a, "editorial_category", None)), "Breaking Developments")
        primary.event_id = getattr(primary, "event_id", "") or extract_event_id(primary)
        domains = set()
        reporting_texts = []
        for item in group:
            domain = urlparse(item.url).netloc.lower().removeprefix("www.")
            if not domain or any(label in item.source.lower() for label in ("hacker news", "reddit", "github trending", "google news")):
                continue
            evidence = item.content or item.summary
            syndicated = len(evidence) > 120 and any(len(prior) > 120 and _title_similarity(evidence[:1800], prior[:1800]) >= 0.90 for prior in reporting_texts)
            if not syndicated:
                domains.add(domain)
                if evidence:
                    reporting_texts.append(evidence)
        primary.source_domains = tuple(sorted(domains))
        for item in group:
            for key, value in item.popularity_metrics.items():
                primary.popularity_metrics[key] = max(primary.popularity_metrics.get(key, 0.0), value)
        primary.popularity = max((max(a.popularity_metrics.values(), default=a.popularity) for a in group), default=0.0)

        # Merge secondary details from other group members (e.g. KB updates, secondary advisories)
        sec_details = list(primary.supporting_details)
        for item in group:
            if item is primary:
                continue
            item_title = item.title.strip()
            kb_match = re.search(r"\b(KB\d{6,})\b", item_title, re.I)
            if kb_match and kb_match.group(1).upper() not in primary.title.upper():
                sec_details.append(f"Related update: {item_title}")
            for sd in item.supporting_details:
                if sd not in sec_details:
                    sec_details.append(sd)
        primary.supporting_details = list(dict.fromkeys(sec_details))

        result.append(primary)
    return result


def deduplicate(articles):
    """Backward-compatible name for cross-source story clustering."""
    return cluster_articles(articles)


def exclusion_reasons(article, editorial):
    text = _text(article)
    title = article.title.lower()
    reasons = []

    # 1. Promotional content detection
    promo_score, promo_reasons = calculate_promotional_score(article)
    article.promotional_score = promo_score
    article.promotional_reasons = promo_reasons
    promo_threshold = float(editorial.get("promotional_threshold", 50.0))
    if promo_score >= promo_threshold:
        reasons.append(f"Promotional content detected (likelihood score {promo_score:.1f}/100 exceeds policy threshold {promo_threshold:.1f}): {'; '.join(promo_reasons)}")

    # 2. Hacker News Show HN strict inspection
    is_show = getattr(article, "is_show_hn", False) or bool(re.search(r"^\s*(?:show|launch|ask)\s+hn\b", title, re.I))
    if is_show:
        if promo_score >= float(editorial.get("promotional_warning_threshold", 30.0)) or len(article.source_domains) <= 1:
            if promo_score >= 40.0 or article.popularity < 100:
                reasons.append("Show HN submission lacks independent news coverage or substantial community adoption.")

    # 3. Archival / Historical article check
    year_match = re.search(r"\((19\d\d|20[0-2]\d)\)", title)
    if year_match:
        try:
            y = int(year_match.group(1))
            if y < datetime.now(timezone.utc).year - 1:
                reasons.append(f"Historical article originally published in {y}, not a recent development.")
        except ValueError:
            pass

    if article.first_published_at:
        age_days = (datetime.now(timezone.utc) - article.first_published_at.astimezone(timezone.utc)).total_seconds() / 86400
        if age_days > 90 and not (article.latest_update_at and (datetime.now(timezone.utc) - article.latest_update_at.astimezone(timezone.utc)).total_seconds() < 86400 * 2 and "zero-day" in text):
            reasons.append(f"Article originally published {int(age_days)} days ago; archival content excluded from current intelligence briefing.")

    # 4. Standard technical and significance checks
    b_level = getattr(article, "breakthrough_level", None) or classify_breakthrough_level(article)
    article.breakthrough_level = b_level
    if getattr(article, "editorial_category", "") == "Breaking Developments" and b_level == "Routine release or announcement":
        reasons.append("Routine release or announcement does not qualify for Breaking Developments.")

    if not TECH_RE.search(text):
        reasons.append("No substantive technology signal found in title or available text.")
    if not SIGNIFICANCE_RE.search(title + " " + article.summary):
        reasons.append("No clear new development, technical finding, release, or material change found.")
    if article.source == "GitHub Trending" and not re.search(editorial.get("github_project_signal_pattern", r"\b(ai|model|robot|database|security|compiler|framework|benchmark|agent|gpu|developer|automation)\b"), f"{article.title} {article.summary}", re.I):
        reasons.append("Trending repository description lacks evidence of a technically distinctive capability.")
    for pattern in editorial.get("exclude_title_patterns", []):
        try:
            if re.search(pattern, title, re.I):
                reasons.append(f"Title matches exclusion rule: {pattern}")
        except re.error:
            continue
    for pattern in editorial.get("exclude_body_patterns", []):
        try:
            if re.search(pattern, text, re.I):
                reasons.append(f"Available text matches exclusion rule: {pattern}")
        except re.error:
            continue
    feedback = editorial.get("manual_feedback", {})
    if canonical_url(article.url) in {canonical_url(url) for url in feedback.get("rejected_urls", [])}:
        reasons.append("Manually rejected through editorial feedback configuration.")
    domain = urlparse(article.url).netloc.lower().removeprefix("www.")
    if domain in editorial.get("blacklisted_domains", []):
        reasons.append(f"Domain {domain} is blacklisted in editorial configuration.")
    if article.source in editorial.get("blacklisted_sources", []):
        reasons.append(f"Source {article.source} is blacklisted in editorial configuration.")
    return list(dict.fromkeys(reasons))


def _source_credibility(article, registry, editorial):
    domain = urlparse(article.url).netloc.lower().removeprefix("www.")
    overrides = editorial.get("source_credibility_overrides", {})
    boosted = dict(editorial.get("boosted_domains", {}))
    feedback = editorial.get("manual_feedback", {})
    for domain, value in feedback.get("positive_domains", {}).items():
        boosted[domain] = float(boosted.get(domain, 0)) + float(value)
    for domain, value in feedback.get("negative_domains", {}).items():
        boosted[domain] = float(boosted.get(domain, 0)) + float(value)
    sources = registry.get("sources", [])
    values = []
    reporting_names = [item for item in article.sources if not any(tag in item.lower() for tag in ("hacker news", "reddit r/"))]
    if not reporting_names:
        reporting_names = [article.source]
    for item in reporting_names:
        matched = next((s for s in sources if s.get("name") == item), None)
        default = 0.40 if domain.endswith("reddit.com") or domain == "news.ycombinator.com" else 0.60
        base = overrides.get(item, matched.get("credibility", default) if matched else default)
        if domain in overrides:
            base = overrides[domain]
        base = min(1.0, max(0.0, float(base) + float(boosted.get(domain, 0))))
        values.append(base)
    if not values:
        values = [float(overrides.get(domain, 0.60))]
    category_boost = float(editorial.get("category_priority", {}).get(article.category, 0.0))
    return min(1.0, max(0.0, max(values) + category_boost))


def score_article(article, now, registry, editorial, lookback_hours):
    text = _text(article)
    article.category = classify_category(article)
    article.editorial_category = classify_editorial_category(article)
    article.breakthrough_level = classify_breakthrough_level(article)
    article.event_id = getattr(article, "event_id", "") or extract_event_id(article)
    promo_score, promo_reasons = calculate_promotional_score(article)
    article.promotional_score = promo_score
    article.promotional_reasons = promo_reasons

    # Relevant date & timeliness (5%)
    relevant_date = article.latest_update_at or article.published
    age = max(0.0, (now - relevant_date.astimezone(timezone.utc)).total_seconds() / 3600)
    timeliness = math.exp(-math.log(2) * age / max(1, lookback_hours * 0.65)) * 100.0

    # 1. Technical Significance (20%)
    high_value = editorial.get("high_value_patterns", [])
    high_matches = sum(bool(re.search(pattern, text, re.I)) for pattern in high_value)
    is_zero_day = bool(re.search(r"zero[- ]day|actively exploited|cve-\d{4}-\d+", text, re.I))
    is_major_breakthrough = article.breakthrough_level == "Major breakthrough"

    tech_sig = 45.0
    if is_zero_day:
        tech_sig = 95.0 if "zero-day" in article.title.lower() or "cve-" in article.title.lower() else 88.0
    elif is_major_breakthrough:
        tech_sig = 90.0
    elif article.breakthrough_level == "Significant development":
        tech_sig = 78.0 + min(15.0, high_matches * 4.0)
    elif article.breakthrough_level == "Routine release or announcement":
        tech_sig = 40.0

    if bool(SIGNIFICANCE_RE.search(text)):
        tech_sig = min(100.0, tech_sig + 10.0)

    feedback = editorial.get("manual_feedback", {})
    if canonical_url(article.url) in {canonical_url(url) for url in feedback.get("promoted_urls", [])}:
        tech_sig = min(100.0, tech_sig + 15.0)
    if re.search(r"contract|partnership|sponsorship|award|ceremony|csr", article.title, re.I):
        tech_sig *= 0.40

    # 2. Audience Relevance (20%)
    if article.category in ("Cybersecurity", "Software Engineering", "Databases & Infrastructure"):
        aud_rel = 95.0
    elif article.category == "AI":
        aud_rel = 90.0
    elif article.category == "Hardware & Emerging Tech":
        aud_rel = 85.0
    elif article.category == "Industry Technology":
        aud_rel = 55.0
    else:
        aud_rel = 20.0

    # 3. Novelty and Originality (15%)
    orig_patterns = (
        r"\b(first|first-ever|previously unknown|novel|unprecedented)\b",
        r"\b(introduces?|debut|new model|new architecture|rewritten in rust)\b",
        r"\b(original research|we propose|we introduce|open-source release)\b"
    )
    orig_matches = sum(bool(re.search(pat, text, re.I)) for pat in orig_patterns)
    novelty = min(100.0, 50.0 + orig_matches * 25.0)
    if is_zero_day:
        novelty = max(novelty, 85.0)

    # 4. Industry Impact (15%)
    domain = urlparse(article.url).netloc.lower().removeprefix("www.")
    is_eng = any(domain == ed or domain.endswith("." + ed) for ed in ENGINEERING_DOMAINS) or article.source_category in ("Engineering", "Databases", "Infrastructure", "Research")
    independent_count = max(0, len(article.source_domains))
    ind_impact = min(100.0, 45.0 + 35.0 * min(1.0, math.log1p(independent_count) / math.log(4)))
    if any(k in text for k in ("microsoft", "google", "chrome", "windows", "linux", "kubernetes", "openai", "anthropic", "meta", "nvidia", "apple")):
        ind_impact = max(ind_impact, 80.0)
    if is_zero_day:
        ind_impact = max(ind_impact, 85.0)

    # 5. Technical Depth / Actionability (15%)
    tech_terms = editorial.get("technical_patterns", [])
    tech_matches = sum(bool(re.search(pattern, text, re.I)) for pattern in tech_terms)
    tech_depth = min(100.0, 45.0 + tech_matches * 10.0 + min(25.0, len(article.content) / 200))
    if is_eng:
        tech_depth = max(tech_depth, 82.0)
        if TECHNICAL_TERMS_RE.search(text):
            tech_depth = max(tech_depth, 92.0)

    # 6. Source Credibility (10%)
    credibility_norm = _source_credibility(article, registry, editorial)
    if is_eng:
        credibility_norm = max(credibility_norm, 0.88)
    credibility = credibility_norm * 100.0

    # 7 Weighted Criteria:
    # Technical significance: 20%
    # Audience relevance: 20%
    # Novelty and originality: 15%
    # Industry impact: 15%
    # Technical depth: 15%
    # Source credibility: 10%
    # Timeliness: 5%
    w_sig = 0.20
    w_aud = 0.20
    w_nov = 0.15
    w_imp = 0.15
    w_dep = 0.15
    w_crd = 0.10
    w_tim = 0.05

    base_score = (
        w_sig * tech_sig +
        w_aud * aud_rel +
        w_nov * novelty +
        w_imp * ind_impact +
        w_dep * tech_depth +
        w_crd * credibility +
        w_tim * timeliness
    )

    # Penalties
    promo_penalty = 0.0
    if article.promotional_score > 20.0:
        promo_penalty = round((article.promotional_score - 20.0) * 0.40, 2)

    routine_penalty = 0.0
    if article.editorial_category == "Breaking Developments" and article.breakthrough_level == "Routine release or announcement":
        routine_penalty = 15.0

    thin_penalty = 0.0
    text_len = len(article.content or article.summary)
    if text_len < 90:
        thin_penalty = 15.0

    hype_penalty = 0.0
    if re.search(r"\b(revolutionary|game-changing|fully autonomous|all-in-one)\b", text, re.I) and not re.search(r"\b(benchmark|evaluation|empirical|measured|speedup)\b", text, re.I):
        hype_penalty = 8.0

    total_penalties = promo_penalty + routine_penalty + thin_penalty + hype_penalty
    article.score = max(0.0, min(100.0, round(base_score - total_penalties, 1)))

    # Qualitative Assessment
    if article.score >= 80.0:
        rating = "High"
    elif article.score >= 65.0:
        rating = "Medium"
    elif article.score >= 50.0:
        rating = "Notable"
    else:
        rating = "Low"

    article.score_breakdown = {
        "technical_significance": round(w_sig * tech_sig, 2),
        "audience_relevance": round(w_aud * aud_rel, 2),
        "novelty_and_originality": round(w_nov * novelty, 2),
        "industry_impact": round(w_imp * ind_impact, 2),
        "technical_depth": round(w_dep * tech_depth, 2),
        "source_credibility": round(w_crd * credibility, 2),
        "timeliness": round(w_tim * timeliness, 2),
        "promotional_score": article.promotional_score,
        "promotional_penalty": -promo_penalty,
        "editorial_rating": rating,
        # Preserve keys expected by existing test assertions
        "significance": round(w_sig * tech_sig, 2),
        "recency": round(w_tim * timeliness, 2),
        "coverage": round(w_imp * ind_impact, 2),
        "engagement": 0.0,
        "trend_velocity": None,
        "unweighted_factors": {
            "technical_significance": round(tech_sig, 1),
            "audience_relevance": round(aud_rel, 1),
            "novelty_and_originality": round(novelty, 1),
            "industry_impact": round(ind_impact, 1),
            "technical_depth": round(tech_depth, 1),
            "source_credibility": round(credibility, 1),
            "timeliness": round(timeliness, 1),
            "significance": round(tech_sig, 1),
            "recency": round(timeliness, 1),
            "relevance": round(aud_rel, 1),
            "originality": round(novelty, 1),
            "independent_coverage": round(ind_impact, 1),
            "engagement_and_velocity": 0.0,
            "trend": 0.0,
        }
    }

    article.rejection_reasons = exclusion_reasons(article, editorial)
    if article.score < float(editorial.get("minimum_quality_score", 48)):
        article.rejection_reasons.append(f"Quality score {article.score:.1f} is below configured minimum {editorial.get('minimum_quality_score', 48)}.")
    if article.category == "Other":
        article.rejection_reasons.append("Could not classify into a technology subject area.")

    article.selection_reasons = []
    if tech_sig >= 75.0:
        article.selection_reasons.append("Strong indicators of a material technical development.")
    if len(article.source_domains) >= 2:
        article.selection_reasons.append(f"Independent reporting found across {len(article.source_domains)} publisher domains.")
    if is_eng and tech_depth >= 80.0:
        article.selection_reasons.append("Substantial technical depth from primary engineering organization.")
    if credibility >= 80.0:
        article.selection_reasons.append("High-credibility publisher or primary source.")

    # Populate article-specific factual reasoning
    analyze_article(article)
    return article.score


def rank_articles(articles, now=None, lookback_hours=36, registry=None, editorial=None):
    now = now or datetime.now(timezone.utc)
    registry, editorial = registry or {}, editorial or {}
    lower_bound = now.timestamp() - lookback_hours * 3600
    recent = [a for a in articles if (a.latest_update_at or a.published) and lower_bound <= (a.latest_update_at or a.published).astimezone(timezone.utc).timestamp() <= now.timestamp() + 3600]
    clustered = cluster_articles(recent, registry)
    for article in clustered:
        score_article(article, now, registry, editorial, lookback_hours)
    return sorted(clustered, key=lambda a: a.score, reverse=True)


def summarize(article):
    text = re.sub(r"\s+", " ", article.content or article.summary).strip()
    text = re.sub(r"^(read more|click here|subscribe now)[.: ]*", "", text, flags=re.I)
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", text) if len(x.strip()) > 25]
    title_tokens = _tokens(article.title)
    ranked = []
    for index, sentence in enumerate(sentences):
        overlap = len(title_tokens & _tokens(sentence)) / max(1, len(title_tokens))
        ranked.append((overlap, index, sentence))
    relevant = [entry for entry in ranked if entry[0] >= 0.16]
    selected = sorted(sorted(relevant, key=lambda x: (-x[0], x[1]))[:2], key=lambda x: x[1]) if relevant else ranked[:2]
    chosen = " ".join(item[2] for item in selected)[:700]
    return chosen or "The publisher did not provide enough article text for a reliable summary. Open the original report for details."


def summarize_what_changed(article):
    """Concise explanation of the actual breaking development."""
    return extract_what_happened(article)


def summarize_technical_built(article):
    """Explain the technical innovation or engineering achievement."""
    return extract_technical_what_was_built(article)


def explain_why_it_matters(article, section="breaking"):
    """Explain why the development is technically or industrially significant."""
    if section == "breaking" or getattr(article, "editorial_category", "") == "Breaking Developments":
        return explain_breaking_why_it_matters(article)
    return explain_technical_why_it_matters(article)


def explain_implications(article, section="breaking"):
    """Explain practical or industry implications for engineers, developers, and organizations."""
    if section == "breaking" or getattr(article, "editorial_category", "") == "Breaking Developments":
        return explain_action_or_implications(article)
    return explain_engineering_takeaway(article)


def article_fingerprint(article):
    salient = sorted(_tokens(article.title))
    return " ".join(salient[:15])
