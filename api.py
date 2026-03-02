"""
api.py - Klient football-data.org API

Dokumentacja: https://www.football-data.org/documentation/quickstart
Darmowy plan: 10 req/min, główne ligi europejskie
"""

import os
import time
import logging
from datetime import datetime, timedelta
from typing import Optional
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Mapowanie lig football-data.org
LEAGUES = {
    "Premier League": {"id": "PL",  "season": 2024},
    "La Liga":        {"id": "PD",  "season": 2024},
    "Bundesliga":     {"id": "BL1", "season": 2024},
    "Serie A":        {"id": "SA",  "season": 2024},
    "Ligue 1":        {"id": "FL1", "season": 2024},
    "Ekstraklasa":    {"id": "PPL", "season": 2024},
    "Champions League": {"id": "CL","season": 2024},
}

class FootballDataClient:
    BASE_URL = "https://api.football-data.org/v4"
    API_KEY  = "0239f610e5474033ba919718886d7688"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("FOOTBALL_DATA_KEY") or self.API_KEY
        self.headers = {"X-Auth-Token": self.api_key}
        self._cache: dict = {}
        self._cache_ttl = 300
        self._last_request_time = 0
        self._min_interval = 6.5  # 10 req/min = 6s między zapytaniami

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _get(self, endpoint: str, params: dict = None, use_cache: bool = True) -> dict:
        params = params or {}
        url = f"{self.BASE_URL}{endpoint}"
        cache_key = f"{url}:{sorted(params.items())}"

        if use_cache and cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if time.time() - ts < self._cache_ttl:
                return data

        self._rate_limit()
        try:
            r = requests.get(url, headers=self.headers, params=params, timeout=15)
            self._last_request_time = time.time()
            if r.status_code == 429:
                logger.warning("Rate limit - czekam 60s")
                time.sleep(60)
                return self._get(endpoint, params, use_cache)
            if r.status_code == 403:
                return {"error": "Brak dostępu - sprawdź klucz API lub plan (liga może być niedostępna w darmowym planie)"}
            r.raise_for_status()
            data = r.json()
            if use_cache:
                self._cache[cache_key] = (time.time(), data)
            return data
        except requests.exceptions.RequestException as e:
            logger.error(f"Błąd zapytania: {e}")
            return {"error": str(e)}

    # ── Wyszukiwanie drużyn ──────────────────────────────────────────────────

    def search_teams(self, name: str) -> list:
        """Wyszukuje drużyny po nazwie."""
        import unicodedata
        name_clean = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        data = self._get(f"/teams", {"name": name_clean})
        if "error" in data:
            return [], data["error"]
        teams = data.get("teams", [])
        return teams, None

    def get_team(self, team_id: int) -> dict:
        """Pobiera szczegóły drużyny."""
        return self._get(f"/teams/{team_id}")

    # ── Mecze drużyny ────────────────────────────────────────────────────────

    def get_team_matches(self, team_id: int, limit: int = 15, status: str = "FINISHED") -> list:
        """Pobiera ostatnie mecze drużyny."""
        data = self._get(f"/teams/{team_id}/matches", {
            "status": status,
            "limit": limit,
        })
        if "error" in data:
            return []
        return data.get("matches", [])

    def get_h2h(self, team1_id: int, team2_id: int, limit: int = 10) -> list:
        """Pobiera mecze H2H między dwoma drużynami."""
        # football-data.org nie ma dedykowanego H2H endpointu
        # Pobieramy mecze team1 i filtrujemy te z team2
        matches = self.get_team_matches(team1_id, limit=50)
        h2h = []
        for m in matches:
            home_id = m.get("homeTeam", {}).get("id")
            away_id = m.get("awayTeam", {}).get("id")
            if team2_id in (home_id, away_id):
                h2h.append(m)
            if len(h2h) >= limit:
                break
        return h2h

    def get_team_statistics(self, team_id: int, season: int = 2024) -> dict:
        """Pobiera statystyki drużyny ze wszystkich meczów sezonu."""
        matches = self.get_team_matches(team_id, limit=30)
        if not matches:
            return {}

        goals_scored = []
        goals_conceded = []
        for m in matches:
            score = m.get("score", {}).get("fullTime", {})
            home_id = m.get("homeTeam", {}).get("id")
            is_home = (home_id == team_id)
            gh = score.get("home")
            ga = score.get("away")
            if gh is None or ga is None:
                continue
            scored   = gh if is_home else ga
            conceded = ga if is_home else gh
            goals_scored.append(scored)
            goals_conceded.append(conceded)

        n = len(goals_scored)
        if n == 0:
            return {}

        avg_scored   = sum(goals_scored) / n
        avg_conceded = sum(goals_conceded) / n

        # Konwertuj do formatu kompatybilnego z model.py
        return {
            "goals": {
                "for":     {"average": {"total": str(round(avg_scored, 2)),   "home": str(round(avg_scored * 1.1, 2)),   "away": str(round(avg_scored * 0.9, 2))}},
                "against": {"average": {"total": str(round(avg_conceded, 2)), "home": str(round(avg_conceded * 0.9, 2)), "away": str(round(avg_conceded * 1.1, 2))}},
            }
        }

    def get_injuries(self, team_id: int, season: int = 2024) -> list:
        """football-data.org nie udostępnia kontuzji w darmowym planie."""
        return []

# ── Adapter dla model.py ──────────────────────────────────────────────────────

def convert_match_to_fixture(match: dict, home_team_id: int) -> dict:
    """
    Konwertuje format meczu football-data.org do formatu kompatybilnego z model.py
    (który oczekuje formatu API-Football).
    """
    score = match.get("score", {}).get("fullTime", {})
    status = match.get("status", "")
    short_status = "FT" if status == "FINISHED" else "NS"

    home_id = match.get("homeTeam", {}).get("id")
    away_id = match.get("awayTeam", {}).get("id")

    return {
        "fixture": {
            "id": match.get("id", 0),
            "date": match.get("utcDate", "")[:10],
            "status": {"short": short_status},
        },
        "teams": {
            "home": {"id": home_id, "name": match.get("homeTeam", {}).get("name", "")},
            "away": {"id": away_id, "name": match.get("awayTeam", {}).get("name", "")},
        },
        "league": {
            "id": match.get("competition", {}).get("id", 0),
            "name": match.get("competition", {}).get("name", ""),
            "country": "",
        },
        "goals": {
            "home": score.get("home"),
            "away": score.get("away"),
        },
    }
