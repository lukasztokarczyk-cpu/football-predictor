"""
api.py - Klient TheSportsDB API (darmowe, bez rejestracji, Ekstraklasa included)
Dokumentacja: https://www.thesportsdb.com/api.php
"""

import time
import logging
import unicodedata
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_URL = "https://www.thesportsdb.com/api/v1/json/123"

# Liga ID w TheSportsDB
LEAGUES = {
    "Ekstraklasa (Polska)":       "4422",
    "Premier League (Anglia)":    "4328",
    "La Liga (Hiszpania)":        "4335",
    "Bundesliga (Niemcy)":        "4331",
    "Serie A (Włochy)":           "4332",
    "Ligue 1 (Francja)":          "4334",
    "Champions League":           "4480",
    "Eredivisie (Holandia)":      "4337",
    "Primeira Liga (Portugalia)": "4344",
    "I Liga (Polska)":            "4423",
}

class TheSportsDBClient:
    def __init__(self):
        self._cache: dict = {}
        self._cache_ttl = 600
        self._last_request_time = 0
        self._min_interval = 0.5

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _get(self, endpoint: str, use_cache: bool = True) -> dict:
        url = f"{BASE_URL}/{endpoint}"
        if use_cache and url in self._cache:
            ts, data = self._cache[url]
            if time.time() - ts < self._cache_ttl:
                return data
        self._rate_limit()
        try:
            r = requests.get(url, timeout=15)
            self._last_request_time = time.time()
            r.raise_for_status()
            data = r.json()
            if use_cache:
                self._cache[url] = (time.time(), data)
            return data
        except Exception as e:
            logger.error(f"Błąd: {e}")
            return {}

    def get_league_teams(self, league_id: str, season: str = "2024-2025") -> list:
        """Pobiera drużyny z ligi przez tabelę (darmowy endpoint)."""
        data = self._get(f"lookuptable.php?l={league_id}&s={season}")
        table = data.get("table") or []
        teams = []
        seen = set()
        for row in table:
            tid = row.get("idTeam")
            if tid and tid not in seen:
                seen.add(tid)
                teams.append({
                    "idTeam": tid,
                    "strTeam": row.get("strTeam", ""),
                    "strTeamAlternate": "",
                })
        return teams

    def get_all_teams(self) -> list:
        """Pobiera drużyny ze wszystkich lig."""
        all_teams = []
        seen = set()
        for league_name, league_id in LEAGUES.items():
            teams = self.get_league_teams(league_id)
            for t in teams:
                tid = t.get("idTeam")
                if tid and tid not in seen:
                    seen.add(tid)
                    t["_competition"] = league_name
                    all_teams.append(t)
        return all_teams

    def search_teams_local(self, query: str, all_teams: list) -> list:
        """Wyszukuje lokalnie po nazwie."""
        q = unicodedata.normalize('NFKD', query.lower()).encode('ascii','ignore').decode('ascii')
        results = []
        for t in all_teams:
            name = t.get("strTeam","")
            alt  = t.get("strTeamAlternate","") or ""
            name_n = unicodedata.normalize('NFKD', name.lower()).encode('ascii','ignore').decode('ascii')
            alt_n  = unicodedata.normalize('NFKD', alt.lower()).encode('ascii','ignore').decode('ascii')
            if q in name_n or q in alt_n:
                results.append(t)
        return results

    def get_team_last_matches(self, team_id: str, limit: int = 15) -> list:
        """Pobiera ostatnie mecze drużyny."""
        data = self._get(f"eventslast.php?id={team_id}")
        events = data.get("results") or []
        # Filtruj tylko piłkę nożną i zakończone
        finished = [e for e in events if e.get("strSport") == "Soccer" and e.get("strStatus") == "Match Finished"]
        return finished[-limit:]

    def get_team_next_matches(self, team_id: str, limit: int = 5) -> list:
        """Pobiera nadchodzące mecze drużyny."""
        data = self._get(f"eventsnext.php?id={team_id}")
        events = data.get("events") or []
        return [e for e in events if e.get("strSport") == "Soccer"][:limit]

    def get_h2h(self, team1_id: str, team2_id: str, limit: int = 10) -> list:
        """Pobiera H2H z ostatnich meczów team1, filtruje te z team2."""
        matches = self.get_team_last_matches(team1_id, limit=50)
        h2h = []
        for m in matches:
            hid = m.get("idHomeTeam","")
            aid = m.get("idAwayTeam","")
            if team2_id in (hid, aid):
                h2h.append(m)
            if len(h2h) >= limit:
                break
        return h2h

    def get_team_statistics(self, team_id: str) -> dict:
        """Oblicza statystyki z ostatnich meczów."""
        matches = self.get_team_last_matches(team_id, limit=30)
        scored_list, conceded_list = [], []
        for m in matches:
            gs = m.get("intHomeScore") if m.get("idHomeTeam") == team_id else m.get("intAwayScore")
            gc = m.get("intAwayScore") if m.get("idHomeTeam") == team_id else m.get("intHomeScore")
            try:
                scored_list.append(int(gs)); conceded_list.append(int(gc))
            except (TypeError, ValueError):
                continue
        n = len(scored_list)
        if n == 0: return {}
        avg_s = sum(scored_list)/n
        avg_c = sum(conceded_list)/n
        return {
            "goals": {
                "for":     {"average": {"total": str(round(avg_s,2)), "home": str(round(avg_s*1.1,2)), "away": str(round(avg_s*0.9,2))}},
                "against": {"average": {"total": str(round(avg_c,2)), "home": str(round(avg_c*0.9,2)), "away": str(round(avg_c*1.1,2))}},
            }
        }


def convert_event_to_fixture(event: dict, ref_team_id: str) -> dict:
    """Konwertuje event TheSportsDB do formatu model.py."""
    home_id = event.get("idHomeTeam","")
    away_id = event.get("idAwayTeam","")
    status  = "FT" if event.get("strStatus") == "Match Finished" else "NS"
    try: gh = int(event.get("intHomeScore") or -1)
    except: gh = None
    try: ga = int(event.get("intAwayScore") or -1)
    except: ga = None
    if gh == -1: gh = None
    if ga == -1: ga = None
    return {
        "fixture": {
            "id":     int(event.get("idEvent",0) or 0),
            "date":   (event.get("dateEvent") or "")[:10],
            "status": {"short": status},
        },
        "teams": {
            "home": {"id": int(home_id) if home_id else 0, "name": event.get("strHomeTeam","")},
            "away": {"id": int(away_id) if away_id else 0, "name": event.get("strAwayTeam","")},
        },
        "league": {"id": 0, "name": event.get("strLeague",""), "country": ""},
        "goals":  {"home": gh, "away": ga},
    }
