"""Weather for the clock screen, from Open-Meteo (https://open-meteo.com: free, no account, no key).

What leaves this PC: the latitude and longitude you chose (to api.open-meteo.com) and, when you search for a place in the
editor, the text you typed (to geocoding-api.open-meteo.com). Nothing else is sent, and nothing is sent unless a clock
desktop with weather turned on is showing (or you search for a place).
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("companion.weather")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
REFRESH_S = 15 * 60       # a new reading every 15 minutes while the screen is showing
RETRY_S = 2 * 60          # after a failure
USER_AGENT = "swipedeck-companion"

# WMO weather interpretation codes, in a few words that fit on the board's screen.
WMO = {0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
       51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
       61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
       71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
       80: "Light showers", 81: "Showers", 82: "Heavy showers", 85: "Snow showers", 86: "Snow showers",
       95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Thunderstorm, hail"}


def describe(code) -> str:
    return WMO.get(int(code), "Unknown") if isinstance(code, (int, float)) else "Unknown"


def get_json(url: str, timeout: float = 6.0):
    """GET a JSON document. Only the two Open-Meteo hosts are ever asked."""
    host = urllib.parse.urlsplit(url).hostname
    if host not in ("api.open-meteo.com", "geocoding-api.open-meteo.com"):
        raise ValueError(f"not an Open-Meteo address: {host}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(1_000_000).decode("utf-8"))


def forecast_url(lat: float, lon: float, units: str) -> str:
    query = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min", "forecast_days": 1, "timezone": "auto",
        "temperature_unit": "fahrenheit" if units == "f" else "celsius"})
    return f"{FORECAST_URL}?{query}"


def parse_forecast(data: dict, units: str) -> dict:
    """The board's `weather` message fields from an Open-Meteo answer. Raises ValueError for an answer that is not one."""
    try:
        current, daily = data["current"], data["daily"]
        code = int(current["weather_code"])
        return {"ok": True, "temp": round(float(current["temperature_2m"])), "unit": "F" if units == "f" else "C",
                "code": code, "text": describe(code), "hi": round(float(daily["temperature_2m_max"][0])),
                "lo": round(float(daily["temperature_2m_min"][0]))}
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise ValueError(f"unexpected answer from the weather service ({e!r})") from None


def fetch_weather(lat: float, lon: float, units: str = "c", get=get_json) -> dict:
    return parse_forecast(get(forecast_url(lat, lon, units)), units)


def search_places(query: str, get=get_json) -> list[dict]:
    """Up to five places matching `query`: [{"name", "region", "country", "lat", "lon"}]."""
    query = str(query).strip()
    if not 2 <= len(query) <= 80:
        raise ValueError("type at least two letters of the place")
    url = GEOCODE_URL + "?" + urllib.parse.urlencode({"name": query, "count": 5, "language": "en", "format": "json"})
    out = []
    for r in get(url).get("results") or []:
        try:
            out.append({"name": str(r["name"]), "region": str(r.get("admin1") or ""), "country": str(r.get("country") or ""),
                        "lat": round(float(r["latitude"]), 4), "lon": round(float(r["longitude"]), 4)})
        except (KeyError, TypeError, ValueError):
            continue
    return out


class WeatherFeed:
    """Fetches the weather on a thread of its own while `set_active(True)`, and keeps the latest answer."""

    def __init__(self, get=get_json, clock=None):
        self._get = get
        self._lock = threading.Lock()
        self._place: dict | None = None
        self._units = "c"
        self._active = threading.Event()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._result: dict | None = None
        self._seq = 0
        self._thread: threading.Thread | None = None
        self._next_fetch = 0.0
        import time
        self._now = clock or time.monotonic

    def configure(self, place: dict | None, units: str = "c") -> None:
        """place: {"name", "lat", "lon"} or None. A change makes the next poll fetch at once."""
        with self._lock:
            if place != self._place or units != self._units:
                self._place, self._units = (dict(place) if place else None), units
                self._result = None
                self._next_fetch = 0.0
        self._wake.set()

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="weather", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def set_active(self, on: bool) -> None:
        if on:
            self._active.set()
        else:
            self._active.clear()
        self._wake.set()

    def latest(self) -> tuple[int, dict] | None:
        with self._lock:
            return (self._seq, self._result) if self._result is not None else None

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(1.0)
            self._wake.clear()
            if not self._active.is_set():
                continue
            with self._lock:
                place, units, due = self._place, self._units, self._next_fetch
            if place is None or self._now() < due:
                continue
            try:
                result = fetch_weather(place["lat"], place["lon"], units, self._get)
                result["place"] = str(place.get("name") or "")
                wait = REFRESH_S
            except (OSError, ValueError, urllib.error.URLError) as e:  # no network, a hiccup of the service
                log.warning("weather: %s", e)
                result, wait = {"ok": False, "error": "Weather unavailable"}, RETRY_S
            with self._lock:
                if place == self._place:  # (the place may have been changed while we were fetching)
                    self._result, self._next_fetch = result, self._now() + wait
                    self._seq += 1
