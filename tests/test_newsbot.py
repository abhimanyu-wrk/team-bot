import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from newsbot.config import load_json
from newsbot.models import Article
from newsbot.ranking import classify_category, deduplicate, rank_articles, summarize
from newsbot.reports import evaluate_quality
from newsbot.sources import canonical_url
from newsbot.state import add_trend_velocity, filter_sent, load_sent, save_sent
from newsbot.teams import build_card


class NewsBotTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 10, 5, 30, tzinfo=timezone.utc)

    def article(self, title="AI chip launch", source="TechCrunch", url="https://example.com/story?utm_source=x", hours=1, summary="A new technology product launches. This could change the market."):
        return Article(title, url, source, self.now - timedelta(hours=hours), summary, 10)

    def test_canonical_url_drops_tracking_parameters(self):
        self.assertEqual(canonical_url("https://www.example.com/a/?utm_source=x&id=1#top"), "https://example.com/a?id=1")

    def test_deduplicates_urls_and_similar_titles_and_merges_sources(self):
        a = self.article()
        b = self.article("AI chip launch", "The Verge", "https://example.com/story")
        result = deduplicate([a, b])
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0].sources), {"TechCrunch", "The Verge"})

    def test_clusters_differently_worded_coverage_of_one_model_release(self):
        a = self.article("Company X releases a new frontier AI model", "TechCrunch", "https://techcrunch.com/a")
        b = self.article("Company X introduces its latest LLM", "Ars Technica", "https://arstechnica.com/b")
        self.assertEqual(len(deduplicate([a, b])), 1)

    def test_clusters_reporting_with_same_cve_even_when_headlines_differ(self):
        a = self.article("Google patches Chrome zero-day", "BleepingComputer", "https://bleepingcomputer.com/a", summary="CVE-2026-125350 is exploited in attacks.")
        b = self.article("Emergency Chrome security update", "The Hacker News", "https://thehackernews.com/b", summary="Google fixed CVE-2026-125350 after active exploitation.")
        grouped = deduplicate([a, b])
        self.assertEqual(len(grouped), 1)
        self.assertEqual(len(grouped[0].sources), 2)

    def test_clusters_ncentral_zero_day_reporting_with_different_headlines(self):
        a = self.article("N-able takes cloud platform offline after 30+ zero-days published", "BleepingComputer", "https://bleepingcomputer.com/nable-a", summary="N-able took hosted N-central offline after disclosing over 30 vulnerabilities.")
        b = self.article("N-able Takes N-central Cloud Offline After Researcher Discloses 33 Zero-Days", "The Hacker News", "https://thehackernews.com/nable-b", summary="N-able said N-central was taken offline during remediation of the vulnerabilities.")
        self.assertEqual(len(deduplicate([a, b])), 1)

    def test_clusters_related_product_and_event_without_shared_cve(self):
        a = self.article("Critical Xen Orchestra auth bypass flaw exploited to hijack VM servers", "BleepingComputer", "https://bleepingcomputer.com/xo", summary="A critical authentication bypass vulnerability in Xen Orchestra is being actively exploited.")
        b = self.article("Hackers exploit critical Xen Orchestra auth bypass flaw to gain admin access", "The Hacker News", "https://thehackernews.com/xo", summary="Xen Orchestra auth bypass flaw exploited in the wild.")
        self.assertEqual(len(deduplicate([a, b])), 1)

    def test_clusters_chrome_zero_day_roundup_with_original_report(self):
        a = self.article("Google patches eighth Chrome zero-day exploited in attacks this year", "BleepingComputer", "https://bleepingcomputer.com/chrome", summary="Google patched CVE-2026-125350, an actively exploited Chrome zero-day.")
        b = self.article("Chrome zero-day tally hits eight as 2026 whack-a-mole continues", "The Register", "https://theregister.com/chrome", summary="Chrome zero-day attacks have reached an eighth flaw this year.")
        self.assertEqual(len(deduplicate([a, b])), 1)

    def test_ranking_filters_old_stories(self):
        result = rank_articles([self.article(hours=2), self.article("Old story", hours=30)], self.now, 24)
        self.assertEqual(len(result), 1)

    def test_state_persists_and_filters_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/sent.json"
            article = self.article()
            state = save_sent(path, {}, [article], self.now)
            loaded = load_sent(path)
            fresh, _ = filter_sent([article], loaded, self.now)
            self.assertEqual(fresh, [])
            self.assertTrue(state)

    def test_velocity_uses_real_prior_metric_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/metrics.json"
            article = self.article()
            article.popularity_metrics = {"stars": 30}
            save_at = self.now - timedelta(hours=2)
            import json
            from pathlib import Path
            Path(path).write_text(json.dumps({canonical_url(article.url): {"observed_at": save_at.timestamp(), "metrics": {"stars": 10}}}), encoding="utf-8")
            add_trend_velocity([article], path, self.now)
            self.assertAlmostEqual(article.popularity_metrics["velocity_per_hour"], 10)

    def test_card_contains_clickable_article_and_fields(self):
        card = build_card([self.article()], "Asia/Kolkata", self.now)
        rendered = str(card)
        self.assertIn("Original report", rendered)
        self.assertIn("Why it matters", rendered)
        self.assertIn("application/vnd.microsoft.card.adaptive", rendered)

    def test_source_registry_is_configurable_and_valid(self):
        registry = load_json("config/sources.json")
        self.assertGreaterEqual(len(registry["sources"]), 20)
        self.assertTrue(all(item.get("name") and item.get("type") and item.get("url") for item in registry["sources"]))

    def test_classifies_security_development_and_rejects_contract(self):
        security = self.article("Critical zero-day vulnerability actively exploited", "CISA", "https://cisa.gov/advisories/cve", summary="Researchers discovered a critical vulnerability that is actively exploited and requires immediate patching.")
        contract = self.article("Vendor wins $40 million technology infrastructure contract", "Newswire", "https://example.com/contract", summary="A vendor wins a routine contract for technology infrastructure services.")
        editorial = load_json("config/editorial.json")
        ranked = rank_articles([security, contract], self.now, 36, load_json("config/sources.json"), editorial)
        self.assertEqual(classify_category(security), "Cybersecurity")
        contract_row = next(item for item in ranked if "contract" in item.title.lower())
        self.assertTrue(contract_row.rejection_reasons)

    def test_quality_gate_rejects_thin_unverified_article(self):
        article = self.article("New AI tool launches", summary="Short.")
        reasons = evaluate_quality(article, [], 48)
        self.assertTrue(any("Too little article text" in reason for reason in reasons))

    def test_summary_prefers_sentences_about_the_actual_headline(self):
        article = self.article("Chrome zero-day patch exploited in attacks", summary="New GPU products offer faster graphics for gamers. Google patched a Chrome zero-day flaw exploited in attacks. The patch is available to users today.")
        result = summarize(article)
        self.assertIn("Chrome zero-day", result)
        self.assertNotIn("GPU products", result)

    def test_score_breakdown_is_explainable(self):
        article = self.article("Critical zero-day vulnerability patched", "CISA", "https://www.cisa.gov/advisories/cve")
        editorial = load_json("config/editorial.json")
        rank_articles([article], self.now, 36, load_json("config/sources.json"), editorial)
        self.assertIn("significance", article.score_breakdown)
        self.assertIn("trend_velocity", article.score_breakdown)

    def test_excludes_sponsored_and_generic_interview_content(self):
        sponsored = self.article("AI agents are changing cybersecurity", summary="This article is brought to you by Example University and describes generic use cases.")
        interview = self.article("Why senior engineers fail system design interviews", source="Medium Software Engineering")
        editorial = load_json("config/editorial.json")
        rows = rank_articles([sponsored, interview], self.now, 36, load_json("config/sources.json"), editorial)
        self.assertTrue(all(row.rejection_reasons for row in rows))

    def test_rejects_trending_repository_without_distinctive_technical_signal(self):
        repository = self.article("Open-source project: example/media-app", "GitHub Trending", "https://github.com/example/media-app", summary="A self-hosted app for organizing movies and music.")
        editorial = load_json("config/editorial.json")
        row = rank_articles([repository], self.now, 36, load_json("config/sources.json"), editorial)[0]
        self.assertTrue(any("technically distinctive" in reason for reason in row.rejection_reasons))

    def test_rejects_listicles_and_general_license_commentary(self):
        items = [
            self.article("5 Core Components of Microsoft Fabric Architecture", "Medium Data Engineering", "https://medium.com/example/fabric", summary="Microsoft Fabric architecture brings data integration and storage into one platform."),
            self.article("Weird licenses and the new open source", "Hacker News", "https://example.org/licenses", summary="Long story short, this is a Wikipedia-style explanation of software licenses."),
        ]
        editorial = load_json("config/editorial.json")
        rows = rank_articles(items, self.now, 36, load_json("config/sources.json"), editorial)
        self.assertTrue(all(row.rejection_reasons for row in rows))

    def test_rejects_rystic_show_hn_as_promotional_content(self):
        rystic = Article(
            title="Show HN: Rystic – Stateful Local Simulators of Slack, GitHub and Linear",
            url="https://www.rystic.ai/",
            source="rystic.ai",
            published=self.now - timedelta(hours=2),
            summary="Slack, GitHub, Kalshi, PayPal, Lob, Okta, Linear, Stripe on localhost. Our simulators are local, stateful, settable. Current catalog: Slack, GitHub, Linear, Stripe. Verified daily.",
            popularity=4.0,
            popularity_metrics={"points": 4.0, "comments": 0.0},
            source_category="Community",
            is_show_hn=True,
        )
        editorial = load_json("config/editorial.json")
        ranked = rank_articles([rystic], self.now, 36, load_json("config/sources.json"), editorial)
        self.assertGreaterEqual(rystic.promotional_score, 50.0)
        self.assertTrue(any("Promotional content detected" in r or "Show HN" in r for r in rystic.rejection_reasons))

    def test_rejects_mimzie_show_hn_as_promotional_content(self):
        mimzie = Article(
            title="Show HN: Mimzie, an agent-neutral alternative to the Claude Suite/Claude Cowork",
            url="https://mimzie.com/",
            source="mimzie.com",
            published=self.now - timedelta(hours=3),
            summary="Orchestrate Claude, ChatGPT, Gemini, and Grok to superpower your AI workspace. Use the plan you already pay for. Sign in once and it works on your files. Give each bot one job.",
            popularity=4.0,
            popularity_metrics={"points": 4.0, "comments": 0.0},
            source_category="Community",
            is_show_hn=True,
        )
        editorial = load_json("config/editorial.json")
        ranked = rank_articles([mimzie], self.now, 36, load_json("config/sources.json"), editorial)
        self.assertGreaterEqual(mimzie.promotional_score, 50.0)
        self.assertTrue(any("Promotional content detected" in r or "Show HN" in r for r in mimzie.rejection_reasons))

    def test_meta_tlx_engineering_article_eligible_under_technical_intelligence(self):
        tlx = Article(
            title="How We Built TLX: A Hardware-Native GPU Compiler for Production ML at Scale",
            url="https://engineering.fb.com/2026/11/10/open-source/tlx-a-hardware-native-gpu-compiler-for-production-ml/",
            source="Meta Engineering",
            published=self.now - timedelta(hours=5),
            summary="Triton Low-Level Extensions (TLX) is an extension to Triton that gives engineers explicit control over GPU execution while preserving Triton's productive programming model. TLX introduces warp-group and cluster-level orchestration for production ML training and large-scale LLM pretraining.",
            content="Modern GPU architectures increasingly rely on execution-oriented features such as warp specialization, asynchronous memory movement, tensor memory, and cluster-level execution. We built Triton Low-Level Extensions (TLX) to give engineers control while maintaining velocity. In production at Meta, TLX powers RecSys training and large-scale LLM pretraining with significant throughput improvements.",
            popularity=15.0,
            popularity_metrics={"points": 25.0},
            source_category="Engineering",
        )
        editorial = load_json("config/editorial.json")
        registry = load_json("config/sources.json")
        ranked = rank_articles([tlx], self.now, 36, registry, editorial)
        self.assertEqual(tlx.editorial_category, "Technical Intelligence")
        self.assertLess(tlx.promotional_score, 30.0)
        self.assertGreaterEqual(tlx.score, 50.0)
        self.assertEqual(tlx.rejection_reasons, [])

    def test_chrome_zero_day_eligible_under_breaking_developments(self):
        chrome = Article(
            title="Google patches eighth Chrome zero-day exploited in attacks this year",
            url="https://www.bleepingcomputer.com/news/security/google-patches-eighth-chrome-zero-day-exploited-in-attacks-this-year/",
            source="BleepingComputer",
            published=self.now - timedelta(hours=4),
            summary="Google has released emergency security updates to patch a new Chrome vulnerability exploited in zero-day attacks (CVE-2026-125350). Attackers can exploit this memory corruption weakness in Chrome's XML parser to execute malicious code.",
            content="Google is aware that an exploit for CVE-2026-125350 exists in the wild. This high-severity memory corruption flaw allows attackers to execute arbitrary code. Users are advised to apply emergency patches immediately.",
            story_ids=("CVE-2026-125350",),
            source_category="Security",
        )
        editorial = load_json("config/editorial.json")
        registry = load_json("config/sources.json")
        ranked = rank_articles([chrome], self.now, 36, registry, editorial)
        self.assertEqual(chrome.editorial_category, "Breaking Developments")
        self.assertEqual(chrome.promotional_score, 0.0)
        self.assertGreaterEqual(chrome.score, 55.0)
        self.assertEqual(chrome.rejection_reasons, [])

    def test_does_not_force_five_articles_when_fewer_meet_threshold(self):
        from newsbot.reports import select_balanced_articles
        good1 = Article(
            title="Critical zero-day actively exploited in enterprise hypervisors",
            url="https://cisa.gov/advisories/cve-2026-9999",
            source="CISA Advisories",
            published=self.now - timedelta(hours=2),
            summary="CISA added a critical vulnerability actively exploited in attacks to the KEV catalog.",
            content="A critical zero-day vulnerability in hypervisor platforms is actively exploited in targeted intrusions. Organizations must patch immediately.",
            score=75.0,
            editorial_category="Breaking Developments",
            category="Cybersecurity",
        )
        good2 = Article(
            title="How We Built TLX: A Hardware-Native GPU Compiler for Production ML at Scale",
            url="https://engineering.fb.com/2026/11/10/open-source/tlx-gpu-compiler/",
            source="Meta Engineering",
            published=self.now - timedelta(hours=3),
            summary="Meta built TLX to optimize GPU execution for production machine learning.",
            content="We built Triton Low-Level Extensions (TLX) to give engineers explicit hardware control in production.",
            score=72.0,
            editorial_category="Technical Intelligence",
            category="AI",
        )
        subpar = Article(
            title="Minor tool update v1.0.1 fixes typo",
            url="https://example.com/tool",
            source="Example Blog",
            published=self.now - timedelta(hours=4),
            summary="A minor tool update fixes a typo in the documentation.",
            content="We released version 1.0.1 to fix a small issue.",
            score=35.0,
            editorial_category="Technical Intelligence",
            category="Software Engineering",
        )
        selected, rejected = select_balanced_articles([good1, good2, subpar], max_articles=5, min_score=48.0)
        self.assertEqual(len(selected), 2)
        self.assertIn(good1, selected)
        self.assertIn(good2, selected)
        self.assertNotIn(subpar, selected)
        self.assertIn(subpar, rejected)

    def test_teams_card_formats_both_categories_and_omits_empty_sections(self):
        breaking_art = Article(
            title="Emergency Chrome security update patches active zero-day",
            url="https://bleepingcomputer.com/news/security/chrome-patch",
            source="BleepingComputer",
            published=self.now - timedelta(hours=2),
            summary="Google patched CVE-2026-125350 after active exploitation in attacks.",
            content="Google patched an actively exploited Chrome zero-day vulnerability.",
            score=70.0,
            category="Cybersecurity",
            editorial_category="Breaking Developments",
        )
        tech_art = Article(
            title="How We Built TLX: Hardware-Native GPU Compiler",
            url="https://engineering.fb.com/open-source/tlx",
            source="Meta Engineering",
            published=self.now - timedelta(hours=4),
            summary="Meta engineered TLX for hardware-native GPU control in production ML.",
            content="TLX introduces warp orchestration for large-scale production training.",
            score=68.0,
            category="AI",
            editorial_category="Technical Intelligence",
        )
        card_both = build_card([breaking_art, tech_art], "Asia/Kolkata", self.now)
        text_both = str(card_both)
        self.assertIn("BREAKING DEVELOPMENTS", text_both)
        self.assertIn("What happened:", text_both)
        self.assertIn("TECHNICAL INTELLIGENCE", text_both)
        self.assertIn("What was built or discovered:", text_both)

        # Card with only breaking development must NOT include empty TECHNICAL INTELLIGENCE section
        card_breaking_only = build_card([breaking_art], "Asia/Kolkata", self.now)
        text_breaking = str(card_breaking_only)
        self.assertIn("BREAKING DEVELOPMENTS", text_breaking)
        self.assertNotIn("TECHNICAL INTELLIGENCE", text_breaking)

    def test_rejects_historical_2013_article_from_lookback_window(self):
        old_article = Article(
            title="A great old-timey game-programming hack (2013)",
            url="https://blog.moertel.com/posts/2013-12-14-great-old-timey-game-programming-hack.html",
            source="blog.moertel.com",
            published=self.now - timedelta(hours=1),
            summary="A story from 2013 about 8-bit PC game programming hacks.",
            content="When I was an undergrad in 2013, I spent time on game programming hacks.",
            popularity=70.0,
            popularity_metrics={"points": 70.0, "comments": 10.0},
            source_category="Community",
        )
        editorial = load_json("config/editorial.json")
        ranked = rank_articles([old_article], self.now, 36, load_json("config/sources.json"), editorial)
        self.assertTrue(any("Historical article" in r or "archival" in r for r in old_article.rejection_reasons))

    def test_balanced_selection_guideline_four_breaking_one_technical(self):
        from newsbot.reports import select_balanced_articles
        breaking = [
            Article('Critical zero-day actively exploited in Chrome browser XML parser', 'https://example.com/sec1', 'BleepingComputer',
                    self.now - timedelta(hours=1),
                    summary='Google released an emergency security update to patch an actively exploited zero-day vulnerability in Chrome browser XML parser.',
                    score=79.0, editorial_category='Breaking Developments', category='Cybersecurity'),
            Article('Remote access platform taken offline following mass vulnerability disclosure', 'https://example.com/sec2', 'TechCrunch',
                    self.now - timedelta(hours=2),
                    summary='N-able took its hosted remote access platform offline after security researchers published dozens of zero-day exploits online.',
                    score=78.0, editorial_category='Breaking Developments', category='Cybersecurity'),
            Article('Major cloud hypervisor authentication bypass vulnerability exploited in wild', 'https://example.com/sec3', 'Dark Reading',
                    self.now - timedelta(hours=3),
                    summary='Security researchers warn of active exploitation of a critical authentication bypass flaw in virtual machine hypervisors.',
                    score=77.0, editorial_category='Breaking Developments', category='Cybersecurity'),
            Article('Frontier AI lab announces breakthrough in autonomous reasoning models', 'https://example.com/sec4', 'The Verge',
                    self.now - timedelta(hours=4),
                    summary='OpenAI announced a new frontier model with major improvements in autonomous mathematical reasoning and coding benchmarks.',
                    score=76.0, editorial_category='Breaking Developments', category='AI'),
        ]
        technical = [
            Article("Deep dive into distributed consensus and storage engines", "https://engineering.example.com/consensus", "Meta Engineering",
                    self.now - timedelta(hours=2),
                    summary="Deep dive detailing distributed consensus protocols and LSM tree storage engine optimizations in production under high throughput workloads.",
                    score=85.0,
                    editorial_category="Technical Intelligence", category="Databases & Infrastructure")
        ]
        all_articles = breaking + technical
        selected, rejected = select_balanced_articles(all_articles, max_articles=5, min_score=48.0, source_cap=2)
        self.assertEqual(len(selected), 5)
        breaking_count = sum(1 for a in selected if a.editorial_category == "Breaking Developments")
        technical_count = sum(1 for a in selected if a.editorial_category == "Technical Intelligence")
        self.assertEqual(breaking_count, 4)
        self.assertEqual(technical_count, 1)

    def test_balanced_selection_guideline_one_breaking_four_technical(self):
        from newsbot.reports import select_balanced_articles
        breaking = [
            Article("CISA adds active zero-day exploit to KEV catalog", "https://cisa.gov/kev-1", "CISA Advisories",
                    self.now - timedelta(hours=2),
                    summary="CISA adds active zero-day exploit information to the KEV catalog for immediate patching across enterprise software systems.",
                    score=88.0,
                    editorial_category="Breaking Developments", category="Cybersecurity")
        ]
        technical = [
            Article('How Meta built TLX: hardware-native GPU compiler for production ML', 'https://eng.example.com/gpu1', 'Meta Engineering',
                    self.now - timedelta(hours=1),
                    summary='Engineering architecture deep dive on GPU cluster compilation and TLX runtime in production ML infrastructure with measurable speedups.',
                    score=82.0, editorial_category='Technical Intelligence', category='AI'),
            Article('Netflix deep dive into distributed cache invalidation architecture', 'https://eng.example.com/cache2', 'Netflix TechBlog',
                    self.now - timedelta(hours=2),
                    summary='Technical analysis explaining how Netflix built a scalable distributed cache invalidation system across multi-region services.',
                    score=81.0, editorial_category='Technical Intelligence', category='Databases & Infrastructure'),
            Article('PostgreSQL query optimizer rewrite for high-concurrency workloads', 'https://eng.example.com/pg3', 'PostgreSQL',
                    self.now - timedelta(hours=3),
                    summary='PostgreSQL engineering writeup detailing the redesigned query optimizer and SIMD acceleration algorithms in version 17.',
                    score=80.0, editorial_category='Technical Intelligence', category='Databases & Infrastructure'),
            Article('Cloudflare engineering on mitigating terabit-scale DDoS attacks', 'https://eng.example.com/cf4', 'Cloudflare Blog',
                    self.now - timedelta(hours=4),
                    summary='Cloudflare engineers share how eBPF and XDP kernel bypass pipelines automatically mitigate hyper-volumetric DDoS attacks in real time.',
                    score=79.0, editorial_category='Technical Intelligence', category='Software Engineering'),
        ]
        all_articles = breaking + technical
        selected, rejected = select_balanced_articles(all_articles, max_articles=5, min_score=48.0, source_cap=2)
        self.assertEqual(len(selected), 5)
        breaking_count = sum(1 for a in selected if a.editorial_category == "Breaking Developments")
        technical_count = sum(1 for a in selected if a.editorial_category == "Technical Intelligence")
        self.assertEqual(breaking_count, 1)
        self.assertEqual(technical_count, 4)

    def test_promotional_likelihood_scoring_mechanisms(self):
        from newsbot.ranking import calculate_promotional_score
        landing_page = Article(
            title="Next-gen AI assistant for developers",
            url="https://coolsaas.ai/",
            source="coolsaas.ai",
            published=self.now,
            summary="Sign up now for free. Book a demo today and start your free trial. Supercharge your workflow with our revolutionary all-in-one platform.",
            source_category="Community",
        )
        score, reasons = calculate_promotional_score(landing_page)
        self.assertGreaterEqual(score, 60.0)
        self.assertTrue(any("call-to-action" in r.lower() or "landing page" in r.lower() for r in reasons))

        legitimate_news = Article(
            title="PostgreSQL 17 Released with Advanced Query Execution and Memory Enhancements",
            url="https://www.postgresql.org/about/news/postgresql-17-released/",
            source="PostgreSQL",
            published=self.now,
            summary="The PostgreSQL Global Development Group today announced the release of PostgreSQL 17, featuring improved query execution performance, memory management, and logical replication.",
            content="PostgreSQL 17 adds query planner enhancements, SIMD acceleration for memory copy operations, and vacuum optimization.",
            source_category="Databases",
        )
        legit_score, legit_reasons = calculate_promotional_score(legitimate_news)
        self.assertLess(legit_score, 25.0)

    def test_regression_1_microsoft_patch_tuesday(self):
        """
        TEST 1: MICROSOFT PATCH TUESDAY
        Article: Microsoft November 2026 Patch Tuesday fixes 1,235 flaws, Outlook zero-day.
        Expected: Selected as Breaking Developments; recognized as significant cybersecurity news;
        active exploitation given substantial importance; CVE-2026-124194 preserved;
        explanation focuses on actual vulnerability and implications.
        """
        patch_tuesday = Article(
            title="Microsoft November 2026 Patch Tuesday fixes 1,235 flaws, Outlook zero-day",
            url="https://www.bleepingcomputer.com/news/microsoft/microsoft-november-2026-patch-tuesday-fixes-1-235-flaws-outlook-zero-day/",
            source="BleepingComputer",
            published=self.now - timedelta(hours=3),
            summary="Microsoft's November 2026 Patch Tuesday addresses 1,235 security vulnerabilities across Windows, Office, and server software, including an actively exploited Outlook zero-day vulnerability (CVE-2026-124194) allowing remote code execution without user interaction.",
            content="Microsoft released its monthly security update fixing over a thousand vulnerabilities. An actively exploited zero-day in Microsoft Outlook (CVE-2026-124194) allows remote attackers to compromise systems. Enterprise administrators are urged to apply patches immediately.",
            story_ids=("CVE-2026-124194",),
            source_category="Security",
        )
        editorial = load_json("config/editorial.json")
        registry = load_json("config/sources.json")
        ranked = rank_articles([patch_tuesday], self.now, 36, registry, editorial)
        self.assertEqual(patch_tuesday.editorial_category, "Breaking Developments")
        self.assertEqual(patch_tuesday.category, "Cybersecurity")
        self.assertIn("CVE-2026-124194", patch_tuesday.story_ids)
        self.assertGreaterEqual(patch_tuesday.score, 75.0)
        self.assertIn("Outlook", patch_tuesday.analysis["why_it_matters"])
        self.assertIn("CVE-2026-124194", patch_tuesday.analysis["action_or_implications"])
        self.assertIn("Windows", patch_tuesday.analysis["who_should_care"])

    def test_regression_2_windows_10_kb_event_deduplication(self):
        """
        TEST 2: WINDOWS 10 KB5131096 UPDATE
        Article: Microsoft releases Windows 10 KB5131096 extended security update.
        Expected: Recognized as related to Microsoft Patch Tuesday event; not selected as an
        independent headline if it adds no substantial separate development;
        relevant information merged into Patch Tuesday story.
        """
        patch_tuesday = Article(
            title="Microsoft November 2026 Patch Tuesday fixes 1,235 flaws, Outlook zero-day",
            url="https://www.bleepingcomputer.com/news/microsoft/microsoft-november-2026-patch-tuesday-fixes-1-235-flaws-outlook-zero-day/",
            source="BleepingComputer",
            published=self.now - timedelta(hours=3),
            summary="Microsoft addresses 1,235 flaws including an actively exploited Outlook zero-day vulnerability.",
            content="Microsoft released updates for Windows and Office.",
            story_ids=("CVE-2026-124194",),
            source_category="Security",
        )
        kb_update = Article(
            title="Microsoft releases Windows 10 KB5131096 extended security update",
            url="https://www.bleepingcomputer.com/news/microsoft/microsoft-releases-windows-10-kb5131096-extended-security-update/",
            source="BleepingComputer",
            published=self.now - timedelta(hours=2),
            summary="Microsoft deployed the Windows 10 KB5131096 cumulative extended security update as part of the November monthly cycle.",
            content="Windows 10 users with Extended Security Updates received KB5131096 today.",
            source_category="Security",
        )
        editorial = load_json("config/editorial.json")
        registry = load_json("config/sources.json")
        clustered = rank_articles([patch_tuesday, kb_update], self.now, 36, registry, editorial)
        self.assertEqual(len(clustered), 1)
        primary = clustered[0]
        self.assertEqual(primary.title, patch_tuesday.title)
        self.assertTrue(any("KB5131096" in detail for detail in primary.supporting_details))

    def test_regression_3_botpsy_evaluated_on_evidence(self):
        """
        TEST 3: BOTPSY
        Article: BotPsy: On-Device Personal Assistant with a Sub-Billion-Parameter Agentic Language Model.
        Expected: Not automatically considered a major AI breakthrough; checked for independent benchmarks,
        technical novelty; promotional claims not repeated; classified as routine release if lacking benchmarks;
        does not qualify for Breaking Developments.
        """
        botpsy = Article(
            title="BotPsy: On-Device Personal Assistant with a Sub-Billion-Parameter Agentic Language Model",
            url="https://huggingface.co/blog/qvac/botpsy",
            source="Hugging Face Blog",
            published=self.now - timedelta(hours=3),
            summary="We present BotPsy, an on-device personal assistant powered by a sub-billion parameter agentic language model designed for local task assistance and fast function calling.",
            content="BotPsy runs locally on personal devices. It aims to assist users with daily routine workflows and function calling.",
            source_category="AI",
        )
        editorial = load_json("config/editorial.json")
        registry = load_json("config/sources.json")
        ranked = rank_articles([botpsy], self.now, 36, registry, editorial)
        self.assertNotEqual(botpsy.breakthrough_level, "Major breakthrough")
        self.assertEqual(botpsy.breakthrough_level, "Routine release or announcement")
        if botpsy.editorial_category == "Breaking Developments":
            self.assertTrue(any("Routine release or announcement" in r for r in botpsy.rejection_reasons))

    def test_regression_4_nvidia_agent_substrate_focuses_on_kubernetes(self):
        """
        TEST 4: NVIDIA AGENT SUBSTRATE
        Article: Agent Substrate: Advancing Kubernetes for Agents at Scale.
        Expected: Selected as Technical Intelligence; explanation focuses on agent infrastructure,
        Kubernetes orchestration, scheduling, execution environments; does not generate generic
        GPU optimization claims unless discussed in source.
        """
        nvidia_substrate = Article(
            title="Agent Substrate: Advancing Kubernetes for Agents at Scale",
            url="https://developer.nvidia.com/blog/agent-substrate-advancing-kubernetes-for-agents-at-scale/",
            source="NVIDIA Technical Blog",
            published=self.now - timedelta(hours=4),
            summary="NVIDIA introduces Agent Substrate, an architectural framework that adapts Kubernetes for orchestrating multi-agent systems, container lifecycle scheduling, and low-latency sandboxing.",
            content="Agent Substrate extends Kubernetes with specialized controllers to manage agent memory, tool execution environments, and checkpointing for stateful agent workflows at scale.",
            source_category="Engineering",
        )
        editorial = load_json("config/editorial.json")
        registry = load_json("config/sources.json")
        ranked = rank_articles([nvidia_substrate], self.now, 36, registry, editorial)
        self.assertEqual(nvidia_substrate.editorial_category, "Technical Intelligence")
        analysis = nvidia_substrate.analysis
        self.assertIn("Kubernetes", analysis["what_was_built_or_discovered"])
        self.assertIn("Kubernetes", analysis["how_it_works"] + analysis["why_it_matters"] + analysis["engineering_takeaway"])
        self.assertNotIn("optimizing large-scale compute workloads", analysis["why_it_matters"].lower())

    def test_regression_5_angular_compiler_no_gpu_claims(self):
        """
        TEST 5: ANGULAR COMPILER
        Article: Angular's TypeScript 7-Powered Compiler Enters Beta with Rust-Based Preprocessor.
        Expected: Selected as Technical Intelligence; explanation focuses on Angular's compiler architecture,
        Rust-based preprocessing, and TypeScript 7 compatibility; does not mention GPU workloads or
        unrelated infrastructure implications.
        """
        angular = Article(
            title="Angular's TypeScript 7-Powered Compiler Enters Beta with Rust-Based Preprocessor",
            url="https://www.infoq.com/news/2026/11/angular-compiler-rust-typescript/",
            source="InfoQ",
            published=self.now - timedelta(hours=3),
            summary="The Angular team has shared details of a new version of its compiler, the Angular Preprocessor (ngp), which rewrites the analysis stage in Rust to enable TypeScript 7 adoption.",
            content="Microsoft removed the ts.Transformer API in TypeScript 7, prompting Angular engineers to build ngp in Rust. The Rust preprocessor parses templates and decorators ahead of time.",
            source_category="Engineering",
        )
        editorial = load_json("config/editorial.json")
        registry = load_json("config/sources.json")
        ranked = rank_articles([angular], self.now, 36, registry, editorial)
        self.assertEqual(angular.editorial_category, "Technical Intelligence")
        analysis = angular.analysis
        self.assertIn("Rust", analysis["what_was_built_or_discovered"])
        self.assertIn("TypeScript 7", analysis["what_was_built_or_discovered"])
        combined_explanation = " ".join(analysis.values()).lower()
        self.assertNotIn("gpu", combined_explanation)
        self.assertNotIn("cuda", combined_explanation)
        self.assertNotIn("ml engineers", combined_explanation)

    def test_regression_6_promotional_content_handling(self):
        """
        TEST 6: PROMOTIONAL CONTENT
        Synthetic and previous promotional posts rejected; company engineering blogs accepted.
        """
        rystic = Article(
            title="Show HN: Rystic – Stateful Local Simulators of Slack, GitHub and Linear",
            url="https://www.rystic.ai/",
            source="rystic.ai",
            published=self.now - timedelta(hours=2),
            summary="Try now for free. Book a demo today. Seed any state you need.",
            is_show_hn=True,
            source_category="Community",
        )
        meta_tlx = Article(
            title="How We Built TLX: A Hardware-Native GPU Compiler for Production ML at Scale",
            url="https://engineering.fb.com/2026/11/10/open-source/tlx-a-hardware-native-gpu-compiler-for-production-ml/",
            source="Meta Engineering",
            published=self.now - timedelta(hours=3),
            summary="Meta engineers detail Triton Low-Level Extensions (TLX) for GPU warp orchestration in production ML.",
            content="We built TLX to optimize warp specialization and asynchronous memory copies in production model training.",
            source_category="Engineering",
        )
        editorial = load_json("config/editorial.json")
        registry = load_json("config/sources.json")
        ranked = rank_articles([rystic, meta_tlx], self.now, 36, registry, editorial)
        self.assertTrue(rystic.rejection_reasons)
        self.assertEqual(meta_tlx.rejection_reasons, [])

    def test_regression_7_article_specific_explanations_differ_across_unrelated_articles(self):
        """
        TEST 7: ARTICLE-SPECIFIC EXPLANATIONS
        Unrelated articles (AI infrastructure, compiler, database, zero-day) must receive
        genuinely different explanations, detecting and preventing generic boilerplate reuse.
        """
        articles = [
            Article("Agent Substrate: Advancing Kubernetes for Agents at Scale", "https://nvidia.com/agent", "NVIDIA Technical Blog", self.now - timedelta(hours=2), summary="NVIDIA built Agent Substrate for Kubernetes agent orchestration.", content="Kubernetes controllers manage agent memory and lifecycle.", source_category="Engineering"),
            Article("Angular's TypeScript 7-Powered Compiler Enters Beta with Rust-Based Preprocessor", "https://infoq.com/angular", "InfoQ", self.now - timedelta(hours=3), summary="Angular preprocessor rewritten in Rust for TypeScript 7.", content="Rust preprocessor replaces ts.Transformer API.", source_category="Engineering"),
            Article("PostgreSQL 17 Released with Advanced Query Execution and SIMD Support", "https://postgresql.org/pg17", "PostgreSQL", self.now - timedelta(hours=4), summary="PostgreSQL 17 optimizes query planning and memory allocation with SIMD.", content="SIMD vectorization accelerates memory operations.", source_category="Databases"),
            Article("Critical Chrome zero-day vulnerability patched by Google", "https://bleepingcomputer.com/chrome", "BleepingComputer", self.now - timedelta(hours=1), summary="Google patched an actively exploited Chrome zero-day flaw CVE-2026-125350.", content="Zero-day memory corruption flaw in XML parser exploited.", story_ids=("CVE-2026-125350",), source_category="Security"),
        ]
        editorial = load_json("config/editorial.json")
        registry = load_json("config/sources.json")
        ranked = rank_articles(articles, self.now, 36, registry, editorial)

        why_it_matters_set = {a.analysis["why_it_matters"] for a in ranked}
        self.assertEqual(len(why_it_matters_set), 4)
        banned_string = "bridges the gap between high-level developer productivity and hardware-native execution efficiency"
        for a in ranked:
            self.assertNotIn(banned_string, a.analysis["why_it_matters"].lower())

    def test_regression_8_insufficient_news_publishes_only_qualifying_stories(self):
        """
        TEST 8: INSUFFICIENT NEWS
        Provide a set containing only two high-quality qualifying stories and several low-quality articles.
        Expected: The bot publishes only the two qualifying stories.
        """
        from newsbot.reports import select_balanced_articles
        qualifying1 = Article("Critical zero-day actively exploited in enterprise hypervisor", "https://cisa.gov/cve", "CISA Advisories", self.now - timedelta(hours=1), summary="CISA adds an actively exploited critical zero-day vulnerability to the KEV catalog, requiring immediate emergency patching across all federal agencies and enterprise networks.", content="CISA has issued an emergency directive regarding an active zero-day exploit targeting virtualization hypervisors. Organizations operating affected platforms must apply security updates immediately.", score=80.0, editorial_category="Breaking Developments", category="Cybersecurity")
        qualifying2 = Article("How Meta Built TLX: Hardware-Native GPU Compiler", "https://engineering.fb.com/tlx", "Meta Engineering", self.now - timedelta(hours=2), summary="Meta details Triton Low-Level Extensions (TLX), a hardware-native GPU compiler used in production ML infrastructure to optimize warp specialization and memory throughput.", content="We built TLX to give ML engineers fine-grained control over GPU hardware while preserving Triton's productive Python syntax for large-scale model pretraining.", score=78.0, editorial_category="Technical Intelligence", category="AI")
        weak1 = Article("Minor blog typo fix", "https://example.com/1", "Blog", self.now - timedelta(hours=3), summary="Fixed a typo in the documentation.", score=30.0, editorial_category="Technical Intelligence", category="Software Engineering")
        weak2 = Article("Generic marketing webinar next week", "https://example.com/2", "Marketing", self.now - timedelta(hours=4), summary="Attend our free webinar on cloud tools.", score=25.0, editorial_category="Breaking Developments", category="Industry Technology")

        selected, rejected = select_balanced_articles([qualifying1, qualifying2, weak1, weak2], max_articles=5, min_score=48.0)
        self.assertEqual(len(selected), 2)
        self.assertIn(qualifying1, selected)
        self.assertIn(qualifying2, selected)
        self.assertNotIn(weak1, selected)
        self.assertNotIn(weak2, selected)

    def test_regression_9_event_deduplication_consolidates_multi_source_reports(self):
        """
        TEST 9: EVENT DEDUPLICATION
        Provide multiple articles from different publications reporting the same underlying announcement.
        Expected: The bot selects one consolidated story, using additional sources only when they contribute useful information.
        """
        source_a = Article(
            title="Google patches eighth Chrome zero-day exploited in attacks this year",
            url="https://bleepingcomputer.com/news/security/chrome-patch",
            source="BleepingComputer",
            published=self.now - timedelta(hours=3),
            summary="Google patched CVE-2026-125350, an actively exploited Chrome zero-day.",
            content="Google is aware of reports that an exploit for CVE-2026-125350 exists in the wild.",
            story_ids=("CVE-2026-125350",),
            source_category="Security",
        )
        source_b = Article(
            title="Chrome Emergency Security Advisory Issued Following In-The-Wild Exploits",
            url="https://thehackernews.com/chrome-update",
            source="The Hacker News",
            published=self.now - timedelta(hours=2),
            summary="Emergency Chrome security update resolves actively targeted vulnerability CVE-2026-125350.",
            content="The Hacker News reports Google issued an emergency patch for CVE-2026-125350.",
            story_ids=("CVE-2026-125350",),
            source_category="Security",
        )
        source_c = Article(
            title="Eighth Zero-Day Flaw of 2026 Patched in Google Chrome Browser",
            url="https://securityweek.com/chrome-advisory",
            source="SecurityWeek",
            published=self.now - timedelta(hours=1),
            summary="SecurityWeek details emergency security patches for Chrome zero-day CVE-2026-125350.",
            content="SecurityWeek coverage of Google Chrome zero-day patch.",
            story_ids=("CVE-2026-125350",),
            source_category="Security",
        )
        editorial = load_json("config/editorial.json")
        registry = load_json("config/sources.json")
        clustered = rank_articles([source_a, source_b, source_c], self.now, 36, registry, editorial)
        self.assertEqual(len(clustered), 1)
        primary = clustered[0]
        self.assertIn("BleepingComputer", primary.sources)
        self.assertIn("The Hacker News", primary.sources)
        self.assertIn("SecurityWeek", primary.sources)


if __name__ == "__main__":
    unittest.main()
