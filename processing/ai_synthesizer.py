"""
Descasio Market Intelligence — AI Synthesizer
The central intelligence engine. Transforms raw scraped signals into
actionable, structured intelligence using Anthropic Claude.

Two operating modes:
  1. SALES MODE — Tactical, time-sensitive. Generates battlecards and urgency ratings.
     Output: Structured signal list with priority, opportunity value, and battlecard points.

  2. EXEC MODE — Strategic, trend-based. Generates monthly C-Suite briefing.
     Output: Single structured brief covering threats, opportunities, regulatory landscape.

All outputs are strict JSON. Any deviation from schema triggers a validation retry.
"""

import json
import logging
from typing import List, Dict, Any, Optional

import anthropic

from config.settings import Config

logger = logging.getLogger(__name__)


# ─── SYSTEM PROMPTS ───────────────────────────────────────────────────────────

SALES_SYSTEM_PROMPT = """You are DAIA — Descasio's AI Intelligence Analyst. Your role is to transform 
raw African market signals into immediately actionable intelligence for Descasio's Business Development team.

{company_context}

TASK: Analyse the batch of raw signals provided by the user and produce structured intelligence for each 
relevant signal. Skip signals with zero relevance to Descasio's business.

PRIORITY DEFINITIONS:
- URGENT: Active tender with ≤14 days to deadline, or a competitor action requiring immediate response
- HIGH: Tender with 15–30 days left, or a significant competitor move or regulatory change
- MEDIUM: Tender with 31–60 days, or a trend signal worth monitoring actively
- LOW: Background intelligence, no immediate action required

BATTLECARD RULES: 3 bullet points maximum. Each point must be specific to Descasio's actual capability. 
No generic "we have great experience" statements — every point should include a differentiator 
(e.g. "Descasio's AWS ACP status is a stated eligibility requirement in this RFP").

OUTPUT: Respond ONLY with valid JSON. No preamble, no markdown fences, no explanation.

JSON SCHEMA:
{
  "signals": [
    {
      "id": "string (first 8 chars of input signal id)",
      "title": "string (max 90 chars, action-oriented — start with the entity name)",
      "signal_type": "tender|competitor_move|regulatory|market_news",
      "country": "string",
      "priority": "URGENT|HIGH|MEDIUM|LOW",
      "revenue_opportunity": "string (e.g. '$2M–$4M' or 'Defensive' or 'N/A')",
      "descasio_service_line": "Cloud Migration|Sovereign AI|PlugIQ BPA|Managed Services|Cybersecurity|Consulting",
      "summary": "string (2–3 sentences. Specific, factual, no fluff. What happened, why it matters to Descasio.)",
      "battlecard_points": ["string", "string", "string"],
      "deadline_raw": "string or null",
      "source_url": "string or null",
      "source": "string",
      "relevance_score": "integer 1–10"
    }
  ],
  "cycle_summary": "string (1 sentence summarising the batch — e.g. '4 signals processed: 1 URGENT tender, 2 competitor moves, 1 regulatory update')"
}"""


EXEC_SYSTEM_PROMPT = """You are DAIA — Descasio's AI Intelligence Analyst. Your role is to produce the 
monthly C-Suite strategic intelligence briefing for Descasio's executive leadership team (CEO, COO, CTO, CSO).

{company_context}

TASK: Analyse the 30-day batch of market signals provided and produce the monthly Strategic Intelligence Brief.

EXECUTIVE BRIEF RULES:
- Board-level language. No jargon. No operational detail.
- Every threat must have a recommended executive response.
- Opportunity pipeline values should be realistic ranges, not point estimates.
- Regulatory items: distinguish between 'creates immediate action' vs 'monitor for 90 days'.
- Strategic recommendations: max 3, each with a clear owner role and 90-day timeline.

OUTPUT: Respond ONLY with valid JSON. No preamble, no markdown fences, no explanation.

JSON SCHEMA:
{
  "briefing_month": "string (e.g. 'June 2026')",
  "executive_summary": "string (max 120 words, board-level overview of the month)",
  "market_sentiment": "BULLISH|CAUTIOUS|BEARISH",
  "key_threats": [
    {
      "threat": "string",
      "severity": "HIGH|MEDIUM|LOW",
      "impacted_service_line": "string",
      "recommended_response": "string (1 sentence, executive-level action)"
    }
  ],
  "opportunity_pipeline": [
    {
      "country": "string",
      "opportunity": "string",
      "estimated_value": "string",
      "timeline": "string",
      "service_line": "string",
      "confidence": "HIGH|MEDIUM|LOW"
    }
  ],
  "regulatory_landscape": [
    {
      "country": "string",
      "regulator": "string",
      "update": "string",
      "impact": "POSITIVE|NEGATIVE|NEUTRAL",
      "descasio_implication": "string",
      "urgency": "IMMEDIATE|MONITOR_90_DAYS|WATCH"
    }
  ],
  "hyperscaler_dynamics": "string (2 paragraphs: what AWS/Azure/GCP are doing in Africa and what it means for Descasio)",
  "competitor_intelligence": "string (1–2 paragraphs: what the competition is doing and Descasio's positioning response)",
  "strategic_recommendations": [
    {
      "action": "string",
      "rationale": "string (1 sentence)",
      "owner": "string (e.g. 'CEO', 'Head of BD', 'CTO')",
      "timeline": "string (e.g. 'Within 30 days', 'Q3 2026')"
    }
  ],
  "total_signals_processed": "integer",
  "signals_by_country": {"Nigeria": 0, "Kenya": 0, "Ghana": 0, "Uganda": 0}
}"""


# ─── SYNTHESIZER CLASS ────────────────────────────────────────────────────────

class AISynthesizer:
    """
    Core AI brain of the Descasio Market Intelligence system.
    
    Usage:
        synthesizer = AISynthesizer(config)
        signals = await synthesizer.process_batch(raw_signals, mode="sales")
        briefing = await synthesizer.process_batch(raw_signals, mode="executive")
    """

    CHUNK_SIZE = 15  # Signals per API call. Keeps responses well within token limits.
    MAX_RETRIES = 2  # JSON parse failures trigger a retry with a stricter prompt.

    def __init__(self, config: Config):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self.model = config.claude_model

    async def process_batch(
        self,
        raw_signals: List[Dict[str, Any]],
        mode: str = "sales",
        generate_battlecards: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Main entry point. Process any number of raw signals.
        
        Args:
            raw_signals: Raw signal dicts from the ingestion layer
            mode: 'sales' (tactical) or 'executive' (strategic)
            generate_battlecards: Include battlecard_points in sales output
            
        Returns:
            List of AI-enriched signal dicts (sales) or a list with one briefing dict (exec)
        """
        if not raw_signals:
            logger.info("No raw signals to synthesize.")
            return []

        if mode == "executive":
            # Executive briefing: all signals in one call for holistic synthesis
            return await self._synthesize_exec_batch(raw_signals)

        # Sales mode: chunk to avoid token limits
        chunks = [
            raw_signals[i : i + self.CHUNK_SIZE]
            for i in range(0, len(raw_signals), self.CHUNK_SIZE)
        ]

        all_results = []
        for idx, chunk in enumerate(chunks):
            logger.info(f"Processing chunk {idx + 1}/{len(chunks)} ({len(chunk)} signals)...")
            result = await self._synthesize_sales_chunk(chunk)
            all_results.extend(result)

        # Sort by priority — URGENT first
        priority_order = {"URGENT": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        all_results.sort(key=lambda x: priority_order.get(x.get("priority", "LOW"), 3))

        logger.info(
            f"Synthesis complete: {len(all_results)} signals from {len(raw_signals)} raw inputs"
        )
        return all_results

    async def _synthesize_sales_chunk(
        self, signals: List[Dict]
    ) -> List[Dict]:
        """Process one chunk of signals in sales mode."""
        system = SALES_SYSTEM_PROMPT.format(
            company_context=self.config.company_context
        )
        user_message = (
            f"Process these {len(signals)} raw market signals.\n\n"
            f"RAW SIGNALS:\n{json.dumps(signals, indent=2, default=str)}\n\n"
            "Return ONLY valid JSON matching the schema. No other text."
        )

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=system,
                    messages=[{"role": "user", "content": user_message}],
                )
                raw_text = response.content[0].text.strip()
                parsed = self._safe_parse_json(raw_text)
                signals_out = parsed.get("signals", [])
                # Filter out zero-relevance signals
                return [s for s in signals_out if s.get("relevance_score", 5) >= 4]

            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"JSON parse attempt {attempt + 1} failed: {e}")
                if attempt == self.MAX_RETRIES:
                    logger.error("All parse attempts exhausted. Returning empty for this chunk.")
                    return []
                user_message += "\n\nIMPORTANT: Your previous response was not valid JSON. Return ONLY the JSON object, no other text."
            except anthropic.APIError as e:
                logger.error(f"Anthropic API error: {e}")
                return []

        return []

    async def _synthesize_exec_batch(
        self, signals: List[Dict]
    ) -> List[Dict]:
        """
        Process all signals as a single executive strategic brief.
        For very large batches (>100 signals), pre-summarise in chunks first.
        """
        if len(signals) > 80:
            # Pre-summarise: get sales intelligence first, then pass to exec mode
            pre_summarised = await self.process_batch(
                signals[:80], mode="sales"
            )
            inputs = pre_summarised
        else:
            inputs = signals

        system = EXEC_SYSTEM_PROMPT.format(
            company_context=self.config.company_context
        )
        user_message = (
            f"Produce the monthly C-Suite Strategic Intelligence Brief from these "
            f"{len(inputs)} market signals.\n\n"
            f"SIGNALS:\n{json.dumps(inputs, indent=2, default=str)}\n\n"
            "Return ONLY valid JSON. No other text."
        )

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=6000,
                system=system,
                messages=[{"role": "user", "content": user_message}],
            )
            raw_text = response.content[0].text.strip()
            parsed = self._safe_parse_json(raw_text)
            parsed["total_signals_processed"] = len(inputs)
            return [parsed]  # Exec briefing is a single object
        except Exception as e:
            logger.error(f"Executive synthesis failed: {e}")
            return []

    async def generate_targeted_battlecard(
        self,
        signal: Dict,
        crm_deal: Optional[Dict] = None,
    ) -> str:
        """
        Generate an enhanced battlecard for a specific signal, optionally
        enriched with CRM deal context (existing account data from Zoho).
        Used when a sales rep clicks "Generate Battlecard" in Zoho CRM.
        """
        crm_context = ""
        if crm_deal:
            crm_context = (
                f"\nCRM DEAL CONTEXT (existing Zoho record):\n"
                f"{json.dumps(crm_deal, indent=2)}"
            )

        prompt = f"""Generate a targeted Descasio sales battlecard for this intelligence signal.

SIGNAL:
{json.dumps(signal, indent=2)}
{crm_context}

{self.config.company_context}

BATTLECARD FORMAT (use exactly this structure, max 180 words total):

**SITUATION**
[1 sentence: what happened in the market]

**DESCASIO OPPORTUNITY**
[1–2 sentences: specific opportunity and why Descasio is positioned to win]

**YOUR TALKING POINTS**
• [Specific Descasio differentiator — reference a real capability or credential]
• [Competitor weakness or gap Descasio fills]
• [Risk of inaction for the prospect]

**RECOMMENDED NEXT ACTION**
[Single, concrete action the sales rep should take in the next 48 hours]

**IF WE DON'T ACT**
[1 sentence: cost of inaction]"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except anthropic.APIError as e:
            logger.error(f"Battlecard generation failed: {e}")
            return "⚠️ Battlecard generation failed. Please review signal manually."

    @staticmethod
    def _safe_parse_json(text: str) -> Dict:
        """
        Parse JSON from Claude's response, stripping any accidental markdown fences
        or text that snuck in despite the strict prompt.
        """
        # Strip markdown fences
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]

        # Find the JSON object bounds if there's leading/trailing text
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            text = text[start:end]

        return json.loads(text.strip())


# ─── SIGNAL CLASSIFIER ────────────────────────────────────────────────────────

class SignalClassifier:
    """
    Post-synthesis routing layer.
    Determines which signals go to Sales (immediate alert) vs. Executive (digest only).
    Also applies a final quality filter before delivery.
    """

    SALES_THRESHOLD = 5       # Minimum relevance score for Sales feed
    EXEC_THRESHOLD = 6        # Minimum for Executive digest

    SALES_TYPES = {"tender", "competitor_move"}
    EXEC_TYPES = {"regulatory", "market_news", "competitor_move"}

    def filter_for_sales(self, signals: List[Dict]) -> List[Dict]:
        """
        Return signals appropriate for the Sales team:
        - Tenders and competitor moves
        - Relevance score ≥ 5
        - Priority URGENT, HIGH, or MEDIUM
        """
        return [
            s for s in signals
            if (
                s.get("signal_type") in self.SALES_TYPES
                and s.get("relevance_score", 0) >= self.SALES_THRESHOLD
                and s.get("priority", "LOW") in {"URGENT", "HIGH", "MEDIUM"}
            )
        ]

    def filter_for_exec(self, signals: List[Dict]) -> List[Dict]:
        """
        Return signals appropriate for the executive digest:
        - Regulatory, market news, and major competitor moves
        - Relevance score ≥ 6
        """
        return [
            s for s in signals
            if (
                s.get("signal_type") in self.EXEC_TYPES
                and s.get("relevance_score", 0) >= self.EXEC_THRESHOLD
            )
        ]

    @staticmethod
    def is_urgent(signal: Dict) -> bool:
        return signal.get("priority") == "URGENT"
