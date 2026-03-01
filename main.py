"""
main.py - Główny skrypt systemu predykcji wyników meczów piłkarskich

Przepływ działania:
1. Załaduj konfigurację (klucz API z .env)
2. Pobierz mecze (dziś / jutro / wszystkie dostępne)
3. Dla każdego meczu pobierz dane: statystyki, forma, H2H, kontuzje, składy
4. Uruchom model predykcyjny (Poisson + ELO + forma + kontuzje)
5. Zapisz wyniki do CSV
6. Wyświetl czytelne podsumowanie w konsoli

Użycie:
    python main.py                    # Domyślnie: mecze dziś + jutro
    python main.py --mode today       # Tylko mecze dziś
    python main.py --mode tomorrow    # Tylko mecze jutro
    python main.py --mode all         # Wszystkie dostępne mecze (popularne ligi)
    python main.py --league 39        # Mecze konkretnej ligi (Premier League)
"""

import os
import sys
import csv
import logging
import argparse
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

# Załaduj .env przed importem innych modułów
load_dotenv()

from api import APIFootballClient
from model import MatchPredictor, EloRating

# ---- KONFIGURACJA LOGOWANIA ----
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('prediction.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ---- KOLORY KONSOLI (ANSI) ----
class Colors:
    RESET  = '\033[0m'
    BOLD   = '\033[1m'
    GREEN  = '\033[92m'
    YELLOW = '\033[93m'
    CYAN   = '\033[96m'
    RED    = '\033[91m'
    BLUE   = '\033[94m'
    GRAY   = '\033[90m'
    WHITE  = '\033[97m'

    # Wykryj czy terminal obsługuje kolory
    @staticmethod
    def supported() -> bool:
        return sys.stdout.isatty() and sys.platform != 'win32'


def colorize(text: str, color: str) -> str:
    """Dodaje kolor ANSI jeśli terminal to obsługuje."""
    if Colors.supported():
        return f"{color}{text}{Colors.RESET}"
    return text


# ==============================================================================
# POBIERANIE I PRZETWARZANIE DANYCH
# ==============================================================================

def fetch_fixtures_for_mode(client: APIFootballClient, mode: str,
                             league_id: Optional[int] = None,
                             season: int = 2024) -> list:
    """
    Pobiera mecze na podstawie wybranego trybu.

    Args:
        client: Klient API
        mode: 'today' | 'tomorrow' | 'all' | 'both'
        league_id: Opcjonalne filtrowanie po lidze
        season: Sezon

    Returns:
        Lista meczów z API
    """
    fixtures = []

    if mode in ('today', 'both'):
        logger.info("📅 Pobieranie meczów na DZIŚ...")
        today_fixtures = client.get_fixtures_today()
        fixtures.extend(today_fixtures)
        logger.info(f"   → Znaleziono {len(today_fixtures)} meczów")

    if mode in ('tomorrow', 'both'):
        logger.info("📅 Pobieranie meczów na JUTRO...")
        tomorrow_fixtures = client.get_fixtures_tomorrow()
        fixtures.extend(tomorrow_fixtures)
        logger.info(f"   → Znaleziono {len(tomorrow_fixtures)} meczów")

    if mode == 'all':
        logger.info("🌍 Pobieranie WSZYSTKICH dostępnych meczów (popularne ligi)...")
        fixtures = client.get_popular_leagues_fixtures(season)
        logger.info(f"   → Znaleziono {len(fixtures)} meczów")

    # Filtruj po lidze jeśli podano
    if league_id:
        fixtures = [
            f for f in fixtures
            if f.get('league', {}).get('id') == league_id
        ]
        logger.info(f"   → Po filtrowaniu po lidze {league_id}: {len(fixtures)} meczów")

    # Filtruj tylko mecze zaplanowane (NS = Not Started) lub live
    fixtures = [
        f for f in fixtures
        if f.get('fixture', {}).get('status', {}).get('short') in ('NS', 'TBD', 'LIVE', '1H', 'HT', '2H')
    ]

    # Usuń duplikaty po ID meczu
    seen = set()
    unique_fixtures = []
    for f in fixtures:
        fid = f.get('fixture', {}).get('id')
        if fid and fid not in seen:
            seen.add(fid)
            unique_fixtures.append(f)

    return unique_fixtures


def enrich_fixture_data(client: APIFootballClient, fixture: dict,
                         season: int = 2024) -> dict:
    """
    Pobiera wszystkie dodatkowe dane potrzebne do predykcji dla jednego meczu.

    Args:
        client: Klient API
        fixture: Dane meczu z API
        season: Sezon

    Returns:
        Słownik z wszystkimi zebranymi danymi
    """
    fixture_id = fixture.get('fixture', {}).get('id')
    home_id = fixture.get('teams', {}).get('home', {}).get('id')
    away_id = fixture.get('teams', {}).get('away', {}).get('id')
    league_id = fixture.get('league', {}).get('id')

    data = {
        'fixture': fixture,
        'home_stats': {},
        'away_stats': {},
        'home_last_fixtures': [],
        'away_last_fixtures': [],
        'h2h': [],
        'home_injuries': [],
        'away_injuries': [],
        'lineups': [],
    }

    # --- Statystyki sezonowe ---
    if home_id and league_id:
        data['home_stats'] = client.get_team_statistics(home_id, league_id, season)

    if away_id and league_id:
        data['away_stats'] = client.get_team_statistics(away_id, league_id, season)

    # --- Ostatnie mecze (forma) ---
    if home_id:
        data['home_last_fixtures'] = client.get_team_last_fixtures(home_id, last=10)

    if away_id:
        data['away_last_fixtures'] = client.get_team_last_fixtures(away_id, last=10)

    # --- Head-to-Head ---
    if home_id and away_id:
        data['h2h'] = client.get_h2h(home_id, away_id, last=10)

    # --- Kontuzje i zawieszenia ---
    if fixture_id:
        all_injuries = client.get_fixture_injuries(fixture_id)
        data['home_injuries'] = [
            inj for inj in all_injuries
            if inj.get('team', {}).get('id') == home_id
        ]
        data['away_injuries'] = [
            inj for inj in all_injuries
            if inj.get('team', {}).get('id') == away_id
        ]

    # --- Składy (jeśli już opublikowane) ---
    if fixture_id:
        data['lineups'] = client.get_fixture_lineups(fixture_id)

    return data


def build_global_elo(client: APIFootballClient, fixtures_sample: list,
                      season: int = 2024) -> EloRating:
    """
    Buduje globalne rankingi ELO na podstawie historycznych meczów.

    Dla każdej unikalnej drużyny pobiera ostatnie 20 meczów i używa ich
    do budowania rankingu ELO.

    Args:
        client: Klient API
        fixtures_sample: Próbka meczów (żeby wiedzieć które drużyny analizować)
        season: Sezon historyczny

    Returns:
        Obiekt EloRating z wypełnionymi ratingami
    """
    elo = EloRating()

    # Zbierz unikalne ID drużyn
    team_ids = set()
    for f in fixtures_sample:
        home_id = f.get('teams', {}).get('home', {}).get('id')
        away_id = f.get('teams', {}).get('away', {}).get('id')
        if home_id:
            team_ids.add(home_id)
        if away_id:
            team_ids.add(away_id)

    logger.info(f"Budowanie ELO dla {len(team_ids)} drużyn...")

    # Zbierz historyczne mecze dla każdej drużyny
    all_historical = []
    for team_id in list(team_ids)[:50]:  # Ogranicz żeby nie przekroczyć limitu API
        historical = client.get_team_last_fixtures(team_id, last=15)
        all_historical.extend(historical)

    # Buduj ELO
    elo.build_from_fixtures(all_historical)
    return elo


# ==============================================================================
# EKSPORT DO CSV
# ==============================================================================

CSV_COLUMNS = [
    'data', 'liga', 'kraj', 'gospodarz', 'gość',
    'prawdop_1', 'prawdop_X', 'prawdop_2',
    'oczek_gole_gosp', 'oczek_gole_gość',
    'najp_wynik', 'prawdop_najp_wyniku',
    'typ', 'pewność',
    'ELO_gosp', 'ELO_gość',
    'forma_gosp', 'forma_gość',
    'kara_kontuzje_gosp', 'kara_kontuzje_gość',
    'top_5_wynikow'
]


def save_to_csv(predictions: list, filename: str = None) -> str:
    """
    Zapisuje predykcje do pliku CSV.

    Args:
        predictions: Lista słowników z predykcjami
        filename: Nazwa pliku (domyślnie automatyczna z datą)

    Returns:
        Ścieżka do zapisanego pliku
    """
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        filename = f"predykcje_{timestamp}.csv"

    with open(filename, 'w', newline='', encoding='utf-8-sig') as csvfile:
        writer = csv.writer(csvfile, delimiter=';')

        # Nagłówek
        writer.writerow(CSV_COLUMNS)

        # Dane
        for pred in predictions:
            writer.writerow([
                pred.get('date', ''),
                pred.get('league', ''),
                pred.get('country', ''),
                pred.get('home_team', ''),
                pred.get('away_team', ''),
                f"{pred.get('prob_home_win', 0):.1f}%",
                f"{pred.get('prob_draw', 0):.1f}%",
                f"{pred.get('prob_away_win', 0):.1f}%",
                pred.get('expected_goals_home', 0),
                pred.get('expected_goals_away', 0),
                pred.get('most_likely_score', ''),
                f"{pred.get('most_likely_score_prob', 0):.1f}%",
                pred.get('prediction', ''),
                pred.get('confidence', ''),
                pred.get('home_elo', 0),
                pred.get('away_elo', 0),
                pred.get('home_form_score', 0),
                pred.get('away_form_score', 0),
                pred.get('home_injury_penalty', 1.0),
                pred.get('away_injury_penalty', 1.0),
                ' | '.join(pred.get('top_scores', [])),
            ])

    logger.info(f"Zapisano {len(predictions)} predykcji do: {filename}")
    return filename


# ==============================================================================
# WYŚWIETLANIE W KONSOLI
# ==============================================================================

def print_header():
    """Wyświetla nagłówek programu."""
    border = "=" * 72
    print(colorize(border, Colors.CYAN))
    print(colorize("  ⚽ SYSTEM PREDYKCJI MECZÓW PIŁKARSKICH", Colors.BOLD + Colors.WHITE))
    print(colorize("     Model Poissona + ELO + Forma + Analiza składów", Colors.GRAY))
    print(colorize(border, Colors.CYAN))
    print()


def print_section(title: str):
    """Wyświetla nagłówek sekcji."""
    print()
    print(colorize(f"{'─' * 72}", Colors.BLUE))
    print(colorize(f"  📋 {title}", Colors.BOLD + Colors.CYAN))
    print(colorize(f"{'─' * 72}", Colors.BLUE))


def print_prediction(pred: dict, idx: int):
    """
    Wyświetla predykcję jednego meczu w czytelnej formie.

    Przykładowy output:
    ┌─────────────────────────────────────────────────────┐
    │ #1  Manchester United vs Liverpool                  │
    │     Premier League | 2024-03-10                     │
    │     1 (48%)   X (27%)   2 (25%)                     │
    │     Najp. wynik: 2:1 (12.3%) | Typ: 1 | Pewność: Średnia │
    └─────────────────────────────────────────────────────┘
    """
    home = pred.get('home_team', '?')
    away = pred.get('away_team', '?')
    league = pred.get('league', '')
    country = pred.get('country', '')
    date = pred.get('date', '')

    p1 = pred.get('prob_home_win', 0)
    px = pred.get('prob_draw', 0)
    p2 = pred.get('prob_away_win', 0)

    score = pred.get('most_likely_score', '?')
    score_prob = pred.get('most_likely_score_prob', 0)
    prediction = pred.get('prediction', '?')
    confidence = pred.get('confidence', '?')

    exp_h = pred.get('expected_goals_home', 0)
    exp_a = pred.get('expected_goals_away', 0)

    elo_h = pred.get('home_elo', 0)
    elo_a = pred.get('away_elo', 0)

    # Kolor dla prawdopodobieństw
    def prob_color(p):
        if p >= 50:
            return Colors.GREEN
        elif p >= 35:
            return Colors.YELLOW
        else:
            return Colors.RED

    print(f"\n  {colorize(f'#{idx}', Colors.BOLD)} {colorize(home, Colors.WHITE + Colors.BOLD)} vs {colorize(away, Colors.WHITE + Colors.BOLD)}")
    print(f"     {colorize(f'{country} - {league}', Colors.GRAY)} | {colorize(date, Colors.GRAY)}")
    print(
        f"     {colorize('1', Colors.BOLD)}: {colorize(f'{p1:.0f}%', prob_color(p1))}   "
        f"{colorize('X', Colors.BOLD)}: {colorize(f'{px:.0f}%', prob_color(px))}   "
        f"{colorize('2', Colors.BOLD)}: {colorize(f'{p2:.0f}%', prob_color(p2))}"
    )
    print(
        f"     Oczek. gole: {colorize(f'{exp_h:.2f}', Colors.CYAN)} - {colorize(f'{exp_a:.2f}', Colors.CYAN)} | "
        f"Najp. wynik: {colorize(score, Colors.GREEN)} ({score_prob:.1f}%)"
    )
    print(
        f"     Typ: {colorize(prediction, Colors.BOLD + Colors.YELLOW)} | "
        f"Pewność: {colorize(confidence, Colors.BOLD)} | "
        f"ELO: {elo_h} vs {elo_a}"
    )

    # Top wyniki
    top = pred.get('top_scores', [])
    if top:
        print(f"     Top wyniki: {colorize(' | '.join(top[:3]), Colors.GRAY)}")


def print_summary(predictions: list, csv_path: str, elapsed_time: float):
    """Wyświetla podsumowanie na końcu."""
    print()
    print(colorize("=" * 72, Colors.CYAN))
    print(colorize("  📊 PODSUMOWANIE", Colors.BOLD + Colors.WHITE))
    print(colorize("=" * 72, Colors.CYAN))

    total = len(predictions)
    home_wins = sum(1 for p in predictions if '1 (' in p.get('prediction', ''))
    draws = sum(1 for p in predictions if 'X' in p.get('prediction', ''))
    away_wins = sum(1 for p in predictions if '2 (' in p.get('prediction', ''))

    print(f"\n  Przeanalizowane mecze: {colorize(str(total), Colors.BOLD)}")
    print(f"  Typy: 1={colorize(str(home_wins), Colors.GREEN)} | X={colorize(str(draws), Colors.YELLOW)} | 2={colorize(str(away_wins), Colors.RED)}")
    print(f"  Czas analizy: {elapsed_time:.1f}s")
    print(f"  Wyniki zapisane do: {colorize(csv_path, Colors.CYAN)}")
    print(f"  Log: {colorize('prediction.log', Colors.GRAY)}")
    print()


# ==============================================================================
# GŁÓWNA LOGIKA
# ==============================================================================

def run_predictions(mode: str = 'both',
                     league_id: Optional[int] = None,
                     season: int = 2024,
                     api_key: Optional[str] = None,
                     output_file: Optional[str] = None):
    """
    Główna funkcja uruchamiająca cały pipeline predykcji.

    Args:
        mode: 'today' | 'tomorrow' | 'both' | 'all'
        league_id: Opcjonalne filtrowanie po lidze
        season: Sezon (np. 2024)
        api_key: Klucz API (jeśli None, pobierany z .env)
        output_file: Ścieżka do pliku CSV (domyślnie automatyczna)
    """
    start_time = datetime.now()
    print_header()

    # ---- INICJALIZACJA ----
    try:
        client = APIFootballClient(api_key)
        logger.info("✅ Połączono z API-Football")
    except ValueError as e:
        print(colorize(f"❌ BŁĄD: {e}", Colors.RED))
        print(colorize("Ustaw RAPIDAPI_KEY w pliku .env lub przekaż jako argument.", Colors.YELLOW))
        sys.exit(1)

    # ---- POBIERANIE MECZÓW ----
    print_section(f"POBIERANIE MECZÓW (tryb: {mode.upper()})")
    fixtures = fetch_fixtures_for_mode(client, mode, league_id, season)

    if not fixtures:
        print(colorize("  ⚠️  Brak meczów do analizy w wybranym trybie.", Colors.YELLOW))
        print("  Sprawdź czy poprawnie ustawiono API key i czy wybrana data/liga ma mecze.")
        sys.exit(0)

    print(colorize(f"\n  ✅ Znaleziono {len(fixtures)} meczów do analizy", Colors.GREEN))

    # ---- BUDOWANIE ELO ----
    print_section("BUDOWANIE RANKINGÓW ELO")
    print("  Pobieranie historycznych meczów do budowy rankingów ELO...")
    elo = build_global_elo(client, fixtures, season)
    print(colorize(f"  ✅ ELO zbudowane dla {len(elo.ratings)} drużyn", Colors.GREEN))

    # ---- INICJALIZACJA MODELU ----
    predictor = MatchPredictor(elo_ratings=elo)

    # ---- ANALIZA MECZÓW ----
    print_section(f"ANALIZA MECZÓW ({len(fixtures)} meczów)")
    predictions = []
    errors = []

    for i, fixture in enumerate(fixtures, 1):
        home_name = fixture.get('teams', {}).get('home', {}).get('name', '?')
        away_name = fixture.get('teams', {}).get('away', {}).get('name', '?')
        fixture_id = fixture.get('fixture', {}).get('id', '?')

        print(f"\n  [{i}/{len(fixtures)}] {home_name} vs {away_name} (ID: {fixture_id})")

        try:
            # Pobierz dane dla meczu
            print(f"    ↳ Pobieranie danych...", end=' ', flush=True)
            data = enrich_fixture_data(client, fixture, season)
            print(colorize("OK", Colors.GREEN))

            # Uruchom predykcję
            print(f"    ↳ Obliczanie predykcji...", end=' ', flush=True)
            prediction = predictor.predict_match(
                fixture=data['fixture'],
                home_team_fixtures=data['home_last_fixtures'],
                away_team_fixtures=data['away_last_fixtures'],
                home_stats=data['home_stats'],
                away_stats=data['away_stats'],
                home_injuries=data['home_injuries'],
                away_injuries=data['away_injuries'],
                h2h_fixtures=data['h2h']
            )
            print(colorize("OK", Colors.GREEN))

            predictions.append(prediction)

        except Exception as e:
            print(colorize(f"BŁĄD: {e}", Colors.RED))
            logger.error(f"Błąd przy meczu {home_name} vs {away_name}: {e}", exc_info=True)
            errors.append({'match': f"{home_name} vs {away_name}", 'error': str(e)})

    # ---- WYŚWIETL PREDYKCJE ----
    if predictions:
        print_section("PREDYKCJE")
        for i, pred in enumerate(predictions, 1):
            print_prediction(pred, i)

    # ---- BŁĘDY ----
    if errors:
        print_section("BŁĘDY")
        for err in errors:
            print(colorize(f"  ❌ {err['match']}: {err['error']}", Colors.RED))

    # ---- ZAPIS CSV ----
    csv_path = save_to_csv(predictions, output_file)

    # ---- PODSUMOWANIE ----
    elapsed = (datetime.now() - start_time).total_seconds()
    print_summary(predictions, csv_path, elapsed)

    return predictions


# ==============================================================================
# DEMO MODE (bez klucza API)
# ==============================================================================

def run_demo():
    """
    Tryb demo - pokazuje jak system działa bez prawdziwego API.
    Używa syntetycznych danych do demonstracji modelu.
    """
    from model import FormAnalyzer, EloRating, MatchPredictor

    print_header()
    print(colorize("  🎮 TRYB DEMO (bez klucza API)", Colors.YELLOW + Colors.BOLD))
    print(colorize("  Używane są syntetyczne dane do demonstracji algorytmu.\n", Colors.GRAY))

    # Stwórz syntetyczne dane
    elo = EloRating()
    elo.ratings = {
        1: 1750,  # Manchester City
        2: 1680,  # Liverpool
        3: 1550,  # Everton
        4: 1520,  # Burnley
    }

    predictor = MatchPredictor(elo_ratings=elo)

    # Demo mecz: Manchester City vs Liverpool
    demo_fixture = {
        'fixture': {'id': 9999, 'date': '2024-03-10'},
        'teams': {
            'home': {'id': 1, 'name': 'Manchester City'},
            'away': {'id': 2, 'name': 'Liverpool'}
        },
        'league': {'id': 39, 'name': 'Premier League', 'country': 'England'},
        'goals': {'home': None, 'away': None},
    }

    # Syntetyczna forma dla Man City (mocna)
    demo_home_fixtures = []
    for i, result in enumerate([(3,1), (2,0), (4,1), (1,0), (3,2)]):
        demo_home_fixtures.append({
            'fixture': {'id': 1000+i, 'date': f'2024-02-0{i+1}', 'status': {'short': 'FT'}},
            'teams': {'home': {'id': 1, 'name': 'Man City'}, 'away': {'id': 99, 'name': 'Opponent'}},
            'goals': {'home': result[0], 'away': result[1]}
        })

    # Syntetyczna forma dla Liverpool (dobra)
    demo_away_fixtures = []
    for i, result in enumerate([(2,1), (3,2), (1,1), (2,0), (1,2)]):
        demo_away_fixtures.append({
            'fixture': {'id': 2000+i, 'date': f'2024-02-0{i+1}', 'status': {'short': 'FT'}},
            'teams': {'home': {'id': 99, 'name': 'Opponent'}, 'away': {'id': 2, 'name': 'Liverpool'}},
            'goals': {'home': result[0], 'away': result[1]}
        })

    # Syntetyczne statystyki sezonowe
    demo_home_stats = {
        'goals': {
            'for': {'average': {'home': '2.8', 'away': '2.1', 'total': '2.45'}},
            'against': {'average': {'home': '0.8', 'away': '1.2', 'total': '1.0'}}
        }
    }
    demo_away_stats = {
        'goals': {
            'for': {'average': {'home': '2.5', 'away': '1.9', 'total': '2.2'}},
            'against': {'average': {'home': '0.9', 'away': '1.3', 'total': '1.1'}}
        }
    }

    # Uruchom predykcję
    prediction = predictor.predict_match(
        fixture=demo_fixture,
        home_team_fixtures=demo_home_fixtures,
        away_team_fixtures=demo_away_fixtures,
        home_stats=demo_home_stats,
        away_stats=demo_away_stats
    )

    print_section("DEMO: Manchester City vs Liverpool")
    print_prediction(prediction, 1)

    # Drugi demo mecz
    demo_fixture2 = {
        'fixture': {'id': 8888, 'date': '2024-03-10'},
        'teams': {
            'home': {'id': 3, 'name': 'Everton'},
            'away': {'id': 4, 'name': 'Burnley'}
        },
        'league': {'id': 39, 'name': 'Premier League', 'country': 'England'},
        'goals': {'home': None, 'away': None},
    }

    demo_home2 = []
    for i, result in enumerate([(1,2), (0,0), (1,1), (2,3), (0,1)]):
        demo_home2.append({
            'fixture': {'id': 3000+i, 'date': f'2024-02-0{i+1}', 'status': {'short': 'FT'}},
            'teams': {'home': {'id': 3, 'name': 'Everton'}, 'away': {'id': 99, 'name': 'Opp'}},
            'goals': {'home': result[0], 'away': result[1]}
        })
    demo_away2 = []
    for i, result in enumerate([(1,1), (2,2), (0,1), (1,0), (0,2)]):
        demo_away2.append({
            'fixture': {'id': 4000+i, 'date': f'2024-02-0{i+1}', 'status': {'short': 'FT'}},
            'teams': {'home': {'id': 99, 'name': 'Opp'}, 'away': {'id': 4, 'name': 'Burnley'}},
            'goals': {'home': result[0], 'away': result[1]}
        })

    prediction2 = predictor.predict_match(
        fixture=demo_fixture2,
        home_team_fixtures=demo_home2,
        away_team_fixtures=demo_away2,
        home_stats={},
        away_stats={}
    )

    print_prediction(prediction2, 2)

    # Zapisz demo do CSV
    csv_path = save_to_csv([prediction, prediction2], "demo_predykcje.csv")
    elapsed = 0.5
    print_summary([prediction, prediction2], csv_path, elapsed)

    print(colorize("\n  💡 Aby użyć z prawdziwymi danymi:\n", Colors.CYAN))
    print("  1. Zarejestruj się na https://rapidapi.com/api-sports/api/api-football")
    print("  2. Skopiuj .env.example do .env")
    print("  3. Wstaw klucz API do .env jako RAPIDAPI_KEY=twoj_klucz")
    print("  4. Uruchom: python main.py\n")


# ==============================================================================
# PARSOWANIE ARGUMENTÓW CLI
# ==============================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description='System predykcji wyników meczów piłkarskich',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Przykłady użycia:
  python main.py                        # Mecze dziś + jutro (tryb domyślny)
  python main.py --mode today           # Tylko mecze dziś
  python main.py --mode tomorrow        # Tylko mecze jutro
  python main.py --mode all             # Wszystkie dostępne mecze
  python main.py --mode today --league 39   # Mecze Premier League dziś
  python main.py --demo                 # Tryb demo bez klucza API
        """
    )

    parser.add_argument(
        '--mode',
        choices=['today', 'tomorrow', 'both', 'all'],
        default='both',
        help='Tryb pobierania meczów (domyślnie: both)'
    )
    parser.add_argument(
        '--league',
        type=int,
        default=None,
        help='ID ligi (np. 39=Premier League, 140=La Liga, 78=Bundesliga)'
    )
    parser.add_argument(
        '--season',
        type=int,
        default=2024,
        help='Sezon (domyślnie: 2024)'
    )
    parser.add_argument(
        '--api-key',
        type=str,
        default=None,
        help='Klucz API (nadpisuje .env)'
    )
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Ścieżka do pliku CSV z wynikami'
    )
    parser.add_argument(
        '--demo',
        action='store_true',
        help='Uruchom tryb demo (bez klucza API)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Włącz szczegółowe logowanie'
    )

    return parser.parse_args()


# ==============================================================================
# PUNKT WEJŚCIA
# ==============================================================================

if __name__ == '__main__':
    args = parse_args()

    # Poziom logowania
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Tryb demo
    if args.demo:
        run_demo()
        sys.exit(0)

    # Sprawdź klucz API
    api_key = args.api_key or os.getenv("RAPIDAPI_KEY")
    if not api_key:
        print(colorize("\n⚠️  Brak klucza API!", Colors.YELLOW + Colors.BOLD))
        print("  Opcje:")
        print("  1. Uruchom tryb demo: python main.py --demo")
        print("  2. Ustaw klucz w .env: RAPIDAPI_KEY=twoj_klucz")
        print("  3. Przekaż jako argument: python main.py --api-key TWOJ_KLUCZ\n")
        print("  Rejestracja API: https://rapidapi.com/api-sports/api/api-football\n")
        sys.exit(1)

    # Uruchom predykcje
    run_predictions(
        mode=args.mode,
        league_id=args.league,
        season=args.season,
        api_key=api_key,
        output_file=args.output
    )
