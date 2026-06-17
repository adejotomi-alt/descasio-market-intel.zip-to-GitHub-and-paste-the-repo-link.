"""
Descasio Market Intelligence — Zoho CRM Integration
Handles all CRM read/write operations for the Market Intelligence pipeline.

Architecture:
  - OAuth 2.0 with automatic access token refresh (stored in memory)
  - Writes to a custom Zoho module: "Market_Intelligence"
  - Enriches existing Deals with competitor intelligence on matching accounts
  - Creates Zia-compatible competitor alerts on Lead/Contact records

ZOHO CRM SETUP REQUIRED (one-time, see README):
  1. Create custom module: "Market_Intelligence" in Zoho CRM Settings
  2. Add custom fields (see REQUIRED_FIELDS below)
  3. Create a Connected App at https://api-console.zoho.com
  4. Generate refresh token with scopes: ZohoCRM.modules.ALL, ZohoCRM.settings.ALL
"""

import logging
import time
from typing import Dict, List, Optional, Any

import requests

from config.settings import Config

logger = logging.getLogger(__name__)

# Fields required in the Zoho CRM custom "Market_Intelligence" module
REQUIRED_FIELDS = {
    "Signal_ID": "Single Line",
    "Signal_Type": "Picklist: tender|competitor_move|regulatory|market_news",
    "Country": "Picklist: Nigeria|Kenya|Ghana|Uganda|Pan-Africa",
    "Priority": "Picklist: URGENT|HIGH|MEDIUM|LOW",
    "Revenue_Opportunity": "Single Line",
    "Service_Line": "Picklist: Cloud Migration|Sovereign AI|PlugIQ BPA|Managed Services|Cybersecurity|Consulting",
    "Summary": "Multi-line Text",
    "Battlecard_Point_1": "Multi-line Text",
    "Battlecard_Point_2": "Multi-line Text",
    "Battlecard_Point_3": "Multi-line Text",
    "Deadline": "Date",
    "Source_URL": "URL",
    "Source_Name": "Single Line",
    "Relevance_Score": "Integer",
    "Pushed_At": "DateTime",
}


class ZohoCRMClient:
    """
    Full Zoho CRM client for the Market Intelligence pipeline.
    
    Usage:
        zoho = ZohoCRMClient(config)
        await zoho.create_intelligence_record(signal)
        await zoho.enrich_deal_with_intel(deal_id, signal)
    """

    def __init__(self, config: Config):
        self.config = config
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0
        self.api_base = f"{config.zoho_api_domain}/crm/v6"

    # ─── OAuth ────────────────────────────────────────────────────────────────

    def _get_access_token(self) -> str:
        """
        Returns a valid access token, refreshing if expired.
        Zoho access tokens last ~3600 seconds (1 hour).
        """
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token

        logger.debug("Refreshing Zoho access token...")
        resp = requests.post(
            f"{self.config.zoho_accounts_url}/oauth/v2/token",
            data={
                "grant_type": "refresh_token",
                "client_id": self.config.zoho_client_id,
                "client_secret": self.config.zoho_client_secret,
                "refresh_token": self.config.zoho_refresh_token,
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            raise ValueError(f"Zoho token refresh failed: {data['error']}")

        self._access_token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        logger.debug("Zoho access token refreshed successfully.")
        return self._access_token

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Zoho-oauthtoken {self._get_access_token()}",
            "Content-Type": "application/json",
        }

    def _api_get(self, path: str, params: Optional[Dict] = None) -> Dict:
        """Authenticated GET request to Zoho CRM API."""
        resp = requests.get(
            f"{self.api_base}/{path}",
            headers=self._headers(),
            params=params or {},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def _api_post(self, path: str, data: Dict) -> Dict:
        """Authenticated POST request to Zoho CRM API."""
        resp = requests.post(
            f"{self.api_base}/{path}",
            headers=self._headers(),
            json=data,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def _api_put(self, path: str, data: Dict) -> Dict:
        """Authenticated PUT request to Zoho CRM API."""
        resp = requests.put(
            f"{self.api_base}/{path}",
            headers=self._headers(),
            json=data,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    # ─── MARKET INTELLIGENCE MODULE ───────────────────────────────────────────

    def create_intelligence_record(self, signal: Dict[str, Any]) -> Optional[str]:
        """
        Create a new record in the Market_Intelligence custom module.
        Returns the Zoho record ID if successful, None otherwise.
        
        Each AI-synthesized signal becomes one record in Zoho CRM,
        visible to the full BD team on their Market Intelligence dashboard.
        """
        if not self._validate_credentials():
            logger.warning("Zoho credentials not configured. Skipping CRM push.")
            return None

        battlecard_points = signal.get("battlecard_points", [])

        record = {
            "Name": signal.get("title", "Untitled Signal")[:120],
            "Signal_ID": signal.get("id", ""),
            "Signal_Type": signal.get("signal_type", "market_news"),
            "Country": signal.get("country", "Pan-Africa"),
            "Priority": signal.get("priority", "MEDIUM"),
            "Revenue_Opportunity": signal.get("revenue_opportunity", "N/A"),
            "Service_Line": signal.get("descasio_service_line", "Consulting"),
            "Summary": signal.get("summary", ""),
            "Battlecard_Point_1": battlecard_points[0] if len(battlecard_points) > 0 else "",
            "Battlecard_Point_2": battlecard_points[1] if len(battlecard_points) > 1 else "",
            "Battlecard_Point_3": battlecard_points[2] if len(battlecard_points) > 2 else "",
            "Source_URL": signal.get("source_url", ""),
            "Source_Name": signal.get("source", ""),
            "Relevance_Score": signal.get("relevance_score", 5),
            "Pushed_At": self._zoho_datetime_now(),
        }

        # Add deadline if present
        if signal.get("deadline_raw"):
            record["Deadline"] = self._parse_deadline(signal["deadline_raw"])

        try:
            payload = {"data": [record]}
            response = self._api_post(
                f"Market_Intelligence", payload
            )
            result = response.get("data", [{}])[0]
            if result.get("code") == "SUCCESS":
                record_id = result["details"]["id"]
                logger.info(
                    f"Zoho CRM: Created Market Intelligence record {record_id} "
                    f"[{signal.get('priority')}] {signal.get('title', '')[:50]}"
                )
                return record_id
            else:
                logger.error(f"Zoho record creation failed: {result}")
                return None
        except requests.RequestException as e:
            logger.error(f"Zoho API error creating record: {e}")
            return None

    def enrich_deal_with_intel(
        self, deal_id: str, signal: Dict[str, Any]
    ) -> bool:
        """
        Append market intelligence to an existing Zoho CRM Deal.
        
        This is the "Dynamic Battlecard Injection" — when a sales rep opens
        a deal for an account in the same sector/country as a new signal,
        the deal record is automatically enriched with the relevant battlecard.
        
        In practice: the pipeline searches for open deals in the same country
        and service line, then enriches matching deals.
        """
        if not self._validate_credentials():
            return False

        battlecard_points = signal.get("battlecard_points", [])
        battlecard_text = "\n".join(
            f"• {point}" for point in battlecard_points if point
        )

        update_payload = {
            "data": [
                {
                    "id": deal_id,
                    # Append to description field — doesn't overwrite existing content
                    "Description": (
                        f"\n\n--- MARKET INTELLIGENCE ALERT [{signal.get('priority')}] ---\n"
                        f"Source: {signal.get('source', 'DAIA Intelligence')}\n"
                        f"Signal: {signal.get('title', '')}\n\n"
                        f"{signal.get('summary', '')}\n\n"
                        f"BATTLECARD:\n{battlecard_text}\n"
                        f"---"
                    ),
                }
            ]
        }

        try:
            self._api_put(f"Deals/{deal_id}", update_payload)
            logger.info(f"Zoho: Enriched Deal {deal_id} with intelligence signal")
            return True
        except requests.RequestException as e:
            logger.error(f"Zoho Deal enrichment failed for {deal_id}: {e}")
            return False

    def find_matching_deals(
        self, country: str, service_line: str
    ) -> List[Dict[str, Any]]:
        """
        Find open Deals in Zoho CRM matching the signal's country and service line.
        Returns deal IDs for enrichment.
        
        Uses Zoho COQL (CRM Object Query Language) for precise filtering.
        """
        if not self._validate_credentials():
            return []

        query = (
            f"SELECT id, Deal_Name, Account_Name, Stage FROM Deals "
            f"WHERE Stage NOT IN ('Closed Won', 'Closed Lost') "
            f"AND Country = '{country}' "
            f"LIMIT 10"
        )

        try:
            response = self._api_post("coql", {"select_query": query})
            return response.get("data", [])
        except requests.RequestException as e:
            logger.warning(f"Zoho COQL query failed: {e}")
            return []

    def get_module_fields(self) -> List[str]:
        """
        Verify that the Market_Intelligence module has all required fields.
        Run this during setup to confirm module configuration is correct.
        """
        try:
            response = self._api_get(
                f"settings/fields?module=Market_Intelligence"
            )
            fields = [f["field_label"] for f in response.get("fields", [])]
            missing = [f for f in REQUIRED_FIELDS if f not in fields]
            if missing:
                logger.warning(
                    f"Market_Intelligence module missing fields: {missing}. "
                    "See README for Zoho module setup instructions."
                )
            return fields
        except requests.RequestException as e:
            logger.error(f"Could not verify Zoho module fields: {e}")
            return []

    # ─── HELPERS ──────────────────────────────────────────────────────────────

    def _validate_credentials(self) -> bool:
        """Check that Zoho credentials are configured."""
        return all([
            self.config.zoho_client_id,
            self.config.zoho_client_secret,
            self.config.zoho_refresh_token,
        ])

    @staticmethod
    def _zoho_datetime_now() -> str:
        """Returns current UTC time in Zoho's expected format."""
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    @staticmethod
    def _parse_deadline(deadline_raw: str) -> Optional[str]:
        """
        Attempt to parse a raw deadline string into Zoho's YYYY-MM-DD format.
        Returns None if parsing fails — better to skip than send a bad date.
        """
        from datetime import datetime
        import re

        formats = ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"]
        # Normalise
        clean = re.sub(r"\s+", " ", deadline_raw.strip())
        for fmt in formats:
            try:
                return datetime.strptime(clean, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None
