"""
api.py - Klient TheSportsDB (klucz: 3, darmowy)
Endpointy darmowe: searchteams, lookuptable, eventslast, eventsnext
"""

import time
import logging
import unicodedata
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE = "https://www.thesportsdb.com/api/v1/json/3"

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

# Drużyny wpisane ręcznie jako fallback dla Ekstraklasy
EKSTRAKLASA_TEAMS = [
    {"idTeam":"133613","strTeam":"Legia Warszawa","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133614","strTeam":"Lech Poznań","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133615","strTeam":"Wisła Kraków","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133616","strTeam":"Cracovia","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133617","strTeam":"Zagłębie Lubin","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133618","strTeam":"Śląsk Wrocław","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133619","strTeam":"Jagiellonia Białystok","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133620","strTeam":"Pogoń Szczecin","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133621","strTeam":"Piast Gliwice","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133622","strTeam":"Górnik Zabrze","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133623","strTeam":"Raków Częstochowa","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133624","strTeam":"Korona Kielce","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133625","strTeam":"Miedź Legnica","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133626","strTeam":"Warta Poznań","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133627","strTeam":"Widzew Łódź","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133628","strTeam":"Motor Lublin","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133629","strTeam":"Zagłębie Sosnowiec","_competition":"Ekstraklasa (Polska)"},
    {"idTeam":"133630","strTeam":"Piasty Gliwice","_competition":"Ekstraklasa (Polska)"},
]


class TheSportsDBClient:
    def __init__(self):
        self._cache: dict = {}
        self._cache_ttl = 600
        self._last_request_time = 0
        self._min_interval = 1.0

    def _rate_limit(self):
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)

    def _get(self, url: str, use_cache: bool = True) -> dict:
        if use_cache and url in self._cache:
            ts, data = self._cache[url]
            if time.time() - ts < self._cache_ttl:
                return data
        self._rate_limit()
        try:
            r = requests.get(url, timeout=15)
            self._last_request_time = time.time()
            if r.status_code == 429:
                time.sleep(30)
                return self._get(url, use_cache)
            r.raise_for_status()
            data = r.json()
            if use_cache:
                self._cache[url] = (time.time(), data)
            return data
        except Exception as e:
            logger.error(f"Błąd API: {e}")
            return {}

    def search_team_by_name(self, name: str) -> list:
        """Wyszukuje drużynę po nazwie przez API."""
        import unicodedata
        name_clean = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')
        data = self._get(f"{BASE}/searchteams.php?t={requests.utils.quote(name_clean)}")
        teams = data.get("teams") or []
        return [t for t in teams if t.get("strSport") == "Soccer"]

    def get_league_teams_from_table(self, league_id: str, season: str = "2024-2025") -> list:
        """Pobiera drużyny z tabeli ligi."""
        data = self._get(f"{BASE}/lookuptable.php?l={league_id}&s={season}")
        table = data.get("table") or []
        teams = []
        seen = set()
        for row in table:
            tid = row.get("idTeam")
            name = row.get("strTeam", "")
            if tid and tid not in seen and name:
                seen.add(tid)
                teams.append({
                    "idTeam": tid,
                    "strTeam": name,
                    "strTeamAlternate": name,
                })
        return teams

    def get_all_teams(self) -> list:
        """Pobiera drużyny ze wszystkich lig + fallback dla polskich."""
        all_teams = []
        seen_ids = set()

        # Dodaj polskie drużyny z hardcoded listy (fallback)
        for t in EKSTRAKLASA_TEAMS:
            all_teams.append(t)
            seen_ids.add(t["idTeam"])

        # Pobierz z API dla pozostałych lig
        for league_name, league_id in LEAGUES.items():
            if "Polska" in league_name:
                continue  # już dodane z hardcoded
            teams = self.get_league_teams_from_table(league_id)
            for t in teams:
                tid = t.get("idTeam")
                if tid and tid not in seen_ids:
                    seen_ids.add(tid)
                    t["_competition"] = league_name
                    all_teams.append(t)

        return all_teams

    def search_teams_local(self, query: str, all_teams: list) -> list:
        """Wyszukuje lokalnie + przez API jeśli nic nie znaleziono."""
        q = unicodedata.normalize('NFKD', query.lower()).encode('ascii', 'ignore').decode('ascii')
        results = []
        for t in all_teams:
            name = t.get("strTeam", "")
            alt  = t.get("strTeamAlternate", "") or ""
            name_n = unicodedata.normalize('NFKD', name.lower()).encode('ascii', 'ignore').decode('ascii')
            alt_n  = unicodedata.normalize('NFKD', alt.lower()).encode('ascii', 'ignore').decode('ascii')
            if q in name_n or q in alt_n:
                results.append(t)

        # Jeśli nic nie znaleziono lokalnie — szukaj przez API
        if not results:
            api_results = self.search_team_by_name(query)
            for t in api_results[:10]:
                t["_competition"] = t.get("strLeague", "")
                results.append(t)

        return results

    def get_team_last_matches(self, team_id: str, limit: int = 15) -> list:
        data = self._get(f"{BASE}/eventslast.php?id={team_id}")
        events = data.get("results") or []
        finished = [e for e in events if e.get("strSport","").lower() in ("soccer","football") and e.get("strStatus") == "Match Finished"]
        return finished[-limit:]

    def get_h2h(self, team1_id: str, team2_id: str, limit: int = 10) -> list:
        matches = self.get_team_last_matches(team1_id, limit=50)
        h2h = []
        for m in matches:
            if team2_id in (m.get("idHomeTeam",""), m.get("idAwayTeam","")):
                h2h.append(m)
            if len(h2h) >= limit:
                break
        return h2h

    def get_team_statistics(self, team_id: str) -> dict:
        matches = self.get_team_last_matches(team_id, limit=30)
        scored_list, conceded_list = [], []
        for m in matches:
            is_home = (m.get("idHomeTeam","") == team_id)
            try:
                gh = int(m.get("intHomeScore") or -1)
                ga = int(m.get("intAwayScore") or -1)
            except (TypeError, ValueError):
                continue
            if gh < 0 or ga < 0:
                continue
            scored_list.append(gh if is_home else ga)
            conceded_list.append(ga if is_home else gh)
        n = len(scored_list)
        if n == 0:
            return {}
        avg_s = sum(scored_list) / n
        avg_c = sum(conceded_list) / n
        return {
            "goals": {
                "for":     {"average": {"total": str(round(avg_s,2)), "home": str(round(avg_s*1.1,2)), "away": str(round(avg_s*0.9,2))}},
                "against": {"average": {"total": str(round(avg_c,2)), "home": str(round(avg_c*0.9,2)), "away": str(round(avg_c*1.1,2))}},
            }
        }


def convert_event_to_fixture(event: dict, ref_team_id: str) -> dict:
    home_id = event.get("idHomeTeam", "")
    away_id = event.get("idAwayTeam", "")
    status  = "FT" if event.get("strStatus") == "Match Finished" else "NS"
    try:
        gh = int(event.get("intHomeScore") or -1)
        ga = int(event.get("intAwayScore") or -1)
    except (TypeError, ValueError):
        gh = ga = None
    if gh is not None and gh < 0: gh = None
    if ga is not None and ga < 0: ga = None
    try: hid_int = int(home_id) if home_id else 0
    except: hid_int = 0
    try: aid_int = int(away_id) if away_id else 0
    except: aid_int = 0
    return {
        "fixture": {
            "id":     int(event.get("idEvent", 0) or 0),
            "date":   (event.get("dateEvent") or "")[:10],
            "status": {"short": status},
        },
        "teams": {
            "home": {"id": hid_int, "name": event.get("strHomeTeam", "")},
            "away": {"id": aid_int, "name": event.get("strAwayTeam", "")},
        },
        "league": {"id": 0, "name": event.get("strLeague", ""), "country": ""},
        "goals":  {"home": gh, "away": ga},
    }
