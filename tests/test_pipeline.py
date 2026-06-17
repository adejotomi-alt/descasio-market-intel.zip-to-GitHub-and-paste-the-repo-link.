"""
Descasio Market Intelligence — Pipeline Smoke Tests
Validates that all pipeline components are correctly wired and functional.

Run with: python -m pytest tests/test_pipeline.py -v

These are integration smoke tests, NOT unit tests. They require real credentials
to be present in .env. Tests are written to be cheap on API calls — they use
minimal payloads and short timeouts. Mark heavy tests with @pytest.mark.slow
to exclude from fast CI runs.

Test categories:
  CONFIG   — Environment variables and Config object
  SCRAPER  — Web scraping connectivity (hits real URLs, validates parsing)
  AI       — Claude API connectivity and schema validation
  ZOHO     — Zoho CRM OAuth and module configuration
  PIPELINE — End-to-end mini-cycle with one signal
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List

import pytest

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import Config, PROCUREMENT_SOURCES, NEWS_SOURCES, REGULATORY_SOURCES
from ingestion.scrapers import ProcurementScraper, NewsScraper, CompetitorMonitor, RegulatoryTracker
from ingestion.hyperscaler_tracker import HyperscalerTracker, KNOWN_HYPERSCALER_PRESENCE
from processing.ai_synthesizer import AISynthesizer, SignalClassifier


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def config():
    """Load config from environment. All tests that need real credentials use this."""
    from dotenv import load_dotenv
    load_dotenv()
    return Config()


@pytest.fixture(scope="session")
def sample_raw_signals() -> List[Dict]:
    """A small batch of synthetic raw signals for AI synthesis tests."""
    return [
        {
            "id": "test001",
            "signal_type": "tender",
            "country": "Nigeria",
            "title": "CBN Cloud Infrastructure Modernisation — Test Signal",
            "body": "Test tender for cloud migration services in Nigeria's banking sector. Keywords: cloud, managed services, data sovereignty.",
            "source": "Test Source",
            "url": "https://example.com/tender/001",
            "scraped_at": "2026-06-01T10:00:00Z",
            "metadata": {"portal": "BPP Nigeria", "deadline_raw": "30/06/2026"},
        },
        {
            "id": "test002",
            "signal_type": "competitor_move",
            "country": "Kenya",
            "title": "Liquid Technologies — 8 cloud architect roles posted in Nairobi",
            "body": "Competitor hiring surge: 8 AWS and Azure architect positions posted in Nairobi. Threat level: HIGH.",
            "source": "Competitor Monitor",
            "url": "https://liquid.tech/careers",
            "scraped_at": "2026-06-01T10:00:00Z",
            "metadata": {"competitor": "Liquid Intelligent Technologies", "threat_level": "HIGH"},
        },
    ]


# ─── CONFIG TESTS ─────────────────────────────────────────────────────────────

class TestConfig:
    def test_config_instantiates(self):
        """Config object should instantiate without errors."""
        c = Config()
        assert c is not None
        assert isinstance(c.priority_markets, list)
        assert len(c.priority_markets) == 4

    def test_priority_markets(self):
        """All four target markets should be configured."""
        c = Config()
        assert "Nigeria" in c.priority_markets
        assert "Kenya" in c.priority_markets
        assert "Ghana" in c.priority_markets
        assert "Uganda" in c.priority_markets

    def test_company_context_not_empty(self):
        """Company context must be present — it's injected into every AI prompt."""
        c = Config()
        assert len(c.company_context) > 100
        assert "Descasio" in c.company_context
        assert "AWS" in c.company_context

    def test_validate_with_no_env(self):
        """validate() should return missing keys when env vars aren't set."""
        c = Config(
            anthropic_api_key="",
            zoho_client_id="",
            zoho_client_secret="",
            zoho_refresh_token="",
            slack_sales_webhook="",
        )
        missing = c.validate()
        assert len(missing) > 0
        assert "ANTHROPIC_API_KEY" in missing

    def test_validate_passes_with_credentials(self, config):
        """validate() should return empty list when all required env vars are present."""
        missing = config.validate()
        if missing:
            pytest.skip(f"Credentials not configured: {missing}")
        assert missing == []

    def test_source_configs_not_empty(self):
        """All data source configurations should be populated."""
        assert PROCUREMENT_SOURCES, "PROCUREMENT_SOURCES is empty"
        assert NEWS_SOURCES, "NEWS_SOURCES is empty"
        assert REGULATORY_SOURCES, "REGULATORY_SOURCES is empty"
        for country in ["Nigeria", "Kenya", "Ghana", "Uganda"]:
            assert country in PROCUREMENT_SOURCES, f"No procurement sources for {country}"
            assert country in REGULATORY_SOURCES, f"No regulatory sources for {country}"


# ─── SCRAPER TESTS ─────────────────────────────────────────────────────────────

class TestScrapers:
    @pytest.mark.slow
    def test_news_scraper_rss(self, config):
        """News scraper should successfully parse at least one RSS feed."""
        scraper = NewsScraper(config)
        signals = asyncio.run(scraper.fetch_recent(hours=72))
        # We don't assert count > 0 because RSS might be empty — just assert no crash
        assert isinstance(signals, list)
        for s in signals:
            assert "signal_type" in s
            assert s["signal_type"] == "market_news"
            assert "title" in s
            assert "country" in s

    @pytest.mark.slow
    def test_hyperscaler_presence_snapshot(self, config):
        """Hyperscaler tracker should return a complete presence snapshot."""
        tracker = HyperscalerTracker(config)
        snapshot = tracker.get_presence_snapshot()
        assert len(snapshot) > 0
        hyperscalers = {s["hyperscaler"] for s in snapshot}
        assert "AWS" in hyperscalers
        assert "Microsoft Azure" in hyperscalers
        assert "Google Cloud" in hyperscalers
        for s in snapshot:
            assert "status" in s
            assert s["status"] in {"LIVE", "ANNOUNCED", "PLANNED", "RUMOURED"}
            assert "descasio_relevance" in s

    def test_signal_deduplication(self, config):
        """The _make_raw_signal function should deduplicate identical signals."""
        from ingestion.scrapers import _make_raw_signal, _SEEN_HASHES
        initial_count = len(_SEEN_HASHES)
        s1 = _make_raw_signal("tender", "Nigeria", "Test Title ABC", "body", "source", "http://test.com")
        s2 = _make_raw_signal("tender", "Nigeria", "Test Title ABC", "body", "source", "http://test.com")
        assert s1 is not None, "First signal should be created"
        assert s2 is None, "Duplicate signal should return None"
        assert len(_SEEN_HASHES) == initial_count + 1

    def test_tender_keyword_relevance(self):
        """Keyword filter should correctly identify relevant vs. irrelevant content."""
        from ingestion.scrapers import _is_relevant
        assert _is_relevant("Supply of cloud migration services for CBN") is True
        assert _is_relevant("Supply of office furniture for FCT authority") is False
        assert _is_relevant("AI-powered data sovereignty solution required") is True
        assert _is_relevant("Procurement of office stationery items") is False


# ─── AI SYNTHESIZER TESTS ─────────────────────────────────────────────────────

class TestAISynthesizer:
    @pytest.mark.slow
    def test_synthesizer_connectivity(self, config):
        """Claude API should respond with valid JSON for a single signal batch."""
        if not config.anthropic_api_key:
            pytest.skip("ANTHROPIC_API_KEY not set")

        synth = AISynthesizer(config)
        signals = [
            {
                "id": "smoke001",
                "signal_type": "tender",
                "country": "Nigeria",
                "title": "Test: CBN cloud services tender",
                "body": "Central Bank of Nigeria is seeking cloud infrastructure services including migration and managed services. Budget: ₦500M.",
                "source": "BPP Nigeria",
                "scraped_at": "2026-06-01T10:00:00Z",
                "metadata": {},
            }
        ]
        result = asyncio.run(synth.process_batch(signals, mode="sales"))
        assert isinstance(result, list)

    @pytest.mark.slow
    def test_synthesizer_output_schema(self, config, sample_raw_signals):
        """AI output should conform to the expected signal schema."""
        if not config.anthropic_api_key:
            pytest.skip("ANTHROPIC_API_KEY not set")

        synth = AISynthesizer(config)
        results = asyncio.run(synth.process_batch(sample_raw_signals, mode="sales"))

        assert isinstance(results, list)
        for signal in results:
            assert "title" in signal, "Signal must have a title"
            assert "priority" in signal, "Signal must have a priority"
            assert signal["priority"] in {"URGENT", "HIGH", "MEDIUM", "LOW"}, \
                f"Invalid priority: {signal['priority']}"
            assert "signal_type" in signal, "Signal must have a signal_type"
            assert "country" in signal, "Signal must have a country"
            assert "summary" in signal, "Signal must have a summary"
            assert isinstance(signal.get("battlecard_points", []), list), \
                "battlecard_points must be a list"

    def test_signal_classifier_logic(self):
        """SignalClassifier should correctly route signals to Sales vs. Exec."""
        classifier = SignalClassifier()
        signals = [
            {"signal_type": "tender", "priority": "URGENT", "relevance_score": 8},
            {"signal_type": "tender", "priority": "HIGH", "relevance_score": 7},
            {"signal_type": "regulatory", "priority": "HIGH", "relevance_score": 7},
            {"signal_type": "market_news", "priority": "MEDIUM", "relevance_score": 4},
            {"signal_type": "tender", "priority": "LOW", "relevance_score": 2},
        ]
        sales = classifier.filter_for_sales(signals)
        exec_ = classifier.filter_for_exec(signals)

        # Sales: tenders and competitor moves with relevance >= 5 and not LOW priority
        assert len(sales) == 2
        for s in sales:
            assert s["signal_type"] in {"tender", "competitor_move"}
            assert s["priority"] != "LOW"

        # Exec: regulatory and news with relevance >= 6
        assert len(exec_) == 1
        assert exec_[0]["signal_type"] == "regulatory"

    def test_safe_json_parser(self):
        """_safe_parse_json should handle markdown-fenced JSON responses."""
        synth_class = AISynthesizer
        raw = '```json\n{"signals": [{"id": "abc123", "title": "Test"}]}\n```'
        result = synth_class._safe_parse_json(raw)
        assert result == {"signals": [{"id": "abc123", "title": "Test"}]}

        raw_no_fence = '{"signals": []}'
        result2 = synth_class._safe_parse_json(raw_no_fence)
        assert result2 == {"signals": []}

        raw_with_preamble = 'Here is the JSON:\n\n{"signals": [{"id": "xyz"}]}'
        result3 = synth_class._safe_parse_json(raw_with_preamble)
        assert result3 == {"signals": [{"id": "xyz"}]}


# ─── ZOHO INTEGRATION TESTS ───────────────────────────────────────────────────

class TestZohoCRM:
    def test_zoho_credentials_check(self, config):
        """Zoho credentials validation should work correctly."""
        from delivery.zoho_crm import ZohoCRMClient
        client = ZohoCRMClient(config)
        # Just checks the _validate_credentials method — no API call
        if config.zoho_client_id and config.zoho_client_secret and config.zoho_refresh_token:
            assert client._validate_credentials() is True
        else:
            assert client._validate_credentials() is False

    def test_zoho_date_parser(self):
        """Zoho date parser should handle common date formats."""
        from delivery.zoho_crm import ZohoCRMClient
        assert ZohoCRMClient._parse_deadline("30/06/2026") == "2026-06-30"
        assert ZohoCRMClient._parse_deadline("2026-06-30") == "2026-06-30"
        assert ZohoCRMClient._parse_deadline("30 Jun 2026") == "2026-06-30"
        assert ZohoCRMClient._parse_deadline("not-a-date") is None

    @pytest.mark.slow
    def test_zoho_module_verification(self, config):
        """Zoho module should have all required fields configured."""
        if not all([config.zoho_client_id, config.zoho_client_secret, config.zoho_refresh_token]):
            pytest.skip("Zoho credentials not configured")

        from delivery.zoho_crm import ZohoCRMClient, REQUIRED_FIELDS
        client = ZohoCRMClient(config)
        fields = client.get_module_fields()
        missing = [f for f in REQUIRED_FIELDS if f not in fields]
        assert not missing, (
            f"Market_Intelligence module missing fields: {missing}\n"
            "Run the Zoho module setup steps from README.md"
        )


# ─── END-TO-END PIPELINE TESTS ────────────────────────────────────────────────

class TestPipeline:
    @pytest.mark.slow
    def test_pipeline_validate_mode(self, config):
        """Pipeline validate mode should run without throwing errors."""
        from orchestrator.pipeline import DescasioIntelPipeline
        pipeline = DescasioIntelPipeline(config)
        result = asyncio.run(pipeline.validate())
        # validate() returns True if all creds present, False if missing
        missing = config.validate()
        expected = len(missing) == 0
        assert result == expected

    @pytest.mark.slow
    @pytest.mark.integration
    def test_mini_sales_cycle(self, config):
        """
        Full mini sales cycle: one country, AI synthesis, classification.
        Does NOT push to Zoho or Slack — checks the pipeline logic only.
        """
        if not config.anthropic_api_key:
            pytest.skip("ANTHROPIC_API_KEY required for end-to-end test")

        from ingestion.scrapers import NewsScraper
        from processing.ai_synthesizer import AISynthesizer, SignalClassifier

        news = NewsScraper(config)
        synth = AISynthesizer(config)
        classifier = SignalClassifier()

        # Fetch recent news
        raw = asyncio.run(news.fetch_recent(hours=48))
        if not raw:
            pytest.skip("No news signals available in last 48h — retry")

        # Take just the first 3 signals to keep costs low
        mini_batch = raw[:3]

        # Synthesize
        synthesized = asyncio.run(synth.process_batch(mini_batch, mode="sales"))
        assert isinstance(synthesized, list)

        # Classify
        sales_signals = classifier.filter_for_sales(synthesized)
        assert isinstance(sales_signals, list)

        print(f"\nMini-cycle result: {len(raw)} raw → {len(synthesized)} synthesized → {len(sales_signals)} sales signals")


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Quick smoke test without pytest
    print("Running quick smoke tests (no API calls)...\n")

    tc = TestConfig()
    tc.test_config_instantiates()
    print("✓ Config instantiates")

    tc.test_priority_markets()
    print("✓ Priority markets configured")

    tc.test_company_context_not_empty()
    print("✓ Company context present")

    ts = TestScrapers()
    ts.test_signal_deduplication(None)
    print("✓ Signal deduplication works")

    ts.test_tender_keyword_relevance()
    print("✓ Keyword filter works")

    tai = TestAISynthesizer()
    tai.test_signal_classifier_logic()
    print("✓ Signal classifier routes correctly")

    tai.test_safe_json_parser()
    print("✓ JSON parser handles markdown fences")

    from delivery.zoho_crm import ZohoCRMClient
    assert ZohoCRMClient._parse_deadline("30/06/2026") == "2026-06-30"
    print("✓ Zoho date parser works")

    print("\n✓ All quick smoke tests passed.")
    print("Run 'pytest tests/test_pipeline.py -v -m slow' for full integration tests (requires credentials).")
