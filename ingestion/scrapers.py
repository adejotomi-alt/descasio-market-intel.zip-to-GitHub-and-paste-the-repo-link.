"""
Descasio Market Intelligence — Ingestion Layer
Handles all data collection: procurement portals, news feeds, competitor monitoring, regulatory tracking.

Key design decisions:
- Async-first with aiohttp for speed across 4 country portals
- Rate limiting per domain (1.5s delay) to avoid IP blocks on government portals
- Content hash-based deduplication — never surfaces the same signal twice
- Keyword filtering happens at scrape time to keep AI token costs down
- All scrapers return a standardised RawSignal dict
"""

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from urllib.parse import urljoin, urlparse

import aiohttp
import feedparser
import requests
from bs4 import BeautifulSoup

from config.settings import (
    Config,
    PROCUREMENT_SOURCES,
    NEWS_SOURCES,
    COMPETITOR_SOURCES,
    REGULATORY_SOURCES,
    HYPERSCALER_SOURCES,
    TENDER_KEYWORDS,
)

logger = logging.getLogger(__name__)

# In-memory seen-signal store. In production, replace with DynamoDB or Redis.
_SEEN_HASHES: set = set()


def _make_raw_signal(
    signal_type: str,
    country: str,
    title: str,
    body: str,
    source: str,
    url: Optional[str] = None,
    metadata: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    """
    Creates a deduplicated RawSignal dict.
    Returns None if this signal has already been seen in this run.
    """
    content_hash = hashlib.md5(f"{source}::{title}".encode()).hexdigest()
    if content_hash in _SEEN_HASHES:
        return None
    _SEEN_HASHES.add(content_hash)

    return {
        "id": content_hash[:12],
        "signal_type": signal_type,
        "country": country,
        "title": title[:200],
        "body": body[:2000],  # Cap to control AI token costs
        "source": source,
        "url": url,
        "scraped_at": datetime.utcnow().isoformat(),
        "metadata": metadata or {},
    }


def _is_relevant(text: str, keywords: List[str] = TENDER_KEYWORDS) -> bool:
    """Fast keyword relevance check. Case-insensitive."""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _get_html(url: str, config: Config, timeout: int = 30) -> Optional[str]:
    """Synchronous HTTP GET with retry and rate limiting."""
    headers = {"User-Agent": config.user_agent}
    for attempt in range(config.max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            time.sleep(config.rate_limit_delay)
            return resp.text
        except requests.RequestException as e:
            logger.warning(f"Attempt {attempt + 1}/{config.max_retries} failed for {url}: {e}")
            time.sleep(2 ** attempt)  # Exponential backoff
    return None


# ─── PROCUREMENT SCRAPER ──────────────────────────────────────────────────────

class ProcurementScraper:
    """
    Scrapes government procurement portals across all 4 target markets.
    Each country portal has its own parser since page structures differ.
    Falls back to generic extraction if site structure has changed.
    """

    def __init__(self, config: Config):
        self.config = config

    async def fetch_tenders(
        self, country: str, days_back: int = 7
    ) -> List[Dict[str, Any]]:
        """
        Fetch all recent, relevant tenders for a given country.
        Runs all sources for that country concurrently.
        """
        sources = PROCUREMENT_SOURCES.get(country, [])
        if not sources:
            logger.warning(f"No procurement sources configured for {country}")
            return []

        tasks = [self._scrape_source(source) for source in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        signals = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Procurement scrape error: {result}")
                continue
            signals.extend(result)

        logger.info(f"{country}: Found {len(signals)} relevant procurement signals")
        return signals

    async def _scrape_source(self, source: Dict) -> List[Dict]:
        """Dispatch to the right parser based on country."""
        country = source.get("country")
        parser_map = {
            "Nigeria": self._parse_nigeria_bpp,
            "Kenya": self._parse_kenya_ppra,
            "Ghana": self._parse_ghana_ppa,
            "Uganda": self._parse_uganda_ppda,
        }
        parser = parser_map.get(country, self._parse_generic)

        loop = asyncio.get_event_loop()
        html = await loop.run_in_executor(
            None, _get_html, source["url"], self.config
        )
        if not html:
            logger.warning(f"Failed to fetch {source['name']}: no HTML returned")
            return []

        try:
            return parser(html, source)
        except Exception as e:
            logger.error(f"Parser error for {source['name']}: {e}")
            return self._parse_generic(html, source)

    def _parse_nigeria_bpp(self, html: str, source: Dict) -> List[Dict]:
        """Parse Nigeria Bureau of Public Procurement listings."""
        soup = BeautifulSoup(html, "lxml")
        signals = []

        # BPP uses a table layout for tender listings
        rows = soup.select("table.tender-table tr, .tender-item, .procurement-notice")
        if not rows:
            # Fallback: look for any list of procurement items
            rows = soup.find_all(["article", "li"], class_=re.compile(r"tender|procure|notice"))

        for row in rows[:50]:  # Cap at 50 per cycle
            text = row.get_text(" ", strip=True)
            if not _is_relevant(text):
                continue

            title = self._extract_title(row) or text[:100]
            link = self._extract_link(row, source["url"])
            deadline = self._extract_date(text)

            signal = _make_raw_signal(
                signal_type="tender",
                country="Nigeria",
                title=title,
                body=text,
                source=source["name"],
                url=link,
                metadata={"deadline_raw": deadline, "portal": "BPP Nigeria"},
            )
            if signal:
                signals.append(signal)

        return signals

    def _parse_kenya_ppra(self, html: str, source: Dict) -> List[Dict]:
        """Parse Kenya Public Procurement Regulatory Authority tender listings."""
        soup = BeautifulSoup(html, "lxml")
        signals = []

        # PPRA uses a grid/card layout
        cards = soup.select(".tender-card, .notice-item, table.tenders-list tr")
        if not cards:
            cards = soup.find_all(["div", "tr"], class_=re.compile(r"tender|notice|bid"))

        for card in cards[:50]:
            text = card.get_text(" ", strip=True)
            if not _is_relevant(text):
                continue

            title = self._extract_title(card) or text[:100]
            link = self._extract_link(card, source["url"])
            deadline = self._extract_date(text)

            signal = _make_raw_signal(
                signal_type="tender",
                country="Kenya",
                title=title,
                body=text,
                source=source["name"],
                url=link,
                metadata={"deadline_raw": deadline, "portal": "PPRA Kenya"},
            )
            if signal:
                signals.append(signal)

        return signals

    def _parse_ghana_ppa(self, html: str, source: Dict) -> List[Dict]:
        """Parse Ghana Public Procurement Authority adverts."""
        soup = BeautifulSoup(html, "lxml")
        signals = []

        items = soup.select(".procurement-advert, .tender-item, table tr")
        for item in items[:50]:
            text = item.get_text(" ", strip=True)
            if not _is_relevant(text):
                continue

            title = self._extract_title(item) or text[:100]
            signal = _make_raw_signal(
                signal_type="tender",
                country="Ghana",
                title=title,
                body=text,
                source=source["name"],
                url=self._extract_link(item, source["url"]),
                metadata={"portal": "PPA Ghana"},
            )
            if signal:
                signals.append(signal)

        return signals

    def _parse_uganda_ppda(self, html: str, source: Dict) -> List[Dict]:
        """Parse Uganda Public Procurement and Disposal of Public Assets Authority."""
        soup = BeautifulSoup(html, "lxml")
        signals = []

        items = soup.select(".procurement-notice, article.notice, table.notices tr")
        for item in items[:50]:
            text = item.get_text(" ", strip=True)
            if not _is_relevant(text):
                continue

            title = self._extract_title(item) or text[:100]
            signal = _make_raw_signal(
                signal_type="tender",
                country="Uganda",
                title=title,
                body=text,
                source=source["name"],
                url=self._extract_link(item, source["url"]),
                metadata={"portal": "PPDA Uganda"},
            )
            if signal:
                signals.append(signal)

        return signals

    def _parse_generic(self, html: str, source: Dict) -> List[Dict]:
        """
        Fallback generic parser for when a portal has changed its structure.
        Extracts any paragraph or list item containing tender keywords.
        """
        soup = BeautifulSoup(html, "lxml")
        signals = []
        country = source.get("country", "Unknown")

        for el in soup.find_all(["p", "li", "tr", "article"])[:100]:
            text = el.get_text(" ", strip=True)
            if len(text) < 30 or not _is_relevant(text):
                continue

            signal = _make_raw_signal(
                signal_type="tender",
                country=country,
                title=text[:100],
                body=text,
                source=source["name"],
                url=self._extract_link(el, source["url"]),
            )
            if signal:
                signals.append(signal)

        return signals[:20]  # Cap generic results — quality over quantity

    @staticmethod
    def _extract_title(element) -> Optional[str]:
        """Try to extract a meaningful title from an HTML element."""
        for selector in ["h1", "h2", "h3", "h4", ".title", ".tender-title", "strong", "b"]:
            el = element.find(selector) if element.name != selector else element
            if el and el.get_text(strip=True):
                return el.get_text(strip=True)[:200]
        return None

    @staticmethod
    def _extract_link(element, base_url: str) -> Optional[str]:
        """Extract and resolve a hyperlink from an HTML element."""
        a = element.find("a", href=True)
        if a:
            href = a["href"]
            if href.startswith("http"):
                return href
            return urljoin(base_url, href)
        return None

    @staticmethod
    def _extract_date(text: str) -> Optional[str]:
        """Extract a date string from tender text using common date patterns."""
        patterns = [
            r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
            r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1)
        return None


# ─── NEWS SCRAPER ─────────────────────────────────────────────────────────────

class NewsScraper:
    """
    Aggregates and filters pan-African tech news from RSS feeds.
    Runs on the same 6-hour cadence as the procurement scraper.
    """

    def __init__(self, config: Config):
        self.config = config
        # Broader keywords for news vs. the very specific tender keywords
        self.news_keywords = [
            "cloud", "AI", "artificial intelligence", "digital transformation",
            "fintech", "data center", "cybersecurity", "managed services",
            "enterprise", "AWS", "Azure", "Google Cloud",
            "Descasio", "Liquid", "BCX", "Deloitte", "data sovereignty",
            "NDPR", "data protection", "digital", "procurement", "government IT",
            "banking technology", "telco", "infrastructure",
        ]

    async def fetch_recent(self, hours: int = 6) -> List[Dict[str, Any]]:
        """
        Fetch news articles published in the last N hours from all RSS sources.
        """
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        all_signals = []

        for source in NEWS_SOURCES:
            signals = await self._process_rss_feed(source, cutoff)
            all_signals.extend(signals)
            await asyncio.sleep(0.5)  # Polite delay between feeds

        logger.info(f"News scraper: {len(all_signals)} relevant articles in last {hours}h")
        return all_signals

    async def _process_rss_feed(
        self, source: Dict, cutoff: datetime
    ) -> List[Dict[str, Any]]:
        """Parse a single RSS feed and return relevant signals."""
        loop = asyncio.get_event_loop()

        try:
            feed = await loop.run_in_executor(
                None, lambda: feedparser.parse(source["rss"])
            )
        except Exception as e:
            logger.error(f"RSS fetch failed for {source['name']}: {e}")
            return []

        signals = []
        for entry in feed.entries:
            # Filter by recency
            published = entry.get("published_parsed")
            if published:
                pub_dt = datetime(*published[:6])
                if pub_dt < cutoff:
                    continue

            title = entry.get("title", "")
            summary = entry.get("summary", "") or entry.get("description", "")
            full_text = f"{title} {summary}"

            if not _is_relevant(full_text, self.news_keywords):
                continue

            # Determine which African country this primarily relates to
            country = self._detect_country(full_text)

            signal = _make_raw_signal(
                signal_type="market_news",
                country=country,
                title=title,
                body=BeautifulSoup(summary, "lxml").get_text(" ", strip=True)[:1500],
                source=source["name"],
                url=entry.get("link"),
                metadata={
                    "published": entry.get("published", ""),
                    "feed_region": source.get("region", "Pan-Africa"),
                },
            )
            if signal:
                signals.append(signal)

        return signals

    @staticmethod
    def _detect_country(text: str) -> str:
        """Heuristic: return the most-mentioned target country, or 'Pan-Africa'."""
        country_markers = {
            "Nigeria": ["nigeria", "nigerian", "lagos", "abuja", "naira", "ngn"],
            "Kenya": ["kenya", "kenyan", "nairobi", "ksh", "safaricom", "mombasa"],
            "Ghana": ["ghana", "ghanaian", "accra", "cedi", "ghs"],
            "Uganda": ["uganda", "ugandan", "kampala", "ugx", "dfcu"],
        }
        text_lower = text.lower()
        counts = {
            country: sum(1 for marker in markers if marker in text_lower)
            for country, markers in country_markers.items()
        }
        best = max(counts, key=counts.get)
        return best if counts[best] > 0 else "Pan-Africa"


# ─── COMPETITOR MONITOR ───────────────────────────────────────────────────────

class CompetitorMonitor:
    """
    Monitors competitor hiring patterns and website changes.
    A sudden surge in cloud architect job postings = a major pitch is being staffed.
    This is one of the most valuable early warning signals in the system.
    """

    def __init__(self, config: Config):
        self.config = config

    async def scan_all(self) -> List[Dict[str, Any]]:
        """Scan all configured competitors and return relevant signals."""
        all_signals = []

        for name, profile in COMPETITOR_SOURCES.items():
            signals = await self._scan_competitor(name, profile)
            all_signals.extend(signals)
            await asyncio.sleep(self.config.rate_limit_delay)

        logger.info(f"Competitor monitor: {len(all_signals)} competitor signals")
        return all_signals

    async def _scan_competitor(
        self, name: str, profile: Dict
    ) -> List[Dict[str, Any]]:
        """Scan a single competitor's careers page for hiring pattern signals."""
        careers_url = profile.get("careers") or profile.get("website")
        if not careers_url:
            return []

        loop = asyncio.get_event_loop()
        html = await loop.run_in_executor(
            None, _get_html, careers_url, self.config
        )
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        job_elements = soup.find_all(
            ["li", "div", "tr", "article"],
            class_=re.compile(r"job|role|position|career|vacancy", re.IGNORECASE),
        )

        if not job_elements:
            # Fallback: look for any text containing job-related terms
            job_elements = [
                el for el in soup.find_all(["h2", "h3", "li"])
                if any(w in el.get_text().lower() for w in ["engineer", "architect", "consultant", "manager"])
            ]

        hiring_signals = []
        keywords = profile.get("hiring_keywords", ["cloud", "AWS"])

        for job_el in job_elements[:100]:
            job_text = job_el.get_text(" ", strip=True)
            if not job_text or len(job_text) < 10:
                continue
            if _is_relevant(job_text, keywords):
                hiring_signals.append(job_text[:200])

        if not hiring_signals:
            return []

        # Only surface if 3+ relevant positions found — noise filter
        if len(hiring_signals) < 3:
            return []

        signal = _make_raw_signal(
            signal_type="competitor_move",
            country=self._infer_country(name, profile),
            title=f"{name} — {len(hiring_signals)} relevant positions open ({', '.join(keywords[:3])})",
            body=f"Competitor hiring activity detected on {careers_url}.\n\n"
                 f"Job titles found ({len(hiring_signals)} total):\n"
                 + "\n".join(f"• {j}" for j in hiring_signals[:10]),
            source="Competitor Monitor",
            url=careers_url,
            metadata={
                "competitor": name,
                "positions_found": len(hiring_signals),
                "threat_level": profile.get("threat_level", "MEDIUM"),
                "notes": profile.get("notes", ""),
            },
        )
        return [signal] if signal else []

    @staticmethod
    def _infer_country(name: str, profile: Dict) -> str:
        """Infer primary country from competitor profile or name."""
        name_lower = name.lower()
        if "nigeria" in name_lower or "nigerian" in name_lower:
            return "Nigeria"
        if "kenya" in name_lower or "kenyan" in name_lower:
            return "Kenya"
        # Most competitors are pan-African
        return "Pan-Africa"


# ─── REGULATORY TRACKER ───────────────────────────────────────────────────────

class RegulatoryTracker:
    """
    Monitors regulatory and policy body websites for updates.
    Policy changes that create data localisation requirements are the most
    commercially significant — they force existing clients to migrate to
    Descasio's in-country infrastructure.
    """

    def __init__(self, config: Config):
        self.config = config
        self.regulatory_keywords = [
            "data protection", "data localisation", "data residency",
            "cloud", "cybersecurity", "AI regulation", "fintech",
            "digital", "compliance", "regulation", "guidelines", "framework",
            "directive", "policy", "circular", "requirement",
        ]

    async def fetch_updates(
        self, country: str, days_back: int = 7
    ) -> List[Dict[str, Any]]:
        """Fetch regulatory updates for a specific country."""
        sources = REGULATORY_SOURCES.get(country, [])
        if not sources:
            return []

        tasks = [self._scrape_regulator(source, country) for source in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        signals = []
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Regulatory scrape error: {result}")
                continue
            signals.extend(result)

        return signals

    async def _scrape_regulator(
        self, source: Dict, country: str
    ) -> List[Dict[str, Any]]:
        """Scrape a single regulatory body's news or updates page."""
        loop = asyncio.get_event_loop()
        html = await loop.run_in_executor(
            None, _get_html, source["url"], self.config
        )
        if not html:
            return []

        soup = BeautifulSoup(html, "lxml")
        signals = []

        # Most regulatory sites have news/press release sections
        items = soup.select(
            "article, .news-item, .press-release, .update-item, "
            ".post, table.news tr, .regulatory-update"
        )
        if not items:
            items = soup.find_all(["li", "div"], class_=re.compile(r"news|post|article|update"))

        for item in items[:30]:
            text = item.get_text(" ", strip=True)
            if not text or len(text) < 30:
                continue
            if not _is_relevant(text, self.regulatory_keywords):
                continue

            title = self._extract_title(item, text)
            signal = _make_raw_signal(
                signal_type="regulatory",
                country=country,
                title=f"[{source['name']}] {title}",
                body=text[:1500],
                source=source["name"],
                url=self._extract_link(item, source["url"]),
                metadata={
                    "regulator": source["name"],
                    "regulator_full_name": source.get("full_name", ""),
                    "relevance": source.get("relevance", "MEDIUM"),
                    "notes": source.get("notes", ""),
                },
            )
            if signal:
                signals.append(signal)

        return signals

    @staticmethod
    def _extract_title(element, fallback_text: str) -> str:
        for tag in ["h1", "h2", "h3", "h4", ".title"]:
            el = element.find(tag)
            if el and el.get_text(strip=True):
                return el.get_text(strip=True)[:150]
        return fallback_text[:100]

    @staticmethod
    def _extract_link(element, base_url: str) -> Optional[str]:
        a = element.find("a", href=True)
        if a:
            href = a["href"]
            return href if href.startswith("http") else urljoin(base_url, href)
        return None
