"""
Descasio Market Intelligence — Pipeline Orchestrator
Main entry point for the autonomous market intelligence system.

Execution modes:
  --mode sales          Run the 6-hour sales intelligence cycle once
  --mode exec           Run the monthly executive briefing once
  --mode scheduled      Start the scheduler (runs indefinitely — for EC2/ECS)
  --mode validate       Validate credentials and configuration
  --mode setup-zoho     Verify Zoho CRM module configuration

Typical production deployment: AWS Lambda + EventBridge, using lambda_handler.py
For local testing or EC2: python -m orchestrator.pipeline --mode scheduled
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime

import schedule
import time

from config.settings import Config
from ingestion.scrapers import (
    ProcurementScraper,
    NewsScraper,
    CompetitorMonitor,
    RegulatoryTracker,
)
from ingestion.hyperscaler_tracker import HyperscalerTracker
from processing.ai_synthesizer import AISynthesizer, SignalClassifier
from delivery.zoho_crm import ZohoCRMClient
from delivery.alerts import SlackAlerter, BriefingComposer

# ─── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("descasio.pipeline")


class DescasioIntelPipeline:
    """
    Orchestrates the complete Market Intelligence pipeline:

    SALES CYCLE (every 6 hours):
    ┌─────────────────────────────────────────────────────────────────┐
    │ Procurement Scraper → News Scraper → Competitor Monitor         │
    │              ↓                                                  │
    │         AI Synthesizer (sales mode)                             │
    │              ↓                                                  │
    │         Signal Classifier (filter noise)                        │
    │              ↓                                                  │
    │   Zoho CRM (create record) + Slack (post alert) [parallel]      │
    └─────────────────────────────────────────────────────────────────┘

    EXEC CYCLE (1st of each month):
    ┌─────────────────────────────────────────────────────────────────┐
    │ All scrapers (30-day lookback) + Regulatory Tracker             │
    │              ↓                                                  │
    │         AI Synthesizer (executive mode)                         │
    │              ↓                                                  │
    │   Email briefing (AWS SES) + Slack exec notification            │
    └─────────────────────────────────────────────────────────────────┘
    """

    def __init__(self, config: Config):
        self.config = config
        self.procurement = ProcurementScraper(config)
        self.news = NewsScraper(config)
        self.competitors = CompetitorMonitor(config)
        self.regulatory = RegulatoryTracker(config)
        self.hyperscalers = HyperscalerTracker(config)
        self.synthesizer = AISynthesizer(config)
        self.classifier = SignalClassifier()
        self.zoho = ZohoCRMClient(config)
        self.slack = SlackAlerter(config)
        self.briefer = BriefingComposer(config)

    # ─── SALES CYCLE ──────────────────────────────────────────────────────────

    async def run_sales_cycle(self) -> int:
        """
        Run a complete sales intelligence scan across all priority markets.
        Returns the number of high-value signals delivered.
        """
        cycle_start = datetime.utcnow()
        logger.info(
            f"\n{'='*60}\n"
            f"SALES INTEL CYCLE — {cycle_start.strftime('%d %b %Y %H:%M UTC')}\n"
            f"Markets: {', '.join(self.config.priority_markets)}\n"
            f"{'='*60}"
        )

        raw_signals = []

        # ── Ingestion (all markets, concurrent) ────────────────────────────
        logger.info("Phase 1: Ingestion")

        procurement_tasks = [
            self.procurement.fetch_tenders(
                country, days_back=self.config.lookback_days_sales
            )
            for country in self.config.priority_markets
        ]
        procurement_results = await asyncio.gather(*procurement_tasks)
        for country, result in zip(self.config.priority_markets, procurement_results):
            count = len(result)
            logger.info(f"  Procurement [{country}]: {count} signals")
            raw_signals.extend(result)

        # News (all markets combined)
        news_signals = await self.news.fetch_recent(
            hours=self.config.sales_cycle_hours
        )
        logger.info(f"  News: {len(news_signals)} signals")
        raw_signals.extend(news_signals)

        # Competitor monitoring
        comp_signals = await self.competitors.scan_all()
        logger.info(f"  Competitors: {len(comp_signals)} signals")
        raw_signals.extend(comp_signals)

        logger.info(f"  Total raw signals: {len(raw_signals)}")

        if not raw_signals:
            logger.info("No raw signals this cycle. Nothing to process.")
            return 0

        # ── AI Synthesis ───────────────────────────────────────────────────
        logger.info("Phase 2: AI Synthesis")
        synthesized = await self.synthesizer.process_batch(
            raw_signals, mode="sales", generate_battlecards=True
        )
        logger.info(f"  Synthesized: {len(synthesized)} structured signals")

        # ── Classification + Routing ───────────────────────────────────────
        sales_signals = self.classifier.filter_for_sales(synthesized)
        logger.info(f"  After classification: {len(sales_signals)} signals for Sales")

        # ── Delivery ────────────────────────────────────────────────────────
        logger.info("Phase 3: Delivery")
        delivered = 0

        for signal in sales_signals:
            # Push to Zoho CRM
            zoho_id = self.zoho.create_intelligence_record(signal)

            # Enrich matching open Deals
            matching_deals = self.zoho.find_matching_deals(
                signal.get("country", ""), signal.get("descasio_service_line", "")
            )
            for deal in matching_deals[:3]:  # Cap at 3 enrichments per signal
                self.zoho.enrich_deal_with_intel(deal.get("id"), signal)

            # Slack alert (URGENT and HIGH priority get individual alerts)
            if signal.get("priority") in {"URGENT", "HIGH"}:
                self.slack.post_sales_alert(signal)

            delivered += 1

        # Post cycle summary to Slack
        self.slack.post_sales_cycle_summary(sales_signals)

        duration = (datetime.utcnow() - cycle_start).total_seconds()
        logger.info(
            f"\n{'='*60}\n"
            f"CYCLE COMPLETE — {delivered} signals delivered in {duration:.1f}s\n"
            f"{'='*60}\n"
        )
        return delivered

    # ─── EXECUTIVE CYCLE ──────────────────────────────────────────────────────

    async def run_exec_briefing(self) -> bool:
        """
        Generate and deliver the monthly C-Suite Strategic Intelligence Brief.
        Aggregates 30 days of signals for trend-level synthesis.
        """
        briefing_start = datetime.utcnow()
        logger.info(
            f"\n{'='*60}\n"
            f"MONTHLY EXEC BRIEFING — {briefing_start.strftime('%B %Y')}\n"
            f"{'='*60}"
        )

        raw_signals = []

        # Full 30-day lookback across all sources
        procurement_tasks = [
            self.procurement.fetch_tenders(
                country, days_back=self.config.lookback_days_exec
            )
            for country in self.config.priority_markets
        ]
        procurement_results = await asyncio.gather(*procurement_tasks)
        for result in procurement_results:
            raw_signals.extend(result)

        news_signals = await self.news.fetch_recent(hours=720)  # 30 days
        raw_signals.extend(news_signals)

        comp_signals = await self.competitors.scan_all()
        raw_signals.extend(comp_signals)

        # Hyperscaler expansion signals (exec cycle only — strategic framing)
        hyper_signals = await self.hyperscalers.scan_all(hours=720)
        raw_signals.extend(hyper_signals)
        logger.info(f"  Hyperscaler signals: {len(hyper_signals)}")

        reg_tasks = [
            self.regulatory.fetch_updates(
                country, days_back=self.config.lookback_days_exec
            )
            for country in self.config.priority_markets
        ]
        reg_results = await asyncio.gather(*reg_tasks)
        for result in reg_results:
            raw_signals.extend(result)

        logger.info(f"Total raw signals for exec briefing: {len(raw_signals)}")

        # AI Synthesis in executive mode
        briefing_list = await self.synthesizer.process_batch(
            raw_signals, mode="executive"
        )

        if not briefing_list:
            logger.error("Executive synthesis returned empty. Check AI Synthesizer logs.")
            return False

        briefing = briefing_list[0]

        # Deliver: Email + Slack
        email_ok = self.briefer.send_exec_email(briefing)
        slack_ok = self.slack.post_exec_summary(briefing)

        logger.info(
            f"Exec briefing complete — Email: {'✓' if email_ok else '✗'}, "
            f"Slack: {'✓' if slack_ok else '✗'}"
        )
        return email_ok or slack_ok

    # ─── UTILITIES ────────────────────────────────────────────────────────────

    async def validate(self) -> bool:
        """Check that all credentials and dependencies are configured."""
        logger.info("Validating configuration...")
        missing = self.config.validate()
        if missing:
            for var in missing:
                logger.error(f"  Missing: {var}")
            logger.error(
                f"\n{len(missing)} environment variable(s) not configured. "
                "See .env.example for required variables."
            )
            return False

        logger.info("  All required credentials present ✓")

        # Test Zoho connection
        try:
            fields = self.zoho.get_module_fields()
            logger.info(f"  Zoho CRM: Connected ✓ ({len(fields)} fields in Market_Intelligence module)")
        except Exception as e:
            logger.warning(f"  Zoho CRM: Connection test failed — {e}")

        logger.info("Validation complete.")
        return True

    def run_scheduled(self):
        """
        Start the continuous scheduler. Intended for EC2/ECS deployment.
        For Lambda, use lambda_handler.py instead.
        """
        logger.info(
            f"\n{'='*60}\n"
            f"DESCASIO INTELLIGENCE HUB — STARTING\n"
            f"Markets: {', '.join(self.config.priority_markets)}\n"
            f"Sales cycle: every {self.config.sales_cycle_hours}h\n"
            f"Exec briefing: day {self.config.exec_briefing_day} of each month\n"
            f"{'='*60}\n"
        )

        # Sales cycle — every N hours
        schedule.every(self.config.sales_cycle_hours).hours.do(
            lambda: asyncio.run(self.run_sales_cycle())
        )

        # Exec briefing — runs daily at 06:00 WAT (05:00 UTC), executes briefing on day 1
        schedule.every().day.at("05:00").do(
            lambda: asyncio.run(self.run_exec_briefing())
            if datetime.utcnow().day == self.config.exec_briefing_day
            else None
        )

        # Immediate first run on startup
        logger.info("Running initial sales cycle on startup...")
        asyncio.run(self.run_sales_cycle())

        # Loop
        while True:
            schedule.run_pending()
            time.sleep(60)


# ─── CLI Entry Point ──────────────────────────────────────────────────────────

def main():
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Descasio Market Intelligence Pipeline"
    )
    parser.add_argument(
        "--mode",
        choices=["sales", "exec", "scheduled", "validate"],
        default="validate",
        help="Pipeline execution mode",
    )
    args = parser.parse_args()

    config = Config()
    pipeline = DescasioIntelPipeline(config)

    if args.mode == "validate":
        success = asyncio.run(pipeline.validate())
        sys.exit(0 if success else 1)

    elif args.mode == "sales":
        count = asyncio.run(pipeline.run_sales_cycle())
        logger.info(f"Sales cycle complete. {count} signals delivered.")

    elif args.mode == "exec":
        success = asyncio.run(pipeline.run_exec_briefing())
        sys.exit(0 if success else 1)

    elif args.mode == "scheduled":
        pipeline.run_scheduled()


if __name__ == "__main__":
    main()
