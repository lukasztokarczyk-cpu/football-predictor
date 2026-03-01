"""
model.py - Model predykcyjny wyników meczów piłkarskich

Implementuje:
1. Model Poissona - przewidywanie liczby goli każdej drużyny
2. System rankingowy ELO - uwzględnienie relatywnej siły drużyn
3. Analiza formy - wpływ ostatnich wyników (okno ruchome)
4. Wpływ kluczowych zawodników - korekta siły przy kontuzjach/zawieszeniach
5. Wyliczanie prawdopodobieństw: wygrana/remis/porażka
6. Przewidywanie najbar prawdopodobnego wyniku (scoreline)
"""

import math
import logging
from typing import Optional
from scipy.stats import poisson
from scipy.special import factorial

logger = logging.getLogger(__name__)


# ==============================================================================
# STAŁE I PARAMETRY MODELU
# ==============================================================================

# Bazowe ELO dla nowej drużyny (FIFA używa 1000, my startujemy od 1500)
DEFAULT_ELO = 1500

# K-faktor ELO: jak mocno jeden mecz zmienia ranking
# Wyższy = bardziej reaktywny na wyniki, niższy = bardziej stabilny
ELO_K_FACTOR = 32

# Waga przewagi gospodarzy przy ELO (historycznie ~0.1)
HOME_ADVANTAGE_ELO = 100

# Bazowa liczba goli w meczu (średnia europejska ~2.7 gola na mecz)
BASE_GOALS_PER_GAME = 2.7
HOME_GOAL_SHARE = 0.55   # Gospodarz strzela ~55% goli
AWAY_GOAL_SHARE = 0.45   # Gość strzela ~45% goli

# Waga formy (vs statystyki sezonowe)
FORM_WEIGHT = 0.4        # 40% waga ostatniej formy
SEASON_WEIGHT = 0.6      # 60% waga statystyk sezonowych

# Mnożnik kary za brakującego kluczowego zawodnika
KEY_PLAYER_PENALTY = 0.05   # 5% redukcji siły ataku za każdego brakującego kluczowego gracza
MAX_PLAYER_PENALTY = 0.25   # Maksymalnie 25% kary

# Próg oceny gracza do uznania go za "kluczowego"
KEY_PLAYER_RATING_THRESHOLD = 7.0   # Ocena >= 7.0 lub >= 5 goli/asyst


# ==============================================================================
# KLASA ELO
# ==============================================================================

class EloRating:
    """
    System rankingowy ELO adaptowany dla piłki nożnej.

    Klasyczny ELO (szachy) zakłada wyniki 0 lub 1.
    W piłce nożnej dodajemy remis jako wynik 0.5.
    Uwzględniamy przewagę własnego boiska.
    """

    def __init__(self):
        # Słownik: team_id -> rating ELO
        self.ratings: dict[int, float] = {}

    def get_rating(self, team_id: int) -> float:
        """Zwraca rating ELO drużyny (domyślnie DEFAULT_ELO jeśli nieznana)."""
        return self.ratings.get(team_id, DEFAULT_ELO)

    def expected_score(self, rating_a: float, rating_b: float,
                       home_advantage: bool = True) -> float:
        """
        Oblicza oczekiwany wynik (prawdopodobieństwo wygranej) dla drużyny A.

        Wzór ELO: E_a = 1 / (1 + 10^((R_b - R_a) / 400))

        Args:
            rating_a: ELO drużyny A
            rating_b: ELO drużyny B
            home_advantage: Czy A gra u siebie (dodaje HOME_ADVANTAGE_ELO)

        Returns:
            Liczba z zakresu (0, 1) - oczekiwany "wynik" drużyny A
        """
        effective_rating_a = rating_a + (HOME_ADVANTAGE_ELO if home_advantage else 0)
        return 1.0 / (1.0 + 10 ** ((rating_b - effective_rating_a) / 400.0))

    def update(self, home_id: int, away_id: int, home_goals: int, away_goals: int):
        """
        Aktualizuje ELO po meczu.

        Args:
            home_id: ID drużyny domowej
            away_id: ID drużyny gości
            home_goals, away_goals: Wynik meczu
        """
        rating_h = self.get_rating(home_id)
        rating_a = self.get_rating(away_id)

        # Oczekiwane wyniki
        expected_h = self.expected_score(rating_h, rating_a, home_advantage=True)
        expected_a = 1.0 - expected_h

        # Rzeczywiste wyniki (1=wygrana, 0.5=remis, 0=porażka)
        if home_goals > away_goals:
            actual_h, actual_a = 1.0, 0.0
        elif home_goals == away_goals:
            actual_h, actual_a = 0.5, 0.5
        else:
            actual_h, actual_a = 0.0, 1.0

        # Aktualizacja ratingów (formuła ELO)
        self.ratings[home_id] = rating_h + ELO_K_FACTOR * (actual_h - expected_h)
        self.ratings[away_id] = rating_a + ELO_K_FACTOR * (actual_a - expected_a)

    def build_from_fixtures(self, fixtures: list):
        """
        Buduje ratingi ELO na podstawie historycznych meczów.

        Args:
            fixtures: Lista meczów (format API-Football)
        """
        # Sortuj po dacie rosnąco
        sorted_fixtures = sorted(
            fixtures,
            key=lambda f: f.get('fixture', {}).get('date', '')
        )

        for fixture in sorted_fixtures:
            try:
                home_id = fixture['teams']['home']['id']
                away_id = fixture['teams']['away']['id']
                home_goals = fixture['goals']['home']
                away_goals = fixture['goals']['away']

                # Pomiń mecze bez wyników
                if home_goals is None or away_goals is None:
                    continue

                self.update(home_id, away_id, home_goals, away_goals)
            except (KeyError, TypeError):
                continue

        logger.info(f"ELO zbudowane dla {len(self.ratings)} drużyn")


# ==============================================================================
# ANALIZA FORMY
# ==============================================================================

class FormAnalyzer:
    """
    Analizuje formę drużyny z ostatnich N meczów.

    Forma to ważona średnia wyników, gdzie nowsze mecze mają większe znaczenie.
    Używamy liniowej funkcji wagowej: najnowszy mecz ma wagę N, najstarszy wagę 1.
    """

    @staticmethod
    def calculate_form(fixtures: list, team_id: int, last_n: int = 5) -> dict:
        """
        Oblicza wskaźniki formy drużyny.

        Args:
            fixtures: Lista meczów (format API-Football, posortowane od najnowszych)
            team_id: ID analizowanej drużyny
            last_n: Liczba ostatnich meczów do analizy

        Returns:
            Słownik z wskaźnikami:
            - form_score: wynik formy (0-1)
            - goals_scored_avg: średnia goli strzelonych
            - goals_conceded_avg: średnia goli straconych
            - win_rate: % wygranych
            - clean_sheets: % meczów bez straty gola
        """
        # Filtruj zakończone mecze drużyny
        team_fixtures = []
        for f in fixtures:
            home_id = f.get('teams', {}).get('home', {}).get('id')
            away_id = f.get('teams', {}).get('away', {}).get('id')
            status = f.get('fixture', {}).get('status', {}).get('short', '')
            goals_h = f.get('goals', {}).get('home')
            goals_a = f.get('goals', {}).get('away')

            if (home_id == team_id or away_id == team_id) and status == 'FT':
                if goals_h is not None and goals_a is not None:
                    team_fixtures.append(f)

        # Posortuj po dacie malejąco (najnowsze pierwsze)
        team_fixtures.sort(
            key=lambda f: f.get('fixture', {}).get('date', ''),
            reverse=True
        )
        team_fixtures = team_fixtures[:last_n]

        if not team_fixtures:
            return _default_form()

        total_weight = 0
        weighted_points = 0
        goals_scored = []
        goals_conceded = []
        wins = 0
        clean_sheets = 0

        n = len(team_fixtures)
        for i, fixture in enumerate(team_fixtures):
            # Waga: najnowszy mecz = n, najstarszy = 1
            weight = n - i

            home_id = fixture['teams']['home']['id']
            goals_h = fixture['goals']['home']
            goals_a = fixture['goals']['away']

            is_home = (home_id == team_id)
            scored = goals_h if is_home else goals_a
            conceded = goals_a if is_home else goals_h

            goals_scored.append(scored)
            goals_conceded.append(conceded)

            # Punkty (3 za wygraną, 1 za remis, 0 za porażkę)
            if scored > conceded:
                points = 3
                wins += 1
            elif scored == conceded:
                points = 1
            else:
                points = 0

            if conceded == 0:
                clean_sheets += 1

            weighted_points += weight * points
            total_weight += weight

        # Normalizuj do zakresu 0-1 (3 = maks punktów per mecz)
        max_weighted = sum(range(1, n + 1)) * 3  # suma_wag * 3_pkt
        form_score = weighted_points / max_weighted if max_weighted > 0 else 0.5

        return {
            'form_score': form_score,
            'goals_scored_avg': sum(goals_scored) / n,
            'goals_conceded_avg': sum(goals_conceded) / n,
            'win_rate': wins / n,
            'clean_sheet_rate': clean_sheets / n,
            'matches_analyzed': n
        }


def _default_form() -> dict:
    """Domyślne wartości formy gdy brak danych."""
    return {
        'form_score': 0.5,
        'goals_scored_avg': BASE_GOALS_PER_GAME * HOME_GOAL_SHARE,
        'goals_conceded_avg': BASE_GOALS_PER_GAME * AWAY_GOAL_SHARE,
        'win_rate': 0.33,
        'clean_sheet_rate': 0.25,
        'matches_analyzed': 0
    }


# ==============================================================================
# ANALIZA ZAWODNIKÓW
# ==============================================================================

class PlayerImpactAnalyzer:
    """
    Analizuje wpływ nieobecnych zawodników na siłę drużyny.

    Logika:
    1. Pobierz listę kontuzjowanych/zawieszonych zawodników
    2. Dla każdego sprawdź czy jest "kluczowy" (wysoka ocena lub dużo goli/asyst)
    3. Oblicz łączną karę dla ataku drużyny
    """

    @staticmethod
    def get_attack_penalty(injuries: list, team_id: int) -> float:
        """
        Oblicza mnożnik kary dla siły ataku drużyny.

        Args:
            injuries: Lista kontuzji/zawieszeń z API
            team_id: ID drużyny

        Returns:
            Mnożnik (1.0 = brak kary, 0.75 = 25% redukcja)
        """
        total_penalty = 0.0

        for injury in injuries:
            # Sprawdź czy kontuzja dotyczy tej drużyny
            player_team_id = injury.get('team', {}).get('id')
            if player_team_id != team_id:
                continue

            # Typ kontuzji/nieobecności
            injury_type = injury.get('player', {}).get('type', '')
            reason = injury.get('player', {}).get('reason', '')

            # Czy zawodnik jest niedostępny (nie tylko "wątpliwy")
            is_unavailable = injury_type in ['Missing Fixture', 'Injured', 'Suspended']
            if not is_unavailable:
                continue

            # Pobierz statystyki zawodnika jeśli dostępne
            # (API może zwracać różne formaty)
            position = injury.get('player', {}).get('position', '')

            # Napastnicy i pomocnicy ofensywni mają większy wpływ na atak
            if position in ['Attacker', 'Midfielder']:
                penalty = KEY_PLAYER_PENALTY
            elif position == 'Defender':
                penalty = KEY_PLAYER_PENALTY * 0.5  # Mniejszy wpływ na atak
            else:
                penalty = KEY_PLAYER_PENALTY * 0.3

            total_penalty += penalty

        # Ogranicz karę do maksimum
        total_penalty = min(total_penalty, MAX_PLAYER_PENALTY)

        return 1.0 - total_penalty

    @staticmethod
    def get_missing_key_players(lineups: list, squad: list, team_id: int) -> list:
        """
        Porównuje skład do listy mistrzów i wykrywa brakujących kluczowych graczy.

        Args:
            lineups: Składy na mecz z API
            squad: Pełny skład drużyny
            team_id: ID drużyny

        Returns:
            Lista brakujących kluczowych graczy
        """
        # Znajdź lineup dla tej drużyny
        team_lineup = None
        for lineup in lineups:
            if lineup.get('team', {}).get('id') == team_id:
                team_lineup = lineup
                break

        if not team_lineup:
            return []

        # ID zawodników w składzie na mecz (startowa 11 + zmiennicy)
        lineup_player_ids = set()
        for player in team_lineup.get('startXI', []):
            pid = player.get('player', {}).get('id')
            if pid:
                lineup_player_ids.add(pid)
        for player in team_lineup.get('substitutes', []):
            pid = player.get('player', {}).get('id')
            if pid:
                lineup_player_ids.add(pid)

        # W tym momencie potrzebowalibyśmy statystyk zawodników żeby ocenić ich wartość
        # Uproszczone: zwracamy pustą listę (pełna implementacja wymagałaby
        # dodatkowych zapytań API z oceną każdego zawodnika)
        return []


# ==============================================================================
# MODEL POISSONA
# ==============================================================================

class PoissonModel:
    """
    Model Poissona do przewidywania liczby goli.

    Założenie: liczba goli każdej drużyny jest niezależną zmienną losową
    z rozkładu Poissona z parametrem lambda (oczekiwana liczba goli).

    P(X = k) = (λ^k * e^(-λ)) / k!

    Lambda jest estymowana na podstawie:
    1. Statystyk sezonowych drużyny (siła ataku, słabość obrony przeciwnika)
    2. Formy ostatnich N meczów
    3. Rankingu ELO (relatywna siła)
    4. Korekty za nieobecnych graczy
    """

    def __init__(self, elo_ratings: Optional[EloRating] = None):
        self.elo = elo_ratings or EloRating()

    def estimate_lambdas(self,
                          home_team_id: int,
                          away_team_id: int,
                          home_stats: dict,
                          away_stats: dict,
                          home_form: dict,
                          away_form: dict,
                          home_injury_penalty: float = 1.0,
                          away_injury_penalty: float = 1.0,
                          league_avg_goals_home: float = None,
                          league_avg_goals_away: float = None) -> tuple[float, float]:
        """
        Szacuje parametry lambda dla rozkładu Poissona.

        Podejście Dixon-Coles (uproszczone):
        lambda_home = attack_home * defense_away * avg_goals_home
        lambda_away = attack_away * defense_home * avg_goals_away

        Gdzie:
        - attack_strength = śr.goli drużyny / śr.goli ligi
        - defense_weakness = śr.strat przeciwnika / śr.strat ligi

        Args:
            home_team_id, away_team_id: ID drużyn
            home_stats, away_stats: Statystyki sezonowe z API
            home_form, away_form: Forma z ostatnich meczów
            home_injury_penalty, away_injury_penalty: Korekta za nieobecnych
            league_avg_goals_home, league_avg_goals_away: Średnia ligi

        Returns:
            (lambda_home, lambda_away) - oczekiwana liczba goli
        """
        # Domyślne średnie ligi (europejskie ligi)
        if league_avg_goals_home is None:
            league_avg_goals_home = BASE_GOALS_PER_GAME * HOME_GOAL_SHARE  # ~1.485
        if league_avg_goals_away is None:
            league_avg_goals_away = BASE_GOALS_PER_GAME * AWAY_GOAL_SHARE  # ~1.215

        # ---- SIŁA ATAKU I SŁABOŚĆ OBRONY Z STATYSTYK SEZONOWYCH ----
        home_attack = _extract_attack_strength(home_stats, league_avg_goals_home, is_home=True)
        home_defense = _extract_defense_weakness(home_stats, league_avg_goals_away, is_home=True)
        away_attack = _extract_attack_strength(away_stats, league_avg_goals_away, is_home=False)
        away_defense = _extract_defense_weakness(away_stats, league_avg_goals_home, is_home=False)

        # ---- LAMBDA Z MODELU DIXON-COLES ----
        lambda_home_season = home_attack * away_defense * league_avg_goals_home
        lambda_away_season = away_attack * home_defense * league_avg_goals_away

        # ---- LAMBDA Z FORMY (ostatnie mecze) ----
        lambda_home_form = home_form.get('goals_scored_avg', league_avg_goals_home)
        lambda_away_form = away_form.get('goals_scored_avg', league_avg_goals_away)

        # ---- KOREKTA ELO ----
        elo_home = self.elo.get_rating(home_team_id)
        elo_away = self.elo.get_rating(away_team_id)
        elo_factor_home = self.elo.expected_score(elo_home, elo_away, home_advantage=True)
        elo_factor_away = 1.0 - elo_factor_home

        # Znormalizuj ELO factor wokół 0.5 -> mnożnik wokół 1.0
        # Jeśli ELO factor = 0.7 (faworyt), mnożnik = 1.0 + (0.7-0.5)*0.4 = 1.08
        elo_multiplier_home = 1.0 + (elo_factor_home - 0.5) * 0.4
        elo_multiplier_away = 1.0 + (elo_factor_away - 0.5) * 0.4

        # ---- POŁĄCZ SYGNAŁY (ważona suma) ----
        lambda_home = (
            SEASON_WEIGHT * lambda_home_season +
            FORM_WEIGHT * lambda_home_form
        ) * elo_multiplier_home * home_injury_penalty

        lambda_away = (
            SEASON_WEIGHT * lambda_away_season +
            FORM_WEIGHT * lambda_away_form
        ) * elo_multiplier_away * away_injury_penalty

        # Zabezpieczenie przed ujemnymi lambdami
        lambda_home = max(0.3, lambda_home)
        lambda_away = max(0.2, lambda_away)

        logger.debug(
            f"Lambda - Gospodarz: {lambda_home:.3f} | Gość: {lambda_away:.3f} "
            f"(ELO: {elo_home:.0f} vs {elo_away:.0f})"
        )

        return lambda_home, lambda_away

    def predict_probabilities(self, lambda_home: float, lambda_away: float,
                               max_goals: int = 8) -> dict:
        """
        Oblicza prawdopodobieństwa wygranej/remisu/porażki na podstawie rozkładu Poissona.

        Metoda: Sumuje P(home=i, away=j) dla wszystkich kombinacji wyników (i, j)
        gdzie i, j ∈ [0, max_goals].

        Args:
            lambda_home: Oczekiwana liczba goli gospodarza
            lambda_away: Oczekiwana liczba goli gości
            max_goals: Maksymalna liczba goli do rozważenia

        Returns:
            Słownik z prawdopodobieństwami i macierzą wyników
        """
        # Macierz prawdopodobieństw wyników (i gole gospod., j gole gości)
        score_matrix = {}
        prob_home_win = 0.0
        prob_draw = 0.0
        prob_away_win = 0.0

        for home_goals in range(max_goals + 1):
            for away_goals in range(max_goals + 1):
                # P(X=k) = λ^k * e^(-λ) / k!
                p_home = poisson.pmf(home_goals, lambda_home)
                p_away = poisson.pmf(away_goals, lambda_away)
                prob = p_home * p_away

                score_matrix[(home_goals, away_goals)] = prob

                if home_goals > away_goals:
                    prob_home_win += prob
                elif home_goals == away_goals:
                    prob_draw += prob
                else:
                    prob_away_win += prob

        # Normalizuj (pokrycie do max_goals może być < 1.0)
        total = prob_home_win + prob_draw + prob_away_win
        if total > 0:
            prob_home_win /= total
            prob_draw /= total
            prob_away_win /= total

        return {
            'home_win': prob_home_win,
            'draw': prob_draw,
            'away_win': prob_away_win,
            'score_matrix': score_matrix,
            'lambda_home': lambda_home,
            'lambda_away': lambda_away
        }

    def predict_most_likely_score(self, score_matrix: dict, top_n: int = 5) -> list:
        """
        Zwraca top N najbardziej prawdopodobnych wyników.

        Args:
            score_matrix: Macierz z predict_probabilities()
            top_n: Liczba wyników do zwrócenia

        Returns:
            Lista [(home_goals, away_goals, probability), ...]
        """
        sorted_scores = sorted(
            score_matrix.items(),
            key=lambda x: x[1],
            reverse=True
        )[:top_n]

        return [(h, a, p) for (h, a), p in sorted_scores]


# ==============================================================================
# GŁÓWNY MODEL PREDYKCJI
# ==============================================================================

class MatchPredictor:
    """
    Główna klasa predykcji meczów.
    Łączy wszystkie komponenty w jeden pipeline.
    """

    def __init__(self, elo_ratings: Optional[EloRating] = None):
        self.elo = elo_ratings or EloRating()
        self.poisson_model = PoissonModel(self.elo)
        self.form_analyzer = FormAnalyzer()
        self.player_analyzer = PlayerImpactAnalyzer()

    def predict_match(self,
                       fixture: dict,
                       home_team_fixtures: list,
                       away_team_fixtures: list,
                       home_stats: dict,
                       away_stats: dict,
                       home_injuries: list = None,
                       away_injuries: list = None,
                       h2h_fixtures: list = None) -> dict:
        """
        Główna funkcja predykcji dla jednego meczu.

        Args:
            fixture: Dane meczu z API
            home_team_fixtures: Ostatnie mecze gospodarza
            away_team_fixtures: Ostatnie mecze gościa
            home_stats: Statystyki sezonowe gospodarza
            away_stats: Statystyki sezonowe gościa
            home_injuries: Lista kontuzji/zawieszeń w drużynie gospodarza
            away_injuries: Lista kontuzji/zawieszeń w drużynie gości
            h2h_fixtures: Historyczne mecze H2H

        Returns:
            Słownik z pełną predykcją meczu
        """
        home_injuries = home_injuries or []
        away_injuries = away_injuries or []
        h2h_fixtures = h2h_fixtures or []

        # Pobierz ID drużyn
        home_id = fixture.get('teams', {}).get('home', {}).get('id')
        away_id = fixture.get('teams', {}).get('away', {}).get('id')
        home_name = fixture.get('teams', {}).get('home', {}).get('name', 'Gospodarz')
        away_name = fixture.get('teams', {}).get('away', {}).get('name', 'Gość')

        # 1. Analiza formy
        home_form = self.form_analyzer.calculate_form(
            home_team_fixtures, home_id, last_n=5
        )
        away_form = self.form_analyzer.calculate_form(
            away_team_fixtures, away_id, last_n=5
        )

        # Jeśli mamy H2H, włącz do analizy formy z mniejszą wagą
        if h2h_fixtures:
            h2h_home_form = self.form_analyzer.calculate_form(h2h_fixtures, home_id, last_n=5)
            h2h_away_form = self.form_analyzer.calculate_form(h2h_fixtures, away_id, last_n=5)
            # Mieszaj formę 80% aktualna forma + 20% H2H
            home_form = _blend_forms(home_form, h2h_home_form, weight_primary=0.8)
            away_form = _blend_forms(away_form, h2h_away_form, weight_primary=0.8)

        # 2. Kara za kontuzje/zawieszenia
        home_injury_penalty = self.player_analyzer.get_attack_penalty(home_injuries, home_id)
        away_injury_penalty = self.player_analyzer.get_attack_penalty(away_injuries, away_id)

        # 3. Oblicz lambdy (oczekiwane gole)
        lambda_home, lambda_away = self.poisson_model.estimate_lambdas(
            home_team_id=home_id,
            away_team_id=away_id,
            home_stats=home_stats,
            away_stats=away_stats,
            home_form=home_form,
            away_form=away_form,
            home_injury_penalty=home_injury_penalty,
            away_injury_penalty=away_injury_penalty
        )

        # 4. Oblicz prawdopodobieństwa
        probs = self.poisson_model.predict_probabilities(lambda_home, lambda_away)

        # 5. Najbardziej prawdopodobne wyniki
        top_scores = self.poisson_model.predict_most_likely_score(
            probs['score_matrix'], top_n=5
        )

        # 6. Wyznacz najbardziej prawdopodobny wynik
        most_likely_score = top_scores[0] if top_scores else (1, 1, 0)

        # 7. Wyznacz typ (kto wygra / remis)
        prediction = _determine_prediction(
            probs['home_win'],
            probs['draw'],
            probs['away_win'],
            home_name,
            away_name
        )

        # 8. Pewność predykcji (entropia informacyjna)
        confidence = _calculate_confidence(
            probs['home_win'],
            probs['draw'],
            probs['away_win']
        )

        return {
            'fixture_id': fixture.get('fixture', {}).get('id'),
            'home_team': home_name,
            'away_team': away_name,
            'home_team_id': home_id,
            'away_team_id': away_id,
            'date': fixture.get('fixture', {}).get('date', '')[:10],
            'league': fixture.get('league', {}).get('name', ''),
            'country': fixture.get('league', {}).get('country', ''),

            # Prawdopodobieństwa
            'prob_home_win': round(probs['home_win'] * 100, 1),
            'prob_draw': round(probs['draw'] * 100, 1),
            'prob_away_win': round(probs['away_win'] * 100, 1),

            # Oczekiwane gole
            'expected_goals_home': round(lambda_home, 2),
            'expected_goals_away': round(lambda_away, 2),

            # Najbardziej prawdopodobny wynik
            'most_likely_score': f"{most_likely_score[0]}:{most_likely_score[1]}",
            'most_likely_score_prob': round(most_likely_score[2] * 100, 1),

            # Top 5 wyników
            'top_scores': [
                f"{h}:{a} ({p*100:.1f}%)"
                for h, a, p in top_scores
            ],

            # Typ
            'prediction': prediction,
            'confidence': confidence,

            # Dane pomocnicze
            'home_form_score': round(home_form['form_score'], 3),
            'away_form_score': round(away_form['form_score'], 3),
            'home_elo': round(self.elo.get_rating(home_id)),
            'away_elo': round(self.elo.get_rating(away_id)),
            'home_injury_penalty': round(home_injury_penalty, 3),
            'away_injury_penalty': round(away_injury_penalty, 3),
        }


# ==============================================================================
# FUNKCJE POMOCNICZE
# ==============================================================================

def _extract_attack_strength(team_stats: dict, league_avg: float,
                               is_home: bool) -> float:
    """
    Wyciąga siłę ataku drużyny (stosunek goli drużyny do średniej ligi).

    Siła ataku > 1.0 oznacza drużynę strzelającą powyżej średniej.
    """
    if not team_stats:
        return 1.0

    try:
        goals = team_stats.get('goals', {})
        if is_home:
            scored = goals.get('for', {}).get('average', {}).get('home', None)
        else:
            scored = goals.get('for', {}).get('average', {}).get('away', None)

        if scored is None:
            scored = goals.get('for', {}).get('average', {}).get('total', None)

        if scored is not None:
            return float(scored) / league_avg if league_avg > 0 else 1.0
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass

    return 1.0


def _extract_defense_weakness(team_stats: dict, league_avg: float,
                                is_home: bool) -> float:
    """
    Wyciąga słabość obrony drużyny (stosunek goli straconych do średniej ligi).

    Słabość > 1.0 oznacza drużynę tracącą więcej goli niż przeciętnie.
    """
    if not team_stats:
        return 1.0

    try:
        goals = team_stats.get('goals', {})
        if is_home:
            conceded = goals.get('against', {}).get('average', {}).get('home', None)
        else:
            conceded = goals.get('against', {}).get('average', {}).get('away', None)

        if conceded is None:
            conceded = goals.get('against', {}).get('average', {}).get('total', None)

        if conceded is not None:
            return float(conceded) / league_avg if league_avg > 0 else 1.0
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass

    return 1.0


def _blend_forms(form1: dict, form2: dict, weight_primary: float = 0.8) -> dict:
    """Miesza dwa słowniki formy z wagami."""
    w2 = 1.0 - weight_primary
    result = {}
    for key in form1:
        if key in form2 and isinstance(form1[key], (int, float)):
            result[key] = form1[key] * weight_primary + form2[key] * w2
        else:
            result[key] = form1[key]
    return result


def _determine_prediction(p_home: float, p_draw: float, p_away: float,
                           home_name: str, away_name: str) -> str:
    """Wyznacza typ na mecz na podstawie najwyższego prawdopodobieństwa."""
    max_prob = max(p_home, p_draw, p_away)

    if max_prob == p_home:
        return f"1 ({home_name})"
    elif max_prob == p_draw:
        return "X (Remis)"
    else:
        return f"2 ({away_name})"


def _calculate_confidence(p_home: float, p_draw: float, p_away: float) -> str:
    """
    Oblicza poziom pewności predykcji.

    Używa odwrotności entropii Shannona:
    - Niska entropia = pewna predykcja (jedna opcja dominuje)
    - Wysoka entropia = niepewna predykcja (równe szanse)
    """
    # Znormalizuj
    probs = [p for p in [p_home, p_draw, p_away] if p > 0]

    # Entropia Shannona (maks. = log2(3) ≈ 1.585 dla rozkładu równomiernego)
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)
    max_entropy = math.log2(3)

    # Znormalizuj do 0-1 i odwróć (1 = pewna predykcja)
    confidence_score = 1.0 - (entropy / max_entropy)

    if confidence_score >= 0.5:
        return "Wysoka"
    elif confidence_score >= 0.25:
        return "Średnia"
    else:
        return "Niska"
