"""
Descasio Market Intelligence — Delivery: Slack + Email
Handles all outbound intelligence delivery to Sales and Executive audiences.

Two distinct delivery channels:
  1. SLACK — Sales team (#bd-intel): Immediate, formatted Block Kit alerts.
             Exec team (#leadership-intel): Monthly digest notification.
  2. EMAIL  — C-Suite only: Full monthly Strategic Intelligence Brief via AWS SES.
             HTML email with full brief content.

Block Kit design principles applied here:
  - URGENT signals: Red header + bold title
  - HIGH signals: Amber/orange treatment
  - MEDIUM/LOW: Standard with blue context dividers
  - Every sales alert includes the AI-generated battlecard in a quote block
  - Action buttons: "Open in Zoho" and "Mark as Seen"
"""

import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

import boto3
import requests

from config.settings import Config

logger = logging.getLogger(__name__)

# Priority → Slack colour mapping (left border on attachment)
PRIORITY_COLORS = {
    "URGENT": "#EF4444",   # Red
    "HIGH": "#F59E0B",     # Amber
    "MEDIUM": "#3B82F6",   # Blue
    "LOW": "#6B7280",      # Gray
}

PRIORITY_EMOJI = {
    "URGENT": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🔵",
    "LOW": "⚪",
}

SIGNAL_TYPE_LABEL = {
    "tender": "📋 TENDER",
    "competitor_move": "⚡ COMPETITOR",
    "regulatory": "⚖️ REGULATORY",
    "market_news": "📰 MARKET NEWS",
}


class SlackAlerter:
    """
    Sends formatted intelligence alerts to Slack channels.
    
    Sales channel: One message per signal (immediate, actionable).
    Exec channel: One message per cycle summarising key signals.
    """

    def __init__(self, config: Config):
        self.config = config

    def post_sales_alert(self, signal: Dict[str, Any]) -> bool:
        """
        Post a formatted signal alert to the BD/Sales Slack channel.
        Uses Block Kit for rich formatting. Includes battlecard in expandable section.
        """
        if not self.config.slack_sales_webhook:
            logger.warning("SLACK_SALES_WEBHOOK not configured — skipping Slack delivery.")
            return False

        priority = signal.get("priority", "MEDIUM")
        signal_type = signal.get("signal_type", "market_news")
        country = signal.get("country", "Africa")
        title = signal.get("title", "Untitled Signal")
        summary = signal.get("summary", "No summary available.")
        opportunity = signal.get("revenue_opportunity", "N/A")
        service_line = signal.get("descasio_service_line", "")
        deadline = signal.get("deadline_raw")
        source = signal.get("source", "Unknown Source")
        source_url = signal.get("source_url")
        battlecard = signal.get("battlecard_points", [])

        # Build battlecard text
        battlecard_text = "\n".join(
            f"  • {point}" for point in battlecard if point
        )

        # Deadline string
        deadline_str = f" | ⏰ *Deadline: {deadline}*" if deadline else ""

        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{PRIORITY_EMOJI.get(priority, '⚪')} {SIGNAL_TYPE_LABEL.get(signal_type, '📌 SIGNAL')} — {country}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{title}*",
                },
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Priority:* {priority}{deadline_str}"},
                    {"type": "mrkdwn", "text": f"*Opportunity:* {opportunity}"},
                    {"type": "mrkdwn", "text": f"*Service Line:* {service_line}"},
                    {"type": "mrkdwn", "text": f"*Source:* {source}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": summary},
            },
        ]

        # Add battlecard block if available
        if battlecard_text:
            blocks.append({"type": "divider"})
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🎯 BATTLECARD*\n{battlecard_text}",
                },
            })

        # Action buttons
        action_elements = []
        if source_url:
            action_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "View Source →"},
                "url": source_url,
                "style": "primary",
            })
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Open in Zoho CRM"},
            "url": "https://crm.zoho.com/crm/org000000/Market_Intelligence",
        })

        if action_elements:
            blocks.append({"type": "actions", "elements": action_elements})

        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"_Descasio Intelligence Hub · {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}_"}
        ]})

        payload = {
            "attachments": [
                {
                    "color": PRIORITY_COLORS.get(priority, "#6B7280"),
                    "blocks": blocks,
                }
            ]
        }

        return self._post_webhook(self.config.slack_sales_webhook, payload, "Sales")

    def post_sales_cycle_summary(self, signals: List[Dict], cycle_num: int = 0) -> bool:
        """
        Post a brief summary at the end of each sales scan cycle.
        Summarises signal counts without spamming the channel with individual alerts
        for MEDIUM/LOW priority items.
        """
        if not self.config.slack_sales_webhook:
            return False

        urgent = sum(1 for s in signals if s.get("priority") == "URGENT")
        high = sum(1 for s in signals if s.get("priority") == "HIGH")
        medium = sum(1 for s in signals if s.get("priority") == "MEDIUM")

        if not signals:
            return True  # No signals = no noise

        payload = {
            "blocks": [
                {"type": "section", "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*🔎 Intel Scan Complete* — {datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}\n"
                        f"{len(signals)} signals processed | "
                        f"🔴 {urgent} URGENT · 🟠 {high} HIGH · 🔵 {medium} MEDIUM\n"
                        f"Check <https://crm.zoho.com/crm/|Zoho CRM Market Intelligence> for full battlecards."
                    )
                }}
            ]
        }

        return self._post_webhook(self.config.slack_sales_webhook, payload, "Cycle Summary")

    def post_exec_summary(self, briefing: Dict[str, Any]) -> bool:
        """
        Post a concise executive notification to the #leadership-intel channel.
        Brief overview only — the full brief goes by email.
        """
        if not self.config.slack_exec_webhook:
            logger.warning("SLACK_EXEC_WEBHOOK not configured — skipping exec Slack post.")
            return False

        month = briefing.get("briefing_month", datetime.utcnow().strftime("%B %Y"))
        sentiment = briefing.get("market_sentiment", "CAUTIOUS")
        summary = briefing.get("executive_summary", "")
        threats = briefing.get("key_threats", [])
        opps = briefing.get("opportunity_pipeline", [])
        recs = briefing.get("strategic_recommendations", [])

        sentiment_emoji = {"BULLISH": "📈", "CAUTIOUS": "⚠️", "BEARISH": "📉"}.get(sentiment, "📊")

        blocks = [
            {"type": "header", "text": {
                "type": "plain_text",
                "text": f"📊 Descasio Strategic Intelligence Brief — {month}"
            }},
            {"type": "section", "text": {
                "type": "mrkdwn",
                "text": f"*Market Sentiment:* {sentiment_emoji} {sentiment}\n\n{summary}"
            }},
            {"type": "divider"},
        ]

        # Key threats
        if threats:
            threat_text = "\n".join(
                f"• *[{t.get('severity')}]* {t.get('threat', '')}"
                for t in threats[:3]
            )
            blocks.append({"type": "section", "text": {
                "type": "mrkdwn", "text": f"*⚠️ Key Threats*\n{threat_text}"
            }})

        # Pipeline highlights
        if opps:
            opp_text = "\n".join(
                f"• *{o.get('country')}* — {o.get('opportunity', '')[:80]} ({o.get('estimated_value', '')})"
                for o in opps[:4]
            )
            blocks.append({"type": "section", "text": {
                "type": "mrkdwn", "text": f"*💼 Pipeline Highlights*\n{opp_text}"
            }})

        # Strategic actions
        if recs:
            rec_text = "\n".join(
                f"• {r.get('action', '')} — _{r.get('owner', '')} / {r.get('timeline', '')}_"
                for r in recs[:3]
            )
            blocks.append({"type": "section", "text": {
                "type": "mrkdwn", "text": f"*✅ Strategic Recommendations*\n{rec_text}"
            }})

        blocks.append({"type": "context", "elements": [
            {"type": "mrkdwn", "text": "_Full brief sent to executive email list · Descasio Intelligence Hub_"}
        ]})

        payload = {"blocks": blocks}
        return self._post_webhook(self.config.slack_exec_webhook, payload, "Exec Summary")

    @staticmethod
    def _post_webhook(url: str, payload: Dict, label: str) -> bool:
        """Post a payload to a Slack incoming webhook URL."""
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info(f"Slack [{label}]: Posted successfully")
            return True
        except requests.RequestException as e:
            logger.error(f"Slack [{label}] webhook failed: {e}")
            return False


# ─── EMAIL BRIEFING ───────────────────────────────────────────────────────────

class BriefingComposer:
    """
    Composes and sends the monthly C-Suite Strategic Intelligence Brief by email.
    Uses AWS SES (Simple Email Service) for delivery.
    Email is full HTML — designed to read well in both desktop and mobile clients.
    """

    def __init__(self, config: Config):
        self.config = config

    def send_exec_email(self, briefing: Dict[str, Any]) -> bool:
        """
        Send the monthly HTML executive briefing email via AWS SES.
        """
        if not self.config.exec_recipients:
            logger.warning("No EXEC_RECIPIENTS configured — skipping email delivery.")
            return False

        month = briefing.get("briefing_month", datetime.utcnow().strftime("%B %Y"))
        html_body = self._render_html_brief(briefing)
        text_body = self._render_text_brief(briefing)

        try:
            ses = boto3.client("ses", region_name=self.config.ses_region)
            response = ses.send_email(
                Source=self.config.ses_sender,
                Destination={"ToAddresses": self.config.exec_recipients},
                Message={
                    "Subject": {
                        "Data": f"Descasio Strategic Intelligence Brief — {month}",
                        "Charset": "UTF-8",
                    },
                    "Body": {
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                        "Text": {"Data": text_body, "Charset": "UTF-8"},
                    },
                },
            )
            message_id = response["MessageId"]
            logger.info(
                f"Exec email sent to {len(self.config.exec_recipients)} recipients. "
                f"SES MessageId: {message_id}"
            )
            return True
        except Exception as e:
            logger.error(f"SES email delivery failed: {e}")
            return False

    def _render_html_brief(self, briefing: Dict) -> str:
        """Render the briefing as a clean HTML email."""
        month = briefing.get("briefing_month", "")
        summary = briefing.get("executive_summary", "")
        sentiment = briefing.get("market_sentiment", "CAUTIOUS")
        threats = briefing.get("key_threats", [])
        opps = briefing.get("opportunity_pipeline", [])
        regulatory = briefing.get("regulatory_landscape", [])
        recs = briefing.get("strategic_recommendations", [])
        hyperscaler = briefing.get("hyperscaler_dynamics", "")
        competitor_intel = briefing.get("competitor_intelligence", "")

        sentiment_color = {"BULLISH": "#10B981", "CAUTIOUS": "#F59E0B", "BEARISH": "#EF4444"}.get(sentiment, "#6B7280")

        def render_threats():
            if not threats:
                return "<p>No critical threats identified this period.</p>"
            rows = "".join(
                f"""<tr>
                    <td style="padding:8px;border-bottom:1px solid #e5e7eb;font-weight:600;color:{'#EF4444' if t.get('severity')=='HIGH' else '#F59E0B' if t.get('severity')=='MEDIUM' else '#6B7280'}">{t.get('severity')}</td>
                    <td style="padding:8px;border-bottom:1px solid #e5e7eb">{t.get('threat','')}</td>
                    <td style="padding:8px;border-bottom:1px solid #e5e7eb;color:#4B5563;font-style:italic">{t.get('recommended_response','')}</td>
                </tr>"""
                for t in threats
            )
            return f"""<table style="width:100%;border-collapse:collapse;font-size:14px">
                <tr style="background:#F9FAFB">
                    <th style="padding:8px;text-align:left;font-size:12px;color:#6B7280">SEVERITY</th>
                    <th style="padding:8px;text-align:left;font-size:12px;color:#6B7280">THREAT</th>
                    <th style="padding:8px;text-align:left;font-size:12px;color:#6B7280">RECOMMENDED RESPONSE</th>
                </tr>{rows}</table>"""

        def render_pipeline():
            if not opps:
                return "<p>No active opportunities this period.</p>"
            items = "".join(
                f"""<li style="margin-bottom:12px">
                    <strong>{o.get('country')} — {o.get('service_line','')}</strong>: {o.get('opportunity','')} 
                    <br><span style="color:#1D4ED8;font-weight:600">{o.get('estimated_value','')}</span>
                    <span style="color:#6B7280"> · {o.get('timeline','')} · Confidence: {o.get('confidence','')}</span>
                </li>"""
                for o in opps
            )
            return f"<ul style='padding-left:20px;line-height:1.8'>{items}</ul>"

        def render_regulatory():
            if not regulatory:
                return "<p>No significant regulatory changes this period.</p>"
            items = "".join(
                f"""<li style="margin-bottom:12px">
                    <strong>[{r.get('country')}] {r.get('regulator','')}</strong>: {r.get('update','')}
                    <br><em style="color:{'#10B981' if r.get('impact')=='POSITIVE' else '#EF4444' if r.get('impact')=='NEGATIVE' else '#6B7280'}">
                    Impact: {r.get('impact','')} — {r.get('descasio_implication','')}</em>
                </li>"""
                for r in regulatory
            )
            return f"<ul style='padding-left:20px;line-height:1.8'>{items}</ul>"

        def render_recs():
            if not recs:
                return "<p>No strategic recommendations this period.</p>"
            items = "".join(
                f"""<li style="margin-bottom:16px;padding:12px;background:#F9FAFB;border-left:3px solid #1D4ED8;border-radius:4px">
                    <strong>{r.get('action','')}</strong><br>
                    <span style="color:#6B7280">{r.get('rationale','')}</span><br>
                    <span style="font-size:12px;color:#1D4ED8">Owner: {r.get('owner','')} · {r.get('timeline','')}</span>
                </li>"""
                for r in recs
            )
            return f"<ol style='padding-left:20px;list-style:none'>{items}</ol>"

        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#111827;background:#F3F4F6;margin:0;padding:0">
<div style="max-width:680px;margin:24px auto;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">
  
  <!-- Header -->
  <div style="background:#0D1B2A;padding:28px 32px">
    <div style="font-size:11px;color:#94A3B8;letter-spacing:2px;text-transform:uppercase;margin-bottom:4px">DESCASIO INTELLIGENCE HUB</div>
    <div style="font-size:22px;font-weight:700;color:#fff">Strategic Intelligence Brief</div>
    <div style="font-size:14px;color:#94A3B8;margin-top:4px">{month} · Confidential — Executive Distribution Only</div>
    <div style="display:inline-block;margin-top:16px;padding:6px 14px;background:{sentiment_color};border-radius:4px;font-size:12px;font-weight:700;color:#fff;letter-spacing:1px">MARKET SENTIMENT: {sentiment}</div>
  </div>
  
  <!-- Executive Summary -->
  <div style="padding:28px 32px;border-bottom:1px solid #E5E7EB">
    <div style="font-size:11px;color:#6B7280;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px">EXECUTIVE SUMMARY</div>
    <p style="font-size:15px;line-height:1.7;color:#374151;margin:0">{summary}</p>
  </div>
  
  <!-- Key Threats -->
  <div style="padding:28px 32px;border-bottom:1px solid #E5E7EB">
    <div style="font-size:11px;color:#6B7280;letter-spacing:2px;text-transform:uppercase;margin-bottom:16px">KEY THREATS</div>
    {render_threats()}
  </div>
  
  <!-- Opportunity Pipeline -->
  <div style="padding:28px 32px;border-bottom:1px solid #E5E7EB">
    <div style="font-size:11px;color:#6B7280;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px">OPPORTUNITY PIPELINE</div>
    {render_pipeline()}
  </div>
  
  <!-- Regulatory Landscape -->
  <div style="padding:28px 32px;border-bottom:1px solid #E5E7EB">
    <div style="font-size:11px;color:#6B7280;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px">REGULATORY LANDSCAPE</div>
    {render_regulatory()}
  </div>
  
  <!-- Hyperscaler Dynamics -->
  <div style="padding:28px 32px;border-bottom:1px solid #E5E7EB">
    <div style="font-size:11px;color:#6B7280;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px">HYPERSCALER DYNAMICS</div>
    <p style="font-size:14px;line-height:1.7;color:#374151;white-space:pre-line;margin:0">{hyperscaler}</p>
  </div>
  
  <!-- Competitor Intelligence -->
  <div style="padding:28px 32px;border-bottom:1px solid #E5E7EB">
    <div style="font-size:11px;color:#6B7280;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px">COMPETITOR INTELLIGENCE</div>
    <p style="font-size:14px;line-height:1.7;color:#374151;white-space:pre-line;margin:0">{competitor_intel}</p>
  </div>
  
  <!-- Strategic Recommendations -->
  <div style="padding:28px 32px;border-bottom:1px solid #E5E7EB">
    <div style="font-size:11px;color:#6B7280;letter-spacing:2px;text-transform:uppercase;margin-bottom:12px">STRATEGIC RECOMMENDATIONS</div>
    {render_recs()}
  </div>
  
  <!-- Footer -->
  <div style="padding:20px 32px;background:#F9FAFB">
    <p style="font-size:12px;color:#9CA3AF;margin:0;line-height:1.6">
      Generated by Descasio Intelligence Hub (DAIA) · {datetime.utcnow().strftime("%d %B %Y %H:%M UTC")}<br>
      Powered by Claude AI · This briefing is confidential and for internal use only.
    </p>
  </div>
</div>
</body></html>"""

    def _render_text_brief(self, briefing: Dict) -> str:
        """Plain-text fallback for email clients that don't render HTML."""
        month = briefing.get("briefing_month", "")
        lines = [
            f"DESCASIO STRATEGIC INTELLIGENCE BRIEF — {month}",
            "=" * 60,
            "",
            f"MARKET SENTIMENT: {briefing.get('market_sentiment', '')}",
            "",
            "EXECUTIVE SUMMARY",
            "-" * 40,
            briefing.get("executive_summary", ""),
            "",
            "KEY THREATS",
            "-" * 40,
        ]
        for t in briefing.get("key_threats", []):
            lines.append(f"[{t.get('severity')}] {t.get('threat', '')}")
            lines.append(f"  → Response: {t.get('recommended_response', '')}")
            lines.append("")

        lines += ["", "STRATEGIC RECOMMENDATIONS", "-" * 40]
        for r in briefing.get("strategic_recommendations", []):
            lines.append(f"• {r.get('action', '')}")
            lines.append(f"  Owner: {r.get('owner', '')} · {r.get('timeline', '')}")
            lines.append("")

        lines.append(f"\nGenerated by Descasio Intelligence Hub · {datetime.utcnow().strftime('%d %B %Y')}")
        return "\n".join(lines)
