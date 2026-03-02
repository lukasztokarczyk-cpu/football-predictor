"""
api.py - Klient football-data.org API
Darmowe ligi: PL, PD, BL1, SA, FL1, CL, PPL, EL1, DED, PPL
"""

import os
import time
import logging
import unicodedata
from typing import Optional
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_KEY = "0239f610e5474033ba919718886d7688"

# Darmowe ligi w football-data.org
FREE_COMPETITIONS = {
    "PL":  "Premier League (Anglia)",
    "PD":  "La Liga (Hiszpania)",
    "BL1": "Bundesliga (Niemcy)",
    "SA":  "Serie A (Włochy)",
    "FL1": "Ligue 1 (Francja)",
    "CL":  "Champions League",
    "DED": "Eredivisie (Holandia)",
    "PPL": "Primeira Liga (Portugalia)",
}

class FootballDataClient:
    BASE_URL = "https://api.football-data.org/v4"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("FOOTBALL_DATA_KEY") or API_KEY
        self.headers = {"X-Auth-Token": self.api_key}
        self._cache: dict = {}
        self._cache_ttl = 600
        self._last_request_time = 0
        self._min_interval = 7.0  # max ~8 req/min bezpiecznie

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
                time.sleep(60)
                return self._get(endpoint, params, use_cache)
            if r.status_code == 403:
                return {"error": "Brak dostępu do tej ligi w darmowym planie"}
            r.raise_for_status()
            data = r.json()
            if use_cache:
                self._cache[cache_key] = (time.time(), data)
            return data
        except Exception as e:
            return {"error": str(e)}

    def get_competition_teams(self, competition_code: str, season: int = 2024) -> list:
        """Pobiera wszystkie drużyny z danej ligi."""
        data = self._get(f"/competitions/{competition_code}/teams", {"season": season})
        if "error" in data:
            return []
        return data.get("teams", [])

    def get_all_teams(self, season: int = 2024) -> list:
        """Pobiera drużyny ze wszystkich darmowych lig."""
        all_teams = []
        seen_ids = set()
        for code, name in FREE_COMPETITIONS.items():
            teams = self.get_competition_teams(code, season)
            for t in teams:
                tid = t.get("id")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    t["_competition"] = name
                    all_teams.append(t)
        return all_teams

    def search_teams_local(self, query: str, all_teams: list) -> list:
        """Wyszukuje drużyny lokalnie z pobranej listy."""
        q = unicodedata.normalize('NFKD', query.lower()).encode('ascii','ignore').decode('ascii')
        results = []
        for t in all_teams:
            name = t.get("name","")
            short = t.get("shortName","")
            tla = t.get("tla","")
            name_norm = unicodedata.normalize('NFKD', name.lower()).encode('ascii','ignore').decode('ascii')
            short_norm = unicodedata.normalize('NFKD', short.lower()).encode('ascii','ignore').decode('ascii')
            if q in name_norm or q in short_norm or q in tla.lower():
                results.append(t)
        return results

    def get_team_matches(self, team_id: int, limit: int = 15) -> list:
        data = self._get(f"/teams/{team_id}/matches", {"status": "FINISHED", "limit": limit})
        if "error" in data:
            return []
        return data.get("matches", [])

    def get_h2h(self, team1_id: int, team2_id: int, limit: int = 10) -> list:
        matches = self.get_team_matches(team1_id, limit=50)
        h2h = []
        for m in matches:
            home_id = m.get("homeTeam",{}).get("id")
            away_id = m.get("awayTeam",{}).get("id")
            if team2_id in (home_id, away_id):
                h2h.append(m)
            if len(h2h) >= limit:
                break
        return h2h

    def get_team_statistics(self, team_id: int) -> dict:
        matches = self.get_team_matches(team_id, limit=30)
        goals_scored, goals_conceded = [], []
        for m in matches:
            score = m.get("score",{}).get("fullTime",{})
            gh, ga = score.get("home"), score.get("away")
            if gh is None or ga is None: continue
            is_home = (m.get("homeTeam",{}).get("id") == team_id)
            goals_scored.append(gh if is_home else ga)
            goals_conceded.append(ga if is_home else gh)
        n = len(goals_scored)
        if n == 0: return {}
        avg_s = sum(goals_scored)/n
        avg_c = sum(goals_conceded)/n
        return {
            "goals": {
                "for":     {"average": {"total": str(round(avg_s,2)), "home": str(round(avg_s*1.1,2)), "away": str(round(avg_s*0.9,2))}},
                "against": {"average": {"total": str(round(avg_c,2)), "home": str(round(avg_c*0.9,2)), "away": str(round(avg_c*1.1,2))}},
            }
        }

    def get_injuries(self, team_id: int, season: int = 2024) -> list:
        return []


def convert_match_to_fixture(match: dict, ref_team_id: int) -> dict:
    score = match.get("score",{}).get("fullTime",{})
    status = "FT" if match.get("status") == "FINISHED" else "NS"
    home_id = match.get("homeTeam",{}).get("id")
    away_id = match.get("awayTeam",{}).get("id")
    return {
        "fixture": {"id": match.get("id",0), "date": match.get("utcDate","")[:10], "status": {"short": status}},
        "teams": {
            "home": {"id": home_id, "name": match.get("homeTeam",{}).get("name","")},
            "away": {"id": away_id, "name": match.get("awayTeam",{}).get("name","")},
        },
        "league": {"id": match.get("competition",{}).get("id",0), "name": match.get("competition",{}).get("name",""), "country": ""},
        "goals": {"home": score.get("home"), "away": score.get("away")},
    }
