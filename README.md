# Descasio Market Intelligence Hub — Setup Guide

Autonomous pan-African market intelligence system tracking tenders, competitor moves, 
and regulatory changes across Nigeria, Kenya, Ghana, and Uganda. Delivers tactical 
signals to the Sales team via Zoho CRM + Slack, and monthly strategic briefs to 
C-Suite via email.

---

## Architecture

```
[INGESTION]                    [PROCESSING]         [DELIVERY]
BPP Nigeria ──────────────┐
PPRA Kenya ────────────────┤                         ┌── Zoho CRM (Sales)
PPA Ghana ─────────────────┼──► AI Synthesizer ──────┼── Slack #bd-intel
PPDA Uganda ───────────────┤   (Claude claude-sonnet-4-6)       ├── Slack #leadership
TechCabal RSS ─────────────┤                         └── Email (C-Suite)
Competitor career pages ───┤
NDPC / ODPC / DPA news ───┘
```

---

## Prerequisites

- Python 3.12+
- AWS account (for Lambda + SES)
- Zoho CRM account (Professional plan or above)
- Anthropic API key
- Slack workspace with incoming webhooks enabled

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/descasio/market-intel.git
cd market-intel
pip install -r requirements.txt
playwright install chromium  # For JS-heavy government portals
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Zoho CRM Setup (One-time)

**Step 1: Create the custom module**
1. Go to Zoho CRM Settings → Modules and Fields → Create New Module
2. Module name: `Market Intelligence` (API name: `Market_Intelligence`)
3. Singular: `Market Intelligence Record`

**Step 2: Add custom fields** (Settings → Modules → Market Intelligence → Fields)

| Field Label | Field Name | Type |
|---|---|---|
| Signal ID | Signal_ID | Single Line |
| Signal Type | Signal_Type | Picklist |
| Country | Country | Picklist |
| Priority | Priority | Picklist |
| Revenue Opportunity | Revenue_Opportunity | Single Line |
| Service Line | Service_Line | Picklist |
| Summary | Summary | Multi-line Text |
| Battlecard Point 1 | Battlecard_Point_1 | Multi-line Text |
| Battlecard Point 2 | Battlecard_Point_2 | Multi-line Text |
| Battlecard Point 3 | Battlecard_Point_3 | Multi-line Text |
| Deadline | Deadline | Date |
| Source URL | Source_URL | URL |
| Source Name | Source_Name | Single Line |
| Relevance Score | Relevance_Score | Integer |
| Pushed At | Pushed_At | Date Time |

**Picklist values for Signal_Type:** tender, competitor_move, regulatory, market_news  
**Picklist values for Priority:** URGENT, HIGH, MEDIUM, LOW  
**Picklist values for Country:** Nigeria, Kenya, Ghana, Uganda, Pan-Africa  
**Picklist values for Service_Line:** Cloud Migration, Sovereign AI, PlugIQ BPA, Managed Services, Cybersecurity, Consulting  

**Step 3: Generate Zoho OAuth credentials**
1. Go to https://api-console.zoho.com
2. Create a new Self Client
3. Generate code with scopes: `ZohoCRM.modules.ALL,ZohoCRM.settings.ALL,ZohoCRM.coql.READ`
4. Exchange code for refresh token (see Zoho docs)
5. Add credentials to `.env`

### 4. Validate setup

```bash
python -m orchestrator.pipeline --mode validate
```

### 5. Run locally

```bash
# Single sales scan
python -m orchestrator.pipeline --mode sales

# Monthly exec briefing
python -m orchestrator.pipeline --mode exec

# Continuous scheduler (for EC2)
python -m orchestrator.pipeline --mode scheduled
```

---

## AWS Lambda Deployment

### Package and deploy

```bash
# Build deployment package
pip install -r requirements.txt -t ./package
cp -r config ingestion processing delivery orchestrator deploy ./package/
cd package && zip -r9 ../descasio-intel.zip . && cd ..

# Deploy to Lambda
aws lambda update-function-code \
  --function-name descasio-market-intel \
  --zip-file fileb://descasio-intel.zip

# Set environment variables (one-time)
aws lambda update-function-configuration \
  --function-name descasio-market-intel \
  --environment "Variables={ANTHROPIC_API_KEY=...,ZOHO_CLIENT_ID=...,...}"
```

### Lambda configuration

| Setting | Value |
|---|---|
| Runtime | Python 3.12 |
| Handler | `deploy.lambda_handler.handler` |
| Memory | 512 MB |
| Timeout | 300 seconds |
| Architecture | arm64 |

### EventBridge rules

Create two rules in AWS EventBridge:

```
Rule 1 — Sales cycle (every 6 hours):
  Schedule: cron(0 */6 * * ? *)
  Target: Lambda descasio-market-intel
  Input: {"mode": "sales"}

Rule 2 — Monthly exec briefing:
  Schedule: cron(0 5 1 * ? *)
  Target: Lambda descasio-market-intel
  Input: {"mode": "exec"}
```

---

## AWS SES Setup (for exec email)

```bash
# Verify sender domain (one-time)
aws ses verify-domain-identity --domain descasio.io --region eu-west-1

# Add DNS records shown in output, then verify
aws ses get-identity-verification-attributes --identities intelligence@descasio.io

# Request production access if still in sandbox
# (SES sandbox limits to verified addresses only)
```

---

## Zoho Analytics Dashboard (C-Suite)

1. In Zoho Analytics, connect to Zoho CRM as a data source
2. Select the `Market_Intelligence` module
3. Create a new Dashboard with these reports:
   - **Priority Heatmap**: Table grouped by Priority + Country
   - **Pipeline by Service Line**: Bar chart of Revenue_Opportunity by Service_Line  
   - **Signal Volume Trend**: Line chart of record count over time
   - **Country Intelligence Map**: Summary card per country
4. Share dashboard with exec distribution list
5. Set up email schedule: Zoho Analytics → Dashboard → Schedule Reports → Monthly

---

## Adding New Markets

To add a new country (e.g., South Africa):

1. Add procurement sources in `config/settings.py` under `PROCUREMENT_SOURCES`
2. Add regulatory sources under `REGULATORY_SOURCES`
3. Add country to `Config.priority_markets` in `.env` or settings
4. Add new parser in `ingestion/scrapers.py` (or use `_parse_generic`)
5. Update Zoho `Country` picklist with the new country

---

## Troubleshooting

**Zoho 401 errors:** Refresh token expired — regenerate at https://api-console.zoho.com

**Empty scraper results:** Government portals change structure periodically.
Check the URL is still valid, then update the CSS selectors in `ingestion/scrapers.py`.

**AI synthesis returning empty:** Check `ANTHROPIC_API_KEY` is valid and has sufficient credits.
Enable DEBUG logging: `export LOG_LEVEL=DEBUG`

**SES email not delivering:** Ensure SES is out of sandbox mode and sender domain is verified.

---

## Project Structure

```
descasio-market-intel/
├── config/
│   └── settings.py          # Configuration + all data source definitions
├── ingestion/
│   └── scrapers.py          # Procurement, news, competitor, regulatory scrapers
├── processing/
│   └── ai_synthesizer.py    # Claude AI synthesis + signal classification
├── delivery/
│   ├── zoho_crm.py          # Zoho CRM OAuth + record management
│   └── alerts.py            # Slack alerts + email briefing
├── orchestrator/
│   └── pipeline.py          # Main orchestrator + CLI
├── deploy/
│   └── lambda_handler.py    # AWS Lambda entry point
├── requirements.txt
├── .env.example
└── README.md
```

---

*Descasio Market Intelligence Hub — Built by the Descasio Engineering Team*  
*Powered by Anthropic Claude · AWS · Zoho CRM*
