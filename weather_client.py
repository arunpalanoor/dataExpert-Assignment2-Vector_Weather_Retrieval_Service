"""
Client for the National Weather Service (NWS) API (api.weather.gov).

No API key is required - NWS only asks for a descriptive User-Agent
identifying the application and a contact (see
https://www.weather.gov/documentation/services-web-api).

Free-text locations (e.g. "Chicago, IL") are resolved to lat/lon via the
Open-Meteo Geocoding API (also free, no API key: https://open-meteo.com/en/docs/geocoding-api)
before being resolved to an NWS grid point. "lat,lon" input is used as-is.
"""

import hashlib
import os
from typing import Any

import requests

_BASE_URL = os.environ.get("NWS_API_BASE_URL", "https://api.weather.gov")
_USER_AGENT = os.environ.get(
    "NWS_USER_AGENT", "vector-weather-retrieval-service (contact: set NWS_USER_AGENT)"
)
_GEOCODE_URL = os.environ.get(
    "GEOCODE_API_URL", "https://geocoding-api.open-meteo.com/v1/search"
)

_DEFAULT_TIMEOUT = 30

_US_STATE_ABBREVIATIONS = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska",
    "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island",
    "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


class WeatherClient:
    """Thin wrapper around the NWS API with a retry-friendly session."""

    def __init__(self, base_url: str | None = None, timeout: int = _DEFAULT_TIMEOUT):
        self.base_url = (base_url or _BASE_URL).rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": _USER_AGENT,
                "Accept": "application/geo+json",
            }
        )

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self._session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def geocode(self, raw_location: str) -> tuple[float, float]:
        """
        Resolve a free-text "City" or "City, ST" location to (lat, lon) via
        the Open-Meteo geocoding API. When a state abbreviation is present,
        prefer the result whose admin1 (state) name matches it - Open-Meteo
        only takes a plain "name" query param, so disambiguation happens
        client-side against the candidate list.
        """
        name_part, _, admin_part = raw_location.partition(",")
        name = name_part.strip()
        admin = admin_part.strip()
        admin_full = _US_STATE_ABBREVIATIONS.get(admin.upper(), admin) if admin else None

        resp = requests.get(
            _GEOCODE_URL,
            params={"name": name, "count": 10, "language": "en", "format": "json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        results = resp.json().get("results") or []
        if not results:
            raise ValueError(f"Could not geocode location: {raw_location!r}")

        if admin_full:
            for result in results:
                if result.get("admin1", "").lower() == admin_full.lower():
                    return result["latitude"], result["longitude"]

        top = results[0]
        return top["latitude"], top["longitude"]

    def _parse_or_geocode(self, raw_input: str) -> tuple[float, float]:
        """Accepts either a "lat,lon" pair or a free-text location name."""
        parts = raw_input.split(",")
        if len(parts) == 2:
            try:
                return float(parts[0].strip()), float(parts[1].strip())
            except ValueError:
                pass
        return self.geocode(raw_input)

    def resolve_grid_point(self, latitude: float, longitude: float) -> dict:
        """GET /points/{lat},{lon} -> NWS office id + grid x/y for that point."""
        data = self.get(f"/points/{latitude},{longitude}")
        props = data.get("properties", {})
        return {
            "grid_office": props.get("gridId"),
            "grid_x": props.get("gridX"),
            "grid_y": props.get("gridY"),
        }

    def resolve_location(self, raw_input: str) -> dict:
        """
        Resolve a raw location string (free-text or "lat,lon") into the
        fields needed for a `locations` table row: raw_input, latitude,
        longitude, grid_office, grid_x, grid_y.
        """
        latitude, longitude = self._parse_or_geocode(raw_input)
        grid = self.resolve_grid_point(latitude, longitude)
        return {
            "raw_input": raw_input,
            "latitude": latitude,
            "longitude": longitude,
            **grid,
        }

    def get_active_alerts(self, latitude: float, longitude: float) -> list[dict]:
        """GET /alerts/active?point={lat},{lon} -> alert features covering this point."""
        data = self.get("/alerts/active", params={"point": f"{latitude},{longitude}"})
        return data.get("features", [])

    def get_forecast_periods(self, grid_office: str, grid_x: int, grid_y: int) -> list[dict]:
        """GET /gridpoints/{office}/{x},{y}/forecast -> narrative forecast periods."""
        data = self.get(f"/gridpoints/{grid_office}/{grid_x},{grid_y}/forecast")
        return data.get("properties", {}).get("periods", [])

    def fetch_documents_for_location(self, location: dict) -> list[dict]:
        """
        Fetch active alerts + forecast periods for an already-resolved
        location (a dict with at least id, latitude, longitude, grid_office,
        grid_x, grid_y - as returned by resolve_location() plus its
        assigned location id) and normalize them into weather_documents rows.
        """
        location_id = location["id"]
        documents = []

        alerts = self.get_active_alerts(location["latitude"], location["longitude"])
        documents.extend(normalize_alert(feature, location_id) for feature in alerts)

        periods = self.get_forecast_periods(
            location["grid_office"], location["grid_x"], location["grid_y"]
        )
        documents.extend(normalize_forecast_period(period, location_id) for period in periods)

        return documents


def normalize_alert(feature: dict, location_id: str) -> dict:
    """
    Normalize a single NWS alert GeoJSON feature into a weather_documents
    row. Alerts already carry a stable NWS-issued id, used directly as the
    dedup key.
    """
    props = feature.get("properties", {})
    narrative_text = "\n\n".join(
        part for part in (props.get("description"), props.get("instruction")) if part
    )
    return {
        "id": props.get("id"),
        "location_id": location_id,
        "source_type": "alert",
        "headline": props.get("headline") or props.get("event"),
        "narrative_text": narrative_text,
        "issued_at": props.get("sent"),
        "effective_at": props.get("effective"),
        "payload": feature,
    }


def normalize_forecast_period(period: dict, location_id: str) -> dict:
    """
    Normalize a single NWS forecast period into a weather_documents row.
    Forecast periods have no natural id, so synthesize a stable one from
    location + period name + startTime (a re-sync of the same period
    produces the same id, enabling upsert-based dedup).
    """
    dedup_key = f"{location_id}:{period.get('name')}:{period.get('startTime')}"
    doc_id = hashlib.sha256(dedup_key.encode("utf-8")).hexdigest()
    return {
        "id": doc_id,
        "location_id": location_id,
        "source_type": "forecast",
        "headline": period.get("name"),
        "narrative_text": period.get("detailedForecast"),
        "issued_at": period.get("startTime"),
        "effective_at": period.get("endTime"),
        "payload": period,
    }
