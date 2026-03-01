"""
api.py - Moduł komunikacji z API-Football (RapidAPI)

Odpowiada za:
- Pobieranie meczów (dziś, jutro, historyczne)
- Pobieranie statystyk drużyn
- Pobieranie składów, kontuzji, zawieszeń
- Obsługę błędów i cache'owanie odpowiedzi
"""

import os
import time
import logging
from datetime import datetime, timedelta
from typing import Optional
import requests
from dotenv import load_dotenv

# Wczytaj zmienne środowiskowe z pliku .env
load_dotenv()

# Konfiguracja logowania
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class APIFootballClient:
    """
    Klient API-Football (RapidAPI).
    Obsługuje wszystkie zapytania do API z rate limitingiem i obsługą błędów.
    """

    BASE_URL = "https://api-football-v1.p.rapidapi.com/v3"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("RAPIDAPI_KEY")
        if not self.api_key:
            raise ValueError(
                "Brak klucza API! Ustaw RAPIDAPI_KEY w pliku .env lub przekaż do konstruktora."
            )

        self.headers = {
            "x-rapidapi-key": self.api_key,
            "x-rapidapi-host": "api-football-v1.p.rapidapi.com"
        }

        # Prosty cache w pamięci - klucz: URL+parametry, wartość: (timestamp, dane)
        self._cache: dict = {}
        self._cache_ttl = 300  # 5 minut cache dla większości endpointów

        # Rate limiting: API-Football pozwala ~10 req/min na darmowym planie
        self._last_request_time = 0
        self._min_request_interval = 1.5  # sekund między zapytaniami (bezpieczny margines)

    def _rate_limit(self):
        """Zapewnia minimalny odstęp między zapytaniami API."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            sleep_time = self._min_request_interval - elapsed
            logger.debug(f"Rate limiting: czekam {sleep_time:.2f}s")
            time.sleep(sleep_time)

    def _get(self, endpoint: str, params: dict, use_cache: bool = True) -> dict:
        """
        Wykonuje zapytanie GET do API z obsługą cache i błędów.

        Args:
            endpoint: Ścieżka endpointu (np. '/fixtures')
            params: Parametry zapytania
            use_cache: Czy używać cache'owania

        Returns:
            Odpowiedź API jako słownik
        """
        url = f"{self.BASE_URL}{endpoint}"
        cache_key = f"{url}:{sorted(params.items())}"

        # Sprawdź cache
        if use_cache and cache_key in self._cache:
            timestamp, data = self._cache[cache_key]
            if time.time() - timestamp < self._cache_ttl:
                logger.debug(f"Cache hit: {endpoint}")
                return data

        # Rate limiting przed zapytaniem
        self._rate_limit()

        try:
            logger.info(f"Pobieranie: {endpoint} | Params: {params}")
            response = requests.get(url, headers=self.headers, params=params, timeout=15)
            self._last_request_time = time.time()

            # Sprawdź status HTTP
            response.raise_for_status()

            data = response.json()

            # Sprawdź limity API (nagłówki RapidAPI)
            remaining = response.headers.get('X-RateLimit-Requests-Remaining', 'N/A')
            logger.debug(f"Pozostałe zapytania API: {remaining}")

            # Sprawdź czy API zwróciło błąd w odpowiedzi
            if 'errors' in data and data['errors']:
                errors = data['errors']
                logger.error(f"Błąd API: {errors}")
                return {'response': [], 'errors': errors, 'results': 0}

            # Zapisz w cache
            if use_cache:
                self._cache[cache_key] = (time.time(), data)

            return data

        except requests.exceptions.ConnectionError:
            logger.error("Błąd połączenia - sprawdź internet")
            return {'response': [], 'errors': {'connection': 'Brak połączenia'}, 'results': 0}
        except requests.exceptions.Timeout:
            logger.error(f"Timeout dla: {endpoint}")
            return {'response': [], 'errors': {'timeout': 'Przekroczono czas oczekiwania'}, 'results': 0}
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:
                logger.warning("Przekroczono limit zapytań API - czekam 60 sekund")
                time.sleep(60)
                return self._get(endpoint, params, use_cache)  # Ponów próbę
            logger.error(f"Błąd HTTP {e.response.status_code}: {e}")
            return {'response': [], 'errors': {'http': str(e)}, 'results': 0}
        except Exception as e:
            logger.error(f"Nieoczekiwany błąd: {e}")
            return {'response': [], 'errors': {'unknown': str(e)}, 'results': 0}

    # ========== MECZE ==========

    def get_fixtures_by_date(self, date: str, timezone: str = "Europe/Warsaw") -> list:
        """
        Pobiera mecze dla konkretnej daty.

        Args:
            date: Data w formacie YYYY-MM-DD
            timezone: Strefa czasowa

        Returns:
            Lista meczów
        """
        data = self._get("/fixtures", {
            "date": date,
            "timezone": timezone
        })
        return data.get('response', [])

    def get_fixtures_today(self, timezone: str = "Europe/Warsaw") -> list:
        """Pobiera mecze na dziś."""
        today = datetime.now().strftime("%Y-%m-%d")
        logger.info(f"Pobieranie meczów na dziś: {today}")
        return self.get_fixtures_by_date(today, timezone)

    def get_fixtures_tomorrow(self, timezone: str = "Europe/Warsaw") -> list:
        """Pobiera mecze na jutro."""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        logger.info(f"Pobieranie meczów na jutro: {tomorrow}")
        return self.get_fixtures_by_date(tomorrow, timezone)

    def get_fixtures_by_league(self, league_id: int, season: int,
                                status: str = "NS") -> list:
        """
        Pobiera mecze konkretnej ligi i sezonu.

        Args:
            league_id: ID ligi w API
            season: Rok sezonu (np. 2024)
            status: Status meczu (NS=Not Started, FT=Finished, LIVE=Live)
        """
        params = {
            "league": league_id,
            "season": season,
        }
        if status:
            params["status"] = status

        data = self._get("/fixtures", params)
        return data.get('response', [])

    def get_popular_leagues_fixtures(self, season: int = 2024) -> list:
        """
        Pobiera mecze z najpopularniejszych lig europejskich.
        Używane do 'wszystkich dostępnych meczów'.
        """
        # Popularne ligi: Premier League, La Liga, Bundesliga, Serie A, Ligue 1,
        # Ekstraklasa, Champions League, Europa League
        popular_leagues = [
            39,   # Premier League
            140,  # La Liga
            78,   # Bundesliga
            135,  # Serie A
            61,   # Ligue 1
            106,  # Ekstraklasa
            2,    # Champions League
            3,    # Europa League
            848,  # Conference League
        ]

        all_fixtures = []
        # Pobierz mecze na dziś i jutro dla popularnych lig
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

        for date in [today, tomorrow]:
            fixtures = self.get_fixtures_by_date(date)
            # Filtruj tylko popularne ligi
            filtered = [
                f for f in fixtures
                if f.get('league', {}).get('id') in popular_leagues
            ]
            all_fixtures.extend(filtered)

        # Usuń duplikaty na podstawie ID meczu
        seen_ids = set()
        unique_fixtures = []
        for f in all_fixtures:
            fid = f.get('fixture', {}).get('id')
            if fid and fid not in seen_ids:
                seen_ids.add(fid)
                unique_fixtures.append(f)

        return unique_fixtures

    # ========== HEAD-TO-HEAD ==========

    def get_h2h(self, team1_id: int, team2_id: int, last: int = 10) -> list:
        """
        Pobiera historyczne mecze head-to-head między drużynami.

        Args:
            team1_id: ID pierwszej drużyny
            team2_id: ID drugiej drużyny
            last: Liczba ostatnich meczów do pobrania
        """
        data = self._get("/fixtures/headtohead", {
            "h2h": f"{team1_id}-{team2_id}",
            "last": last
        })
        return data.get('response', [])

    # ========== STATYSTYKI DRUŻYN ==========

    def get_team_statistics(self, team_id: int, league_id: int,
                             season: int = 2024) -> dict:
        """
        Pobiera statystyki drużyny w danej lidze i sezonie.

        Zawiera: forma, gole strzelone/stracone, mecze domowe/wyjazdowe,
        xG, posiadanie piłki, strzały, etc.
        """
        data = self._get("/teams/statistics", {
            "team": team_id,
            "league": league_id,
            "season": season
        })
        return data.get('response', {})

    def get_team_last_fixtures(self, team_id: int, last: int = 10) -> list:
        """Pobiera ostatnie N meczów drużyny."""
        data = self._get("/fixtures", {
            "team": team_id,
            "last": last,
            "status": "FT"  # Tylko zakończone mecze
        })
        return data.get('response', [])

    # ========== SKŁADY I KONTUZJE ==========

    def get_fixture_lineups(self, fixture_id: int) -> list:
        """
        Pobiera przewidywane/potwierdzone składy na mecz.

        Args:
            fixture_id: ID meczu

        Returns:
            Lista składów (zwykle 2 elementy: gospodarz i gość)
        """
        data = self._get("/fixtures/lineups", {
            "fixture": fixture_id
        }, use_cache=True)
        return data.get('response', [])

    def get_injuries(self, team_id: int, league_id: Optional[int] = None,
                     season: int = 2024) -> list:
        """
        Pobiera listę kontuzjowanych i zawieszonych zawodników.

        Args:
            team_id: ID drużyny
            league_id: Opcjonalne ID ligi
            season: Sezon
        """
        params = {
            "team": team_id,
            "season": season
        }
        if league_id:
            params["league"] = league_id

        data = self._get("/injuries", params)
        return data.get('response', [])

    def get_fixture_injuries(self, fixture_id: int) -> list:
        """Pobiera kontuzje/zawieszenia dla konkretnego meczu."""
        data = self._get("/injuries", {
            "fixture": fixture_id
        })
        return data.get('response', [])

    # ========== STATYSTYKI ZAWODNIKÓW ==========

    def get_top_scorers(self, league_id: int, season: int = 2024) -> list:
        """Pobiera topowych strzelców ligi."""
        data = self._get("/players/topscorers", {
            "league": league_id,
            "season": season
        })
        return data.get('response', [])

    def get_player_statistics(self, player_id: int, season: int = 2024,
                               league_id: Optional[int] = None) -> dict:
        """
        Pobiera statystyki konkretnego zawodnika.

        Zawiera: gole, asysty, minuty, oceny, strzały, drybling, etc.
        """
        params = {
            "id": player_id,
            "season": season
        }
        if league_id:
            params["league"] = league_id

        data = self._get("/players", params)
        response = data.get('response', [])
        return response[0] if response else {}

    def get_squad(self, team_id: int) -> list:
        """Pobiera aktualny skład drużyny (roster)."""
        data = self._get("/players/squads", {
            "team": team_id
        })
        return data.get('response', [])

    # ========== PRZEWIDYWANIA API (jako dodatkowy sygnał) ==========

    def get_api_predictions(self, fixture_id: int) -> dict:
        """
        Pobiera wbudowane predykcje API-Football.
        Używane jako dodatkowy sygnał w naszym modelu.
        """
        data = self._get("/predictions", {
            "fixture": fixture_id
        })
        response = data.get('response', [])
        return response[0] if response else {}

    # ========== LIGI I TABELE ==========

    def get_standings(self, league_id: int, season: int = 2024) -> list:
        """Pobiera tabelę ligi (pozycja, punkty, forma, gole)."""
        data = self._get("/standings", {
            "league": league_id,
            "season": season
        })
        try:
            return data['response'][0]['league']['standings'][0]
        except (KeyError, IndexError):
            return []

    def get_leagues(self) -> list:
        """Pobiera listę dostępnych lig."""
        data = self._get("/leagues", {"current": "true"})
        return data.get('response', [])

    # ========== POMOCNICZE ==========

    def clear_cache(self):
        """Czyści cache zapytań."""
        self._cache.clear()
        logger.info("Cache wyczyszczony")

    def get_cache_stats(self) -> dict:
        """Zwraca statystyki cache."""
        return {
            "entries": len(self._cache),
            "keys": list(self._cache.keys())[:5]  # Pierwsze 5 kluczy do podglądu
        }
