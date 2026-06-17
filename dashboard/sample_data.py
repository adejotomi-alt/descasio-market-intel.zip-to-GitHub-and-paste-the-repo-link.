"""
Sample/demo market intelligence signals for the dashboard.
Schema matches processing/ai_synthesizer.py SALES_SYSTEM_PROMPT output.

To swap in live data later:
  - Zoho CRM: replace load_signals() with a call to delivery/zoho_crm.py's
    ZohoCRMClient to pull records from the Market_Intelligence module.
  - Local DB: replace load_signals() with a query against a SQLite/Postgres
    table populated by orchestrator/pipeline.py after each sales cycle.
"""

SAMPLE_SIGNALS = [
    {
        "id": "a1b2c3d4",
        "title": "CBN Cloud Infrastructure Modernisation Tender — Nigeria",
        "signal_type": "tender",
        "country": "Nigeria",
        "priority": "URGENT",
        "revenue_opportunity": "$2M–$4M",
        "descasio_service_line": "Cloud Migration",
        "summary": "Central Bank of Nigeria has issued an RFP for cloud migration and managed services covering core banking infrastructure. Deadline in 12 days.",
        "battlecard_points": [
            "Descasio's AWS Advanced Consulting Partner (ACP) status is a stated eligibility requirement in this RFP.",
            "Local data sovereignty hosting via AWS Cape Town region directly addresses CBN's data residency clause.",
            "Existing managed services track record with two other Nigerian financial institutions strengthens the bid.",
        ],
        "deadline_raw": "30/06/2026",
        "source_url": "https://example.com/tender/001",
        "source": "BPP Nigeria",
        "relevance_score": 9,
    },
    {
        "id": "e5f6g7h8",
        "title": "Liquid Technologies — 8 Cloud Architect Roles Posted in Nairobi",
        "signal_type": "competitor_move",
        "country": "Kenya",
        "priority": "HIGH",
        "revenue_opportunity": "Defensive",
        "descasio_service_line": "Managed Services",
        "summary": "Liquid Intelligent Technologies posted 8 AWS/Azure architect roles in Nairobi, signalling an aggressive push into managed cloud services in East Africa.",
        "battlecard_points": [
            "Descasio's PlugIQ BPA platform offers automation depth Liquid does not currently match.",
            "Faster time-to-deploy for SME clients due to pre-built sovereign AI accelerators.",
            "Risk: Liquid may target our existing Kenyan accounts within 60–90 days.",
        ],
        "deadline_raw": None,
        "source_url": "https://liquid.tech/careers",
        "source": "Competitor Monitor",
        "relevance_score": 7,
    },
    {
        "id": "i9j0k1l2",
        "title": "Ghana Data Protection Act Amendment — Cross-Border Transfer Rules",
        "signal_type": "regulatory",
        "country": "Ghana",
        "priority": "MEDIUM",
        "revenue_opportunity": "N/A",
        "descasio_service_line": "Sovereign AI",
        "summary": "Ghana's Data Protection Commission proposed amendments tightening cross-border data transfer rules, with stricter local-hosting requirements for financial and health sector data.",
        "battlecard_points": [
            "Positions Descasio's local sovereign cloud offering as a compliance-driven differentiator.",
            "Opens conversations with existing fintech clients about data residency upgrades.",
            "Monitor for 90 days — amendment not yet finalised.",
        ],
        "deadline_raw": None,
        "source_url": "https://example.com/regulatory/ghana-dpa",
        "source": "Ghana Data Protection Commission",
        "relevance_score": 6,
    },
    {
        "id": "m3n4o5p6",
        "title": "AWS Announces New Local Zone — Kampala, Uganda",
        "signal_type": "market_news",
        "country": "Uganda",
        "priority": "MEDIUM",
        "revenue_opportunity": "$500K–$1.5M",
        "descasio_service_line": "Cloud Migration",
        "summary": "AWS announced plans for a new Local Zone in Kampala, expected to go live within 12 months, lowering latency for East African workloads.",
        "battlecard_points": [
            "Early positioning as an AWS ACP migration partner ahead of regional infrastructure availability.",
            "Opportunity to pre-sell migration assessments to Ugandan enterprise clients.",
            "Watch hyperscaler partner ecosystem announcements over next two quarters.",
        ],
        "deadline_raw": None,
        "source_url": "https://example.com/news/aws-kampala",
        "source": "AWS Newsroom",
        "relevance_score": 8,
    },
    {
        "id": "q7r8s9t0",
        "title": "Kenya PPRA Tender — County Government ERP Cloud Hosting",
        "signal_type": "tender",
        "country": "Kenya",
        "priority": "HIGH",
        "revenue_opportunity": "$800K–$1.2M",
        "descasio_service_line": "Managed Services",
        "summary": "Nairobi County Government issued a tender for cloud hosting and managed support of its ERP system, with a 20-day submission window.",
        "battlecard_points": [
            "Descasio's existing public-sector references in Kenya strengthen credibility.",
            "Managed Services SLA track record directly matches tender uptime requirements.",
            "Service line cross-sell opportunity into Cybersecurity for the same client.",
        ],
        "deadline_raw": "07/07/2026",
        "source_url": "https://example.com/tender/kenya-erp",
        "source": "PPRA Kenya",
        "relevance_score": 8,
    },
    {
        "id": "u1v2w3x4",
        "title": "Nigeria SEC Cloud Outsourcing Guidelines Update",
        "signal_type": "regulatory",
        "country": "Nigeria",
        "priority": "LOW",
        "revenue_opportunity": "N/A",
        "descasio_service_line": "Consulting",
        "summary": "Nigeria's SEC published updated guidance on cloud outsourcing for capital market operators, formalising existing informal practice.",
        "battlecard_points": [
            "Low immediate impact — guidance largely codifies current compliant practice.",
            "Useful talking point in renewal conversations with capital markets clients.",
            "No action required; monitor for further amendments.",
        ],
        "deadline_raw": None,
        "source_url": "https://example.com/regulatory/sec-nigeria",
        "source": "Nigeria SEC",
        "relevance_score": 4,
    },
]


def load_signals():
    """Return the current list of signals. Swap this out for a live data source."""
    return SAMPLE_SIGNALS
