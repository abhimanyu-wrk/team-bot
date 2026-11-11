# Daily Tech Intelligence Aggregator

A deterministic, explainable technology intelligence briefing bot. It discovers recent stories, checks for meaningful technical developments, clusters cross-publication coverage, ranks evidence and trend signals, and sends up to five stories to Microsoft Teams. The GitHub Actions schedule runs at **11:00 AM IST** (`30 5 * * *` UTC) without an always-on computer.

## What changed from a basic headline digest

- **Two Distinct Categories**: News discovery and editorial ranking are organized into **Breaking Developments** (timely, industry-shifting events, major AI model releases, zero-day CVEs, breaches, acquisitions) and **Technical Intelligence** (substantive engineering deep-dives, hardware-native compilers, architecture breakdowns, production AI implementations, research papers).
- **Article Analysis & Reasoning Engine**: Generates fact-grounded, article-specific analyses rather than generic boilerplate templates. For Breaking Developments: *What happened*, *Why it matters*, *Who should care*, and *Action / implications* (with verified CVEs and affected platforms). For Technical Intelligence: *What was built or discovered*, *How it works*, *Why it matters*, and *Engineering takeaway*.
- **Event-Level Deduplication**: Identifies the underlying event, vulnerability, or monthly security cycle (e.g. Microsoft Patch Tuesday roundups and Windows 10 KB updates). Consolidates coverage into a single authoritative primary story with supporting details merged, ensuring headline topic diversity.
- **7-Part Weighted Scoring Model**: Evaluates articles against seven explicit criteria: Technical significance (20%), Audience relevance (20%), Novelty and originality (15%), Industry impact (15%), Technical depth/actionability (15%), Source credibility (10%), and Timeliness (5%), with qualitative assessments (`High`, `Medium`, `Notable`, `Low`).
- **Breakthrough Level Classification**: Categorizes releases into *Major breakthrough*, *Significant development*, and *Routine release or announcement*. Routine releases (such as minor fine-tunes or small SLM releases without benchmarks) are barred from Breaking Developments.
- **Dedicated Promotional & Hype Detection**: Detects sales CTAs, marketing buzzwords, root domain landing pages, and self-promotional Show HN posts, penalizing or rejecting them while preserving legitimate engineering deep-dives from company engineering blogs (Meta, NVIDIA, PostgreSQL, Google, Cloudflare).
- **Flexible Category Delivery (Up to 5 Stories Per Category)**: Delivers up to 5 genuinely valuable articles per category without forcing filler articles when fewer stories meet editorial standards.
- **Teams Digest Adaptive Card**: The Teams briefing displays separate, conditional sections for `BREAKING DEVELOPMENTS` and `TECHNICAL INTELLIGENCE` with qualitative ratings, integer scores, and clean canonical URLs without tracking parameters. Empty sections are omitted.
- **Explainable Diagnostics and Logging**: Every candidate report and log records the promotional likelihood score, quality score, editorial category, and rejection reasons. GitHub Actions uploads full diagnostics as an artifact.

The default evaluator uses deterministic editorial rules and does not call a paid LLM. Clustering is a lightweight, explainable title similarity model with concept normalization; it is not a general-purpose language model. Inspect the diagnostic report when semantic judgment is ambiguous.

## Discovery sources

The centralized registry is [config/sources.json](config/sources.json). It includes the requested technology publications and primary-source ecosystem: TechCrunch, The Verge, Ars Technica, WIRED, The Register, Engadget, MIT Technology Review, IEEE Spectrum; BleepingComputer, Krebs on Security, Dark Reading, The Hacker News, CISA; OpenAI, Anthropic, Google DeepMind, Hugging Face, arXiv; GitHub Blog, Cloudflare, Netflix, Uber, Meta Engineering, PostgreSQL, MongoDB, AWS Architecture; Hacker News, GitHub repository search and releases, Google News, and lower-credibility Medium/Towards Data Science/Better Programming feeds. Where a publisher does not offer an accessible feed, Anthropic, Uber Engineering, and Reuters are discovered through publisher-restricted Google News RSS queries.

RSS/Atom failures are isolated and logged; one broken source will not stop the run. The Hacker News Algolia API contributes points and comments. The GitHub public Search API contributes current star counts for recently created projects; daily snapshots measure actual subsequent changes. Reddit discovery is optional and skipped unless OAuth credentials are configured. Reddit scores, comments, and GitHub stars remain trend signals only.

No engagement fields are invented: unavailable metrics are omitted. RSS dates without a publisher timestamp are assigned fetch time by the parser, so those stories may have less reliable recency.

### Add or remove sources

Edit `config/sources.json` without changing application code. A basic source entry is:

```json
{"name":"Example Engineering Blog","type":"rss","url":"https://example.com/feed.xml","category":"Engineering","tier":4,"credibility":0.8}
```

Supported types are `rss` / `arxiv`, `hackernews`, `github_trending`, `github_releases`, and configurable `json_api`. For a JSON API, set `items_path`, `fields` (title, url, published, summary), optional numeric `metrics`, query `params`, and optional `token_env` to the name of an environment variable. GitHub releases are checked for a configurable watchlist of major open-source projects. Custom RSS feeds need no code changes. Source credentials use environment variables and must not be written into the registry.

Set Reddit credentials as repository secrets `REDDIT_CLIENT_ID` and `REDDIT_CLIENT_SECRET`, plus `REDDIT_USER_AGENT`, to enable the optional OAuth API. Without them, all other sources still run.

## Editorial and scoring configuration

Edit [config/editorial.json](config/editorial.json):

- `weights`: ranking weights. Values are normalized at runtime, so they need not add to exactly 1.
- `minimum_quality_score`: minimum candidate score out of 100; default 48.
- `max_articles_per_source`: source dominance cap; default 2.
- `lookback_hours`: default 36 hours, within the requested 24–48-hour window. `NEWS_LOOKBACK_HOURS` overrides it.
- `exclude_title_patterns`, `exclude_body_patterns`: case-insensitive regular expressions for routine announcements and other exclusions.
- `high_value_patterns`, `technical_patterns`: positive technical-significance and depth evidence.
- `source_credibility_overrides`: map exact source names or article domains to a 0–1 credibility.
- `category_priority`: small signed adjustment to source credibility by category (for example `0.03` or `-0.05`).
- `blacklisted_domains`, `blacklisted_sources`, `boosted_domains`: source policy controls.
- `manual_feedback`: add `promoted_urls`, `rejected_urls`, and domain adjustments under `positive_domains` / `negative_domains`.

`NEWS_LOOKBACK_HOURS` defaults to 36 and `MAX_ARTICLES` to 5. Sent URLs and normalized story fingerprints are retained for 14 days. GitHub Actions cache is best-effort persistence and may be evicted; use a durable database if guaranteed long-term idempotency is required.

## Reports and diagnostic mode

Every run writes `.reports/tech-intelligence-<timestamp>.json` and `.md`. Reports contain the top 20 scored candidates, original publisher and link, date, category, score breakdown, coverage, actual engagement metrics, summary evidence, selection reasons, and rejection reasons. The JSON also includes the separate rejected-story list. Workflow runs upload both reports as an artifact.

Run without posting:

```powershell
$env:DIAGNOSTIC_ONLY = "true"
python main.py
```

This mode does not require `TEAMS_WEBHOOK_URL`. `DRY_RUN=true` instead builds and prints the Teams card JSON without delivering it or marking stories sent.

## Teams setup and permissions

Microsoft retired connector-based Incoming Webhooks in May 2026. Use a Teams **Workflows** webhook. In the target channel, choose **More options (…) → Workflows**, select a webhook template such as **Send webhook alerts to a channel**, save it, and copy its URL. Store it as the GitHub repository Actions secret `TEAMS_WEBHOOK_URL`. The bot retains the standard Teams envelope (`type: message`, Adaptive Card attachment, schema version 1.4).

The account creating the flow needs permission to create Workflows and post to the target channel. Add a co-owner so the flow is not tied to one person's account. If tenant policy requires authenticated requests, the current bot does not attach a bearer token; use an approved unauthenticated URL secret or extend authentication to match tenant policy. Never put a webhook URL in source code, config, or logs.

## GitHub Actions deployment

1. Push the repository files to the default branch of a GitHub repository.
2. In **Settings → Secrets and variables → Actions**, create `TEAMS_WEBHOOK_URL`. Add the optional Reddit credentials only if Reddit discovery is wanted.
3. Ensure Actions are enabled. The workflow token needs only repository read access; sent-state and history use the Actions cache.
4. Use **Actions → Daily Technology News Digest → Run workflow**. Check the Teams channel and download the diagnostic artifact. Select `diagnostic_only` to inspect candidate reports without a Teams post.
5. The scheduled job runs daily at 05:30 UTC / 11:00 IST. GitHub may delay scheduled starts during high load, so check workflow runs if exact minute-level delivery matters.

No local cron, Windows service, administrator installation, or laptop uptime is needed. GitHub Actions and the public APIs/feeds are used within their free/public access limits; feed and API limits vary. GitHub public search is one request per daily job, and Reddit is optional.

## Local setup

Python 3.10+ is recommended. Windows uses `tzdata` for IANA time zones.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
$env:DIAGNOSTIC_ONLY = "true"
python main.py
```

The program reads process environment variables and does not auto-load `.env`. Set them in the shell or use a local environment loader; keep `.env` untracked. Unit tests:

```powershell
python -m unittest discover -s tests -v
```

## Limits and interpretation

This is an explainable newsroom-style filter, not an autonomous fact checker. It uses article text when accessible, source credibility settings, explicit rules, categories, concept-normalized duplicate matching, and exposed trend metrics. It cannot reliably infer every technical nuance from a title or a paywalled feed summary. A low score is an editorial signal, not a factual claim. Read the source and the candidate/rejection explanation when tuning policy. Configure trusted source ratings and exclusions over time; historical metrics are recorded but popularity alone never changes future weights.
