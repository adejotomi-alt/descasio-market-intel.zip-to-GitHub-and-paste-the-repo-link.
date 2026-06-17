"""
Descasio Market Intelligence — Configuration & Data Sources
All settings pulled from environment variables. All source URLs in one place.
To add a new market: extend PROCUREMENT_SOURCES, NEWS_SOURCES, and REGULATORY_SOURCES.
"""

import os
from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class Config:
    """
    Central configuration object. Instantiate once and pass through the pipeline.
    Call Config().validate() at startup to surface any missing credentials.
    """

    # ─── Anthropic / Claude ───────────────────────────────────────────────────
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )
    claude_model: str = "claude-sonnet-4-6"

    # ─── Zoho CRM ─────────────────────────────────────────────────────────────
    zoho_client_id: str = field(
        default_factory=lambda: os.environ.get("ZOHO_CLIENT_ID", "")
    )
    zoho_client_secret: str = field(
        default_factory=lambda: os.environ.get("ZOHO_CLIENT_SECRET", "")
    )
    zoho_refresh_token: str = field(
        default_factory=lambda: os.environ.get("ZOHO_REFRESH_TOKEN", "")
    )
    zoho_api_domain: str = "https://www.zohoapis.com"
    zoho_accounts_url: str = "https://accounts.zoho.com"
    # Custom module name — must be created in Zoho CRM first (see README)
    zoho_intel_module: str = "Market_Intelligence"

    # ─── Slack ────────────────────────────────────────────────────────────────
    slack_sales_webhook: str = field(
        default_factory=lambda: os.environ.get("SLACK_SALES_WEBHOOK", "")
    )
    slack_exec_webhook: str = field(
        default_factory=lambda: os.environ.get("SLACK_EXEC_WEBHOOK", "")
    )

    # ─── AWS SES ──────────────────────────────────────────────────────────────
    ses_region: str = field(
        default_factory=lambda: os.environ.get("AWS_REGION", "eu-west-1")
    )
    ses_sender: str = field(
        default_factory=lambda: os.environ.get("SES_SENDER", "intelligence@descasio.io")
    )
    exec_recipients: List[str] = field(
        default_factory=lambda: [
            e.strip()
            for e in os.environ.get("EXEC_RECIPIENTS", "").split(",")
            if e.strip()
        ]
    )

    # ─── Target Markets ───────────────────────────────────────────────────────
    priority_markets: List[str] = field(
        default_factory=lambda: ["Nigeria", "Kenya", "Ghana", "Uganda"]
    )

    # ─── Timing ───────────────────────────────────────────────────────────────
    sales_cycle_hours: int = 6           # How often to run the sales intelligence scan
    exec_briefing_day: int = 1           # Day-of-month to send the C-Suite briefing
    lookback_days_sales: int = 1         # Days to look back in sales cycles
    lookback_days_exec: int = 30         # Days to look back for monthly brief

    # ─── Scraping Behaviour ───────────────────────────────────────────────────
    request_timeout: int = 30
    max_retries: int = 3
    rate_limit_delay: float = 1.5        # Seconds between requests to same domain
    user_agent: str = (
        "Mozilla/5.0 (compatible; DescasioIntelBot/1.0; +https://descasio.io)"
    )

    # ─── Descasio Context (injected into every AI prompt) ─────────────────────
    company_context: str = """
    Descasio is a leading African IT services and cloud solutions company, headquartered 
    in Lagos, Nigeria, operating across Sub-Saharan Africa. Key service lines:
    
    1. CLOUD MIGRATION — AWS Advanced Consulting Partner. Full-stack cloud migration, 
       architecture, and optimisation for enterprise clients.
    2. SOVEREIGN AI — In-country AI infrastructure and data sovereignty solutions. 
       Particularly relevant as African nations tighten data localisation regulations.
    3. PlugIQ BPA — Business process automation platform. Workflow digitisation, 
       RPA, and system integration for enterprise and government clients.
    4. MANAGED SERVICES — 24/7 cloud operations, NOC services, SLA-driven infrastructure 
       management, and cybersecurity operations.
    5. CONSULTING — Digital transformation advisory, IT strategy, and programme delivery.
    
    Primary client sectors: Banking & Financial Services (FSI), Telecoms, Government/Public 
    Sector, Energy (Oil & Gas), Large Enterprises.
    
    Core competitive differentiators:
    - Only African-headquartered AWS Advanced Consulting Partner with in-country cloud nodes
    - Deep regulatory expertise across NDPR (Nigeria), ODPC (Kenya), DPA (Ghana)
    - Local escalation and delivery — vs. offshore-first competitors like Big 4 and global SIs
    - 100K+ deployed cloud licenses across Africa
    - 12+ years operating in African enterprise markets
    
    Key competitors: Liquid Intelligent Technologies, BCX (South Africa), 
    Deloitte Africa, PwC Africa, KPMG Africa, MTN Business, Airtel Business, 
    Wipro (Africa ops), Infosys BPM.
    
    Priority markets: Nigeria (HQ), Kenya, Ghana, Uganda.
    """

    def validate(self) -> List[str]:
        """Return list of missing required environment variable names."""
        required = {
            "ANTHROPIC_API_KEY": self.anthropic_api_key,
            "ZOHO_CLIENT_ID": self.zoho_client_id,
            "ZOHO_CLIENT_SECRET": self.zoho_client_secret,
            "ZOHO_REFRESH_TOKEN": self.zoho_refresh_token,
            "SLACK_SALES_WEBHOOK": self.slack_sales_webhook,
        }
        return [key for key, val in required.items() if not val]


# ─── DATA SOURCES ─────────────────────────────────────────────────────────────

# Government procurement portals — scraped daily for tender keywords
PROCUREMENT_SOURCES: Dict[str, List[Dict[str, Any]]] = {
    "Nigeria": [
        {
            "name": "Bureau of Public Procurement (BPP)",
            "url": "https://bpp.gov.ng/tenders",
            "api_url": "https://ocds.bpp.gov.ng/api/tenders",
            "type": "html",  # Fallback to HTML if API is unavailable
            "country": "Nigeria",
            "weight": 1.0,  # Highest priority source for Nigeria
        },
        {
            "name": "NITDA Tenders",
            "url": "https://nitda.gov.ng/tenders",
            "type": "html",
            "country": "Nigeria",
            "weight": 0.8,
        },
        {
            "name": "Central Bank of Nigeria Procurement",
            "url": "https://www.cbn.gov.ng/procurementhome.asp",
            "type": "html",
            "country": "Nigeria",
            "weight": 0.9,
        },
    ],
    "Kenya": [
        {
            "name": "PPRA Kenya — Tenders Portal",
            "url": "https://tenders.go.ke/website/tenders/index",
            "type": "html",
            "country": "Kenya",
            "weight": 1.0,
        },
        {
            "name": "Kenya eGP Portal",
            "url": "https://www.supplier.treasury.go.ke",
            "type": "html",
            "country": "Kenya",
            "weight": 0.9,
        },
    ],
    "Ghana": [
        {
            "name": "Public Procurement Authority Ghana",
            "url": "https://ppaghana.org/procurementadverts.php",
            "type": "html",
            "country": "Ghana",
            "weight": 1.0,
        },
        {
            "name": "Ghana Tenders Portal",
            "url": "https://tender.gov.gh",
            "type": "html",
            "country": "Ghana",
            "weight": 0.8,
        },
    ],
    "Uganda": [
        {
            "name": "PPDA Uganda",
            "url": "https://www.ppda.go.ug/procurement-notices/",
            "type": "html",
            "country": "Uganda",
            "weight": 1.0,
        },
        {
            "name": "Uganda eTendering",
            "url": "https://eprocurement.finance.go.ug",
            "type": "html",
            "country": "Uganda",
            "weight": 0.9,
        },
    ],
}

# Keywords used to filter relevant tenders from procurement portals
TENDER_KEYWORDS = [
    # Cloud & Infrastructure
    "cloud", "cloud migration", "cloud infrastructure", "cloud services",
    "data center", "data centre", "colocation", "hosting",
    # AI & Automation
    "artificial intelligence", "AI", "machine learning", "automation",
    "workflow automation", "business process automation", "RPA",
    "digital transformation",
    # Managed Services
    "managed services", "IT services", "managed IT", "outsourcing",
    "NOC", "network operations", "help desk", "service desk",
    # Descasio-specific domains
    "sovereign AI", "sovereign cloud", "data sovereignty", "data residency",
    "cybersecurity", "SIEM", "SOC", "information security",
    # Software & Platforms
    "ERP", "CRM", "enterprise software", "system integration",
    "API", "digital platform",
]

# Pan-African tech media — RSS feeds for news ingestion
NEWS_SOURCES = [
    {
        "name": "TechCabal",
        "rss": "https://techcabal.com/feed/",
        "region": "West Africa",
        "weight": 1.0,
    },
    {
        "name": "Techpoint Africa",
        "rss": "https://techpoint.africa/feed/",
        "region": "West Africa",
        "weight": 1.0,
    },
    {
        "name": "Disrupt Africa",
        "rss": "https://disrupt-africa.com/feed/",
        "region": "Pan-Africa",
        "weight": 0.9,
    },
    {
        "name": "IT News Africa",
        "rss": "https://www.itnewsafrica.com/feed/",
        "region": "Pan-Africa",
        "weight": 0.8,
    },
    {
        "name": "Connecting Africa",
        "rss": "https://www.connectingafrica.com/rss/",
        "region": "Pan-Africa",
        "weight": 0.7,
    },
    {
        "name": "Venture Africa",
        "rss": "https://venturesafrica.com/feed/",
        "region": "Pan-Africa",
        "weight": 0.7,
    },
]

# Competitor profiles — career pages, websites, and LinkedIn slugs
COMPETITOR_SOURCES = {
    "Liquid Intelligent Technologies": {
        "website": "https://liquid.tech",
        "careers": "https://liquid.tech/careers/",
        "hiring_keywords": ["cloud", "AWS", "Azure", "data center", "managed services"],
        "threat_level": "HIGH",
        "notes": "Largest pan-African cloud infrastructure competitor. Expanding NOC capacity in EA.",
    },
    "Deloitte Africa": {
        "careers": "https://www2.deloitte.com/ng/en/careers.html",
        "hiring_keywords": ["AWS", "Azure", "GCP", "cloud architect", "cloud consultant"],
        "threat_level": "HIGH",
        "notes": "Big 4 with expanding cloud practice. Hiring surges precede major pitch cycles.",
    },
    "PwC Africa": {
        "careers": "https://www.pwc.com/ng/en/careers.html",
        "hiring_keywords": ["cloud", "digital", "transformation", "data", "AI"],
        "threat_level": "MEDIUM",
        "notes": "Growing digital advisory. Less delivery capability than Deloitte on cloud.",
    },
    "KPMG Africa": {
        "careers": "https://home.kpmg/ng/en/home/careers.html",
        "hiring_keywords": ["cloud", "digital", "technology", "cyber"],
        "threat_level": "MEDIUM",
        "notes": "Active in FSI sector digital transformation advisory.",
    },
    "BCX": {
        "website": "https://www.bcx.co.za",
        "careers": "https://www.bcx.co.za/careers/",
        "hiring_keywords": ["cloud", "managed services", "NOC", "Africa"],
        "threat_level": "MEDIUM",
        "notes": "SA-based but expanding pan-African managed services presence.",
    },
    "MTN Business": {
        "website": "https://enterprise.mtn.com",
        "hiring_keywords": ["cloud", "enterprise", "B2B", "IoT"],
        "threat_level": "MEDIUM",
        "notes": "Telco-led cloud bundling. Competes primarily on connectivity + cloud.",
    },
    "Airtel Business": {
        "website": "https://www.airtelkbc.com",
        "hiring_keywords": ["cloud", "enterprise", "managed", "B2B"],
        "threat_level": "LOW",
        "notes": "Lower cloud depth than MTN. Primary competitor in East Africa.",
    },
    "Wipro Africa": {
        "careers": "https://careers.wipro.com",
        "hiring_keywords": ["Africa", "Nigeria", "Kenya", "cloud", "SAP"],
        "threat_level": "MEDIUM",
        "notes": "Global SI presence. Competes on SAP/Oracle workloads in large enterprise.",
    },
}

# Regulatory bodies — tracked for policy updates that create tailwinds or headwinds
REGULATORY_SOURCES = {
    "Nigeria": [
        {
            "name": "NDPC",
            "full_name": "Nigeria Data Protection Commission",
            "url": "https://ndpc.gov.ng/news",
            "relevance": "HIGH",
            "notes": "Primary data protection regulator. Policy changes create Sovereign AI tailwinds.",
        },
        {
            "name": "NITDA",
            "full_name": "National IT Development Agency",
            "url": "https://nitda.gov.ng/news",
            "relevance": "HIGH",
            "notes": "IT regulation and local content requirements. Affects procurement eligibility.",
        },
        {
            "name": "NCC",
            "full_name": "Nigerian Communications Commission",
            "url": "https://www.ncc.gov.ng/news-and-media-centre",
            "relevance": "MEDIUM",
            "notes": "Telecoms regulation. Relevant for connectivity-bundled cloud services.",
        },
    ],
    "Kenya": [
        {
            "name": "ODPC",
            "full_name": "Office of the Data Protection Commissioner",
            "url": "https://www.odpc.go.ke/news/",
            "relevance": "HIGH",
            "notes": "Kenya data protection regulator. Equivalent to Nigeria's NDPC.",
        },
        {
            "name": "CA Kenya",
            "full_name": "Communications Authority of Kenya",
            "url": "https://ca.go.ke/newsroom/",
            "relevance": "MEDIUM",
            "notes": "ICT regulation. Monitor for cloud localisation requirements.",
        },
    ],
    "Ghana": [
        {
            "name": "DPA Ghana",
            "full_name": "Data Protection Authority Ghana",
            "url": "https://www.dpa.gov.gh/news",
            "relevance": "HIGH",
        },
        {
            "name": "NCA Ghana",
            "full_name": "National Communications Authority",
            "url": "https://www.nca.org.gh/news/",
            "relevance": "MEDIUM",
        },
    ],
    "Uganda": [
        {
            "name": "NITA-U",
            "full_name": "National Information Technology Authority Uganda",
            "url": "https://www.nita.go.ug/news",
            "relevance": "HIGH",
            "notes": "Uganda's digital transformation policy body.",
        },
        {
            "name": "UCC",
            "full_name": "Uganda Communications Commission",
            "url": "https://www.ucc.co.ug/news-and-events/",
            "relevance": "MEDIUM",
        },
    ],
}

# Hyperscaler tracking — AWS, Azure, Google Cloud expansion signals
HYPERSCALER_SOURCES = {
    "AWS": {
        "blog_rss": "https://aws.amazon.com/blogs/aws/feed/",
        "news_rss": "https://aws.amazon.com/about-aws/whats-new/recent/feed/",
        "africa_keywords": ["Africa", "Nigeria", "Kenya", "Ghana", "Uganda", "Cape Town", "Nairobi", "Lagos"],
        "relevant_services": ["Local Zone", "Outpost", "Wavelength", "Direct Connect"],
    },
    "Microsoft Azure": {
        "blog_rss": "https://azure.microsoft.com/en-us/blog/feed/",
        "updates_rss": "https://azure.microsoft.com/en-us/updates/feed/",
        "africa_keywords": ["Africa", "South Africa", "Nigeria", "Egypt"],
    },
    "Google Cloud": {
        "blog_rss": "https://cloud.google.com/feeds/gcp-release-notes.xml",
        "africa_keywords": ["Africa", "Nigeria", "Kenya", "Ghana"],
    },
}
