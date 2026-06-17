"""
Descasio Market Intelligence — Hyperscaler Tracker
Monitors AWS, Microsoft Azure, and Google Cloud expansion activities across Africa.

This is a distinct ingestion module because hyperscaler signals sit at the
intersection of THREAT and OPPORTUNITY for Descasio:

  OPPORTUNITY signals:
  - AWS announcing a Lagos Local Zone → Descasio (as ACP) can lead client migrations
  - GCP entering Kenya → expands the market, creates demand Descasio can serve
  - Azure new partner programme → Descasio can pursue dual-cloud positioning

  THREAT signals:
  - Azure direct-to-enterprise deal in Nigeria → bypasses Descasio's partner channel
  - AWS Building massive in-country team → reduces reliance on ACP partners for delivery
  - GCP + Safaricom partnership → local telco-hyperscaler bundle threatens Descasio's EA position

Four signal sources tracked per hyperscaler:
  1. Official blog RSS feeds (most reliable — infrastructure announcements, partnerships)
  2. Career page monitoring (leading indicator — hiring precedes expansion by 6–12 months)
  3. Press/news ingestion from pan-African tech media
  4. Infrastructure signals (data center permits, real estate filings — advanced/aspirational)
"""

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from config.settings import Config, HYPERSCALER_SOURCES

logger = logging.getLogger(__name__)

# Known hyperscaler infrastructure presence across Africa (as of build date)
# Updated by the AI synthesizer when new announcements are detected
KNOWN_HYPERSCALER_PRESENCE: Dict[str, Dict[str, Any]] = {
    "AWS": {
        "Africa (Cape Town)": {
            "status": "LIVE",
            "country": "South Africa",
            "type": "Full Region",
            "launched": "2020-04",
            "az_count": 3,
            "descasio_relevance": "MEDIUM",
            "notes": "Closest AWS region to West Africa. Used by Descasio for SA clients.",
        },
        "Lagos Local Zone": {
            "status": "ANNOUNCED",
            "country": "Nigeria",
            "type": "Local Zone",
            "launched": None,
            "descasio_relevance": "HIGH",
            "notes": "In-country compute — directly relevant to Descasio's Sovereign AI positioning.",
        },
        "Nairobi Edge": {
            "status": "PLANNED",
            "country": "Kenya",
            "type": "Edge Location",
            "launched": None,
            "descasio_relevance": "HIGH",
            "notes": "Supports KCB Group and KRA tender positioning for Descasio Kenya.",
        },
    },
    "Microsoft Azure": {
        "South Africa North (Johannesburg)": {
            "status": "LIVE",
            "country": "South Africa",
            "type": "Full Region",
            "launched": "2019-03",
            "az_count": 3,
            "descasio_relevance": "MEDIUM",
            "notes": "Primary Azure region for Africa. Not in Descasio's target markets directly.",
        },
        "South Africa West (Cape Town)": {
            "status": "LIVE",
            "country": "South Africa",
            "type": "Full Region (DR pair)",
            "launched": "2019-03",
            "az_count": 1,
            "descasio_relevance": "LOW",
        },
        "Sub-Saharan Expansion": {
            "status": "ANNOUNCED",
            "country": "Nigeria / Kenya",
            "type": "Edge Zones",
            "launched": None,
            "descasio_relevance": "HIGH",
            "notes": "Azure expansion into West/East Africa could intensify competition.",
        },
    },
    "Google Cloud": {
        "Africa South 1 (Johannesburg)": {
            "status": "LIVE",
            "country": "South Africa",
            "type": "Full Region",
            "launched": "2023-09",
            "az_count": 3,
            "descasio_relevance": "MEDIUM",
        },
        "Sub-Saharan Expansion Programme": {
            "status": "ANNOUNCED",
            "country": "Nigeria / Kenya / Ghana",
            "type": "Edge Zones / Partner Network",
            "launched": None,
            "descasio_relevance": "HIGH",
            "notes": "GCP + MTN partnership extends GCP reach into Descasio's key markets.",
        },
    },
}

# Keywords that indicate a significant hyperscaler Africa expansion signal
HYPERSCALER_AFRICA_KEYWORDS = [
    # Geographic
    "africa", "sub-saharan", "west africa", "east africa", "nigeria", "kenya",
    "ghana", "uganda", "lagos", "nairobi", "accra", "kampala",
    # Infrastructure
    "local zone", "edge location", "data center", "data centre", "region",
    "availability zone", "cloud region", "infrastructure",
    # Partnership / Business
    "partner", "reseller", "consulting partner", "advanced partner",
    "marketplace", "distribution", "channel",
    # Services (relevant to Descasio's lines)
    "sovereign", "data residency", "compliance", "managed services",
    "hybrid cloud", "outpost", "wavelength",
]

# Careers keywords that signal an Africa-focused hiring surge
HYPERSCALER_HIRING_KEYWORDS = [
    "africa", "nigeria", "kenya", "ghana", "lagos", "nairobi",
    "west africa", "east africa", "solutions architect", "partner manager",
    "country manager", "field sales", "enterprise sales", "channel",
    "technical account manager", "cloud architect",
]


class HyperscalerTracker:
    """
    Monitors AWS, Azure, and Google Cloud for Africa-specific expansion signals.

    Signals produced here feed into both:
    - The exec briefing (strategic hyperscaler dynamics section)
    - The sales cycle (partner opportunity or competitive threat alerts)

    Usage:
        tracker = HyperscalerTracker(config)
        signals = await tracker.scan_all(hours=24)
    """

    def __init__(self, config: Config):
        self.config = config
        self.headers = {"User-Agent": config.user_agent}

    async def scan_all(self, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Scan all three hyperscalers for recent Africa expansion signals.
        Returns a combined list of structured signals.
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        all_signals = []

        for hyperscaler_name, sources in HYPERSCALER_SOURCES.items():
            logger.info(f"  Scanning hyperscaler: {hyperscaler_name}")

            # Blog RSS
            blog_signals = await self._scan_rss(
                sources.get("blog_rss", ""), hyperscaler_name, cutoff
            )
            all_signals.extend(blog_signals)

            # Announcements / release notes RSS
            if "news_rss" in sources:
                news_signals = await self._scan_rss(
                    sources["news_rss"], hyperscaler_name, cutoff
                )
                all_signals.extend(news_signals)

            # Career page (leading indicator for expansion)
            career_signals = await self._scan_careers(hyperscaler_name, sources)
            all_signals.extend(career_signals)

            await asyncio.sleep(1.0)  # Polite delay between hyperscalers

        logger.info(f"Hyperscaler tracker: {len(all_signals)} signals detected")
        return all_signals

    def get_presence_snapshot(self) -> List[Dict[str, Any]]:
        """
        Return the known hyperscaler infrastructure presence across Africa.
        Used by the exec briefing to populate the Hyperscaler Expansion Map.
        """
        snapshot = []
        for vendor, nodes in KNOWN_HYPERSCALER_PRESENCE.items():
            for location, details in nodes.items():
                snapshot.append({
                    "hyperscaler": vendor,
                    "location": location,
                    "country": details.get("country", "Africa"),
                    "status": details.get("status", "UNKNOWN"),
                    "type": details.get("type", ""),
                    "descasio_relevance": details.get("descasio_relevance", "MEDIUM"),
                    "notes": details.get("notes", ""),
                    "launched": details.get("launched"),
                })
        return snapshot

    async def _scan_rss(
        self,
        rss_url: str,
        hyperscaler: str,
        cutoff: datetime,
    ) -> List[Dict[str, Any]]:
        """Parse a hyperscaler's RSS feed for Africa-relevant entries."""
        if not rss_url:
            return []

        loop = asyncio.get_event_loop()
        try:
            feed = await loop.run_in_executor(None, lambda: feedparser.parse(rss_url))
        except Exception as e:
            logger.warning(f"{hyperscaler} RSS parse failed ({rss_url}): {e}")
            return []

        signals = []
        africa_kws = HYPERSCALER_AFRICA_KEYWORDS

        for entry in feed.entries:
            # Recency filter
            published = entry.get("published_parsed")
            if published:
                pub_dt = datetime(*published[:6])
                if pub_dt < cutoff:
                    continue

            title = entry.get("title", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            full_text = f"{title} {BeautifulSoup(summary, 'lxml').get_text(' ', strip=True)}"

            if not self._is_africa_relevant(full_text, africa_kws):
                continue

            implication, detail = self._assess_implication(hyperscaler, full_text)
            content_hash = hashlib.md5(f"{hyperscaler}::{title}".encode()).hexdigest()

            signals.append({
                "id": content_hash[:12],
                "signal_type": "hyperscaler",
                "hyperscaler": hyperscaler,
                "signal_subtype": self._classify_subtype(full_text),
                "country": self._detect_country(full_text),
                "title": f"[{hyperscaler}] {title[:120]}",
                "body": full_text[:1500],
                "source": f"{hyperscaler} Blog",
                "url": entry.get("link"),
                "scraped_at": datetime.utcnow().isoformat(),
                "descasio_implication": implication,
                "implication_detail": detail,
                "metadata": {
                    "published": entry.get("published", ""),
                    "hyperscaler": hyperscaler,
                },
            })

        return signals

    async def _scan_careers(
        self, hyperscaler: str, sources: Dict
    ) -> List[Dict[str, Any]]:
        """
        Scan hyperscaler careers pages for Africa-focused hiring surges.
        Mass hiring of Africa-region roles is a 6–12 month leading indicator
        of market entry or major expansion.
        """
        careers_url = sources.get("careers_search") or sources.get("careers")
        if not careers_url:
            return []

        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: requests.get(
                    careers_url,
                    headers=self.headers,
                    timeout=self.config.request_timeout,
                ),
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"{hyperscaler} careers scrape failed: {e}")
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        all_text = soup.get_text(" ", strip=True)

        # Count Africa-related job indicators
        africa_job_count = sum(
            1 for kw in HYPERSCALER_HIRING_KEYWORDS
            if kw.lower() in all_text.lower()
        )

        if africa_job_count < 3:
            return []

        content_hash = hashlib.md5(f"{hyperscaler}::careers::{datetime.utcnow().date()}".encode()).hexdigest()
        implication, detail = self._assess_implication(hyperscaler, f"careers hiring africa {africa_job_count} roles")

        return [{
            "id": content_hash[:12],
            "signal_type": "hyperscaler",
            "hyperscaler": hyperscaler,
            "signal_subtype": "hiring_surge",
            "country": "Pan-Africa",
            "title": f"[{hyperscaler}] Africa-region hiring activity detected ({africa_job_count} keyword matches on careers page)",
            "body": (
                f"{hyperscaler} careers page shows elevated Africa-region hiring indicators "
                f"({africa_job_count} keyword matches across: Africa, Nigeria, Kenya, Ghana, "
                f"solutions architect, partner manager, country manager roles). "
                f"Historical pattern: hyperscaler hiring surges in Africa precede infrastructure "
                f"announcements or major enterprise client wins by 6–12 months."
            ),
            "source": f"{hyperscaler} Careers Page",
            "url": careers_url,
            "scraped_at": datetime.utcnow().isoformat(),
            "descasio_implication": implication,
            "implication_detail": detail,
            "metadata": {
                "hyperscaler": hyperscaler,
                "africa_keyword_matches": africa_job_count,
                "signal_subtype": "leading_indicator",
            },
        }]

    def _is_africa_relevant(self, text: str, keywords: List[str]) -> bool:
        """Check if text is relevant to Africa using keyword matching."""
        text_lower = text.lower()
        return any(kw in text_lower for kw in keywords)

    def _classify_subtype(self, text: str) -> str:
        """Classify the hyperscaler signal subtype from text."""
        text_lower = text.lower()
        if any(w in text_lower for w in ["local zone", "region", "availability zone", "data center", "data centre", "outpost"]):
            return "infrastructure"
        if any(w in text_lower for w in ["partner", "reseller", "consulting", "marketplace", "channel"]):
            return "partnership"
        if any(w in text_lower for w in ["hire", "job", "career", "recruit", "team"]):
            return "hiring_surge"
        return "announcement"

    def _detect_country(self, text: str) -> str:
        """Map text to the most likely African country."""
        markers = {
            "Nigeria": ["nigeria", "lagos", "abuja", "nigerian"],
            "Kenya": ["kenya", "nairobi", "kenyan", "mombasa"],
            "Ghana": ["ghana", "accra", "ghanaian"],
            "Uganda": ["uganda", "kampala", "ugandan"],
            "South Africa": ["south africa", "johannesburg", "cape town", "sa "],
            "Egypt": ["egypt", "cairo", "egyptian"],
        }
        text_lower = text.lower()
        counts = {c: sum(1 for m in markers if m in text_lower) for c, markers in markers.items()}
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else "Pan-Africa"

    def _assess_implication(self, hyperscaler: str, text: str) -> tuple[str, str]:
        """
        Assess whether this signal is an OPPORTUNITY, THREAT, or NEUTRAL for Descasio.

        OPPORTUNITY: Hyperscaler expanding → Descasio (as partner) gets a tailwind
        THREAT: Hyperscaler going direct/partnering with a Descasio competitor
        NEUTRAL: General announcement with no immediate commercial impact
        """
        text_lower = text.lower()

        # Direct-to-enterprise signals → THREAT
        threat_markers = ["direct", "enterprise sales", "country manager", "dedicated sales"]
        if any(m in text_lower for m in threat_markers):
            if hyperscaler == "AWS":
                return "NEUTRAL", (
                    "AWS direct sales expansion may reduce partner channel dependency. "
                    "Monitor: Descasio's ACP status remains a strong differentiator."
                )
            return "THREAT", (
                f"{hyperscaler} direct enterprise expansion reduces Descasio's positioning "
                "as the preferred routing path for enterprise clients."
            )

        # Infrastructure / Local Zone → OPPORTUNITY for Descasio as ACP
        infra_markers = ["local zone", "region", "data center", "data centre", "availability zone", "edge"]
        if any(m in text_lower for m in infra_markers):
            if hyperscaler == "AWS":
                return "OPPORTUNITY", (
                    "New AWS infrastructure in Africa validates the cloud market and creates "
                    "immediate migration/managed services pipeline for Descasio as ACP."
                )
            return "NEUTRAL", (
                f"New {hyperscaler} infrastructure expands the addressable market. "
                "Descasio may evaluate multi-cloud positioning to capture demand."
            )

        # Partnership with competitor → THREAT
        threat_partners = ["safaricom", "mtn", "airtel", "liquid", "deloitte", "accenture", "pwc"]
        if any(p in text_lower for p in threat_partners):
            return "THREAT", (
                f"{hyperscaler} partnership with a Descasio competitor. "
                "Assess impact on shared accounts and re-evaluate partner positioning."
            )

        return "NEUTRAL", f"{hyperscaler} Africa signal. Monitor for commercial impact on Descasio's pipeline."
