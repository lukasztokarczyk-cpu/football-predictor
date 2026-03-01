"""
app.py - Webowa aplikacja Streamlit dla systemu predykcji meczów piłkarskich

Uruchomienie lokalne:  streamlit run app.py
Hosting:               Streamlit Cloud (streamlit.io/cloud)
"""

import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
import logging
import os

# ── Konfiguracja strony ──────────────────────────────────────────────────────
st.set_page_config(
    page_title="⚽ Predykcje Meczów",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Style CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* Tło i czcionki */
    .main { background-color: #0e1117; }
    h1 { color: #00d4aa !important; font-size: 2.2rem !important; }

    /* Karty meczów */
    .match-card {
        background: linear-gradient(135deg, #1a1f2e 0%, #16213e 100%);
        border: 1px solid #2d3561;
        border-radius: 12px;
        padding: 18px 22px;
        margin-bottom: 14px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.3);
    }
    .match-header {
        font-size: 1.15rem;
        font-weight: 700;
        color: #ffffff;
        margin-bottom: 6px;
    }
    .league-badge {
        font-size: 0.75rem;
        color: #8892b0;
        margin-bottom: 12px;
    }

    /* Paski prawdopodobieństwa */
    .prob-row { display: flex; gap: 8px; margin: 10px 0; }
    .prob-box {
        flex: 1;
        text-align: center;
        border-radius: 8px;
        padding: 10px 4px;
        font-weight: 700;
        font-size: 1.1rem;
    }
    .prob-home { background: #1a472a; color: #4ade80; border: 1px solid #4ade80; }
    .prob-draw { background: #3d2e00; color: #fbbf24; border: 1px solid #fbbf24; }
    .prob-away { background: #3d1515; color: #f87171; border: 1px solid #f87171; }

    /* Najlepszy wynik */
    .best-score {
        background: #0f3460;
        color: #00d4aa;
        border-radius: 8px;
        padding: 6px 14px;
        font-size: 1.5rem;
        font-weight: 900;
        display: inline-block;
        margin: 6px 0;
        border: 1px solid #00d4aa;
    }

    /* Typy */
    .prediction-badge {
        background: #fbbf24;
        color: #000;
        border-radius: 6px;
        padding: 3px 10px;
        font-size: 0.85rem;
        font-weight: 700;
    }
    .confidence-high { color: #4ade80; font-weight: 600; }
    .confidence-mid  { color: #fbbf24; font-weight: 600; }
    .confidence-low  { color: #f87171; font-weight: 600; }

    /* Metryki ELO */
    .elo-bar {
        font-size: 0.8rem;
        color: #8892b0;
        margin-top: 8px;
    }

    /* Sekcja błędu */
    .error-box {
        background: #2d0000;
        border: 1px solid #f87171;
        border-radius: 8px;
        padding: 14px;
        color: #f87171;
    }

    /* Spinner / ładowanie */
    .stSpinner > div { border-top-color: #00d4aa !important; }

    /* Sidebar */
    .css-1d391kg { background-color: #0e1117; }

    /* Ukryj menu Streamlit */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ── Helpery renderowania ─────────────────────────────────────────────────────

def confidence_html(conf: str) -> str:
    cls = {"Wysoka": "confidence-high", "Średnia": "confidence-mid", "Niska": "confidence-low"}.get(conf, "confidence-low")
    return f'<span class="{cls}">{conf}</span>'


def render_match_card(pred: dict):
    home = pred.get("home_team", "?")
    away = pred.get("away_team", "?")
    league = pred.get("league", "")
    country = pred.get("country", "")
    date = pred.get("date", "")
    p1   = pred.get("prob_home_win", 0)
    px   = pred.get("prob_draw", 0)
    p2   = pred.get("prob_away_win", 0)
    score      = pred.get("most_likely_score", "?:?")
    score_prob = pred.get("most_likely_score_prob", 0)
    prediction = pred.get("prediction", "?")
    confidence = pred.get("confidence", "?")
    exp_h = pred.get("expected_goals_home", 0)
    exp_a = pred.get("expected_goals_away", 0)
    elo_h = pred.get("home_elo", 0)
    elo_a = pred.get("away_elo", 0)
    top   = pred.get("top_scores", [])

    top_html = "  ·  ".join(top[:4]) if top else ""

    st.markdown(f"""
<div class="match-card">
  <div class="match-header">⚽ {home} &nbsp;vs&nbsp; {away}</div>
  <div class="league-badge">🏆 {country} – {league} &nbsp;|&nbsp; 📅 {date}</div>

  <div class="prob-row">
    <div class="prob-box prob-home">1<br>{p1:.0f}%<br><small>{home[:12]}</small></div>
    <div class="prob-box prob-draw">X<br>{px:.0f}%<br><small>Remis</small></div>
    <div class="prob-box prob-away">2<br>{p2:.0f}%<br><small>{away[:12]}</small></div>
  </div>

  <div style="margin-top:10px;">
    <span style="color:#8892b0;font-size:.85rem;">Najp. wynik: </span>
    <span class="best-score">{score}</span>
    <span style="color:#8892b0;font-size:.8rem;"> ({score_prob:.1f}%)</span>
    &nbsp;&nbsp;
    <span class="prediction-badge">Typ: {prediction}</span>
    &nbsp;
    Pewność: {confidence_html(confidence)}
  </div>

  <div class="elo-bar">
    ⚡ Oczek. gole: <b>{exp_h:.2f}</b> – <b>{exp_a:.2f}</b>
    &nbsp;|&nbsp; ELO: <b>{elo_h}</b> vs <b>{elo_a}</b>
    &nbsp;|&nbsp; Top wyniki: {top_html}
  </div>
</div>
""", unsafe_allow_html=True)


def render_summary_metrics(predictions: list):
    total = len(predictions)
    h_wins = sum(1 for p in predictions if "1 (" in p.get("prediction", ""))
    draws  = sum(1 for p in predictions if "X"   in p.get("prediction", ""))
    a_wins = sum(1 for p in predictions if "2 (" in p.get("prediction", ""))
    avg_goals = sum(
        p.get("expected_goals_home", 0) + p.get("expected_goals_away", 0)
        for p in predictions
    ) / total if total else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("📋 Mecze", total)
    c2.metric("🟢 Typy 1", h_wins)
    c3.metric("🟡 Typy X", draws)
    c4.metric("🔴 Typy 2", a_wins)
    c5.metric("⚽ Śr. goli", f"{avg_goals:.2f}")


# ── Główna logika aplikacji ──────────────────────────────────────────────────

def run_predictions_cached(api_key: str, mode: str, league_id, season: int):
    """Owinięcie pipeline'u predykcji – cache po parametrach."""
    from api import APIFootballClient
    from model import MatchPredictor, EloRating

    # Import pomocniczych funkcji z main.py
    from main import (
        fetch_fixtures_for_mode,
        enrich_fixture_data,
        build_global_elo,
    )

    client = APIFootballClient(api_key)
    fixtures = fetch_fixtures_for_mode(client, mode, league_id, season)

    if not fixtures:
        return [], "Brak meczów dla wybranych parametrów."

    elo = build_global_elo(client, fixtures, season)
    predictor = MatchPredictor(elo_ratings=elo)

    predictions = []
    errors = []
    progress = st.progress(0, text="Analizuję mecze…")

    for i, fixture in enumerate(fixtures):
        home = fixture.get("teams", {}).get("home", {}).get("name", "?")
        away = fixture.get("teams", {}).get("away", {}).get("name", "?")
        progress.progress((i + 1) / len(fixtures), text=f"[{i+1}/{len(fixtures)}] {home} vs {away}")

        try:
            data = enrich_fixture_data(client, fixture, season)
            pred = predictor.predict_match(
                fixture=data["fixture"],
                home_team_fixtures=data["home_last_fixtures"],
                away_team_fixtures=data["away_last_fixtures"],
                home_stats=data["home_stats"],
                away_stats=data["away_stats"],
                home_injuries=data["home_injuries"],
                away_injuries=data["away_injuries"],
                h2h_fixtures=data["h2h"],
            )
            predictions.append(pred)
        except Exception as e:
            errors.append(f"{home} vs {away}: {e}")

    progress.empty()
    err_msg = "\n".join(errors) if errors else None
    return predictions, err_msg


def run_demo_predictions():
    """Generuje demo predykcje bez klucza API."""
    from model import MatchPredictor, EloRating

    elo = EloRating()
    elo.ratings = {1: 1750, 2: 1680, 3: 1600, 4: 1550, 5: 1520, 6: 1490}
    predictor = MatchPredictor(elo_ratings=elo)

    demo_matches = [
        (1, "Manchester City", 2, "Liverpool",     "Premier League", "England",  [(3,1),(2,0),(4,1),(1,0),(3,2)], [(2,1),(3,2),(1,1),(2,0),(1,2)], "2.8","0.8","2.5","1.1"),
        (3, "Real Madrid",     4, "Barcelona",     "La Liga",        "Spain",    [(4,0),(2,1),(3,0),(2,2),(1,0)], [(3,1),(2,0),(4,2),(1,0),(2,1)], "3.1","0.7","2.9","0.9"),
        (5, "Bayern Munich",   6, "Borussia Dortmund","Bundesliga",  "Germany",  [(3,1),(5,0),(2,1),(4,2),(2,0)], [(2,2),(1,1),(3,1),(0,1),(2,0)], "3.4","0.9","2.3","1.4"),
    ]

    predictions = []
    for (hid, hname, aid, aname, league, country, hfix, afix, hg, hga, ag, aga) in demo_matches:
        fixture = {
            "fixture": {"id": hid*1000, "date": (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")},
            "teams": {"home": {"id": hid, "name": hname}, "away": {"id": aid, "name": aname}},
            "league": {"id": 39, "name": league, "country": country},
            "goals": {"home": None, "away": None},
        }
        def make_fix(team_id, tname, results, base_id):
            out = []
            for i, (gh, ga) in enumerate(results):
                out.append({
                    "fixture": {"id": base_id+i, "date": f"2024-02-{i+1:02d}", "status": {"short": "FT"}},
                    "teams": {"home": {"id": team_id, "name": tname}, "away": {"id": 999, "name": "Opp"}},
                    "goals": {"home": gh, "away": ga},
                })
            return out

        home_stats = {"goals": {"for": {"average": {"home": hg,  "total": hg}},
                                "against": {"average": {"home": hga, "total": hga}}}}
        away_stats = {"goals": {"for": {"average": {"away": ag,  "total": ag}},
                                "against": {"average": {"away": aga, "total": aga}}}}

        pred = predictor.predict_match(
            fixture=fixture,
            home_team_fixtures=make_fix(hid, hname, hfix, hid*100),
            away_team_fixtures=make_fix(aid, aname, afix, aid*100),
            home_stats=home_stats,
            away_stats=away_stats,
        )
        predictions.append(pred)

    return predictions


# ── SIDEBAR ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚽ Predykcje Piłkarskie")
    st.markdown("---")

    # Klucz API
    st.markdown("### 🔑 Klucz API")
    # Najpierw sprawdź secrets (Streamlit Cloud) lub zmienną środowiskową
    default_key = st.secrets.get("API_FOOTBALL_KEY", "") if hasattr(st, "secrets") else os.getenv("API_FOOTBALL_KEY", "")
    api_key_input = st.text_input(
        "API-Football Key",
        value=default_key,
        type="password",
        placeholder="Wklej klucz z dashboard.api-football.com…",
        help="Zdobądź klucz na dashboard.api-football.com (darmowe, bez karty)"
    )
    if not api_key_input:
        st.caption("Brak klucza → działa tryb DEMO")

    st.markdown("---")

    # Tryb pobierania
    st.markdown("### 📅 Mecze")
    mode_labels = {
        "Dziś + Jutro": "both",
        "Tylko Dziś": "today",
        "Tylko Jutro": "tomorrow",
        "Wszystkie (pop. ligi)": "all",
    }
    mode_choice = st.selectbox("Zakres meczów", list(mode_labels.keys()))
    mode = mode_labels[mode_choice]

    # Liga
    leagues = {
        "Wszystkie": None,
        "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League": 39,
        "🇪🇸 La Liga": 140,
        "🇩🇪 Bundesliga": 78,
        "🇮🇹 Serie A": 135,
        "🇫🇷 Ligue 1": 61,
        "🇵🇱 Ekstraklasa": 106,
        "🏆 Champions League": 2,
        "🏆 Europa League": 3,
    }
    league_choice = st.selectbox("Liga", list(leagues.keys()))
    league_id = leagues[league_choice]

    # Sezon
    season = st.selectbox("Sezon", [2024, 2023], index=0)

    st.markdown("---")

    # Przycisk
    run_btn = st.button("🔍 Analizuj mecze", use_container_width=True, type="primary")

    st.markdown("---")
    st.markdown("""
**ℹ️ Jak zdobyć klucz API:**
1. Wejdź na [dashboard.api-football.com](https://dashboard.api-football.com/register)
2. Zarejestruj się (email, bez karty)
3. Skopiuj klucz z dashboardu
4. Wklej go powyżej
""")


# ── GŁÓWNA STRONA ────────────────────────────────────────────────────────────

st.markdown("# ⚽ System Predykcji Meczów Piłkarskich")
st.markdown(
    "Model **Poissona** · Rankingi **ELO** · Analiza **Formy** · Wpływ **Kontuzji**",
)
st.markdown("---")

# Stan sesji
if "predictions" not in st.session_state:
    st.session_state.predictions = []
if "last_run" not in st.session_state:
    st.session_state.last_run = None
if "demo_shown" not in st.session_state:
    st.session_state.demo_shown = False

# ── Przy starcie pokaż demo automatycznie ──
if not st.session_state.demo_shown and not run_btn:
    st.info("👋 Nie masz klucza API? Poniżej widzisz **tryb DEMO** z przykładowymi predykcjami. Wklej klucz w panelu bocznym i kliknij **Analizuj mecze** by pobrać prawdziwe dane.")

    with st.spinner("Ładowanie przykładowych predykcji…"):
        demo_preds = run_demo_predictions()

    st.session_state.predictions = demo_preds
    st.session_state.last_run = "DEMO"
    st.session_state.demo_shown = True

# ── Obsługa przycisku Analizuj ──
if run_btn:
    if not api_key_input:
        st.warning("⚠️ Brak klucza API — pokazuję dane demo. Wklej klucz w panelu bocznym.")
        with st.spinner("Ładowanie demo…"):
            st.session_state.predictions = run_demo_predictions()
        st.session_state.last_run = "DEMO"
    else:
        with st.spinner("Łączę z API i analizuję mecze… (może potrwać 30–90 sek.)"):
            try:
                preds, err_msg = run_predictions_cached(
                    api_key_input, mode, league_id, season
                )
                st.session_state.predictions = preds
                st.session_state.last_run = datetime.now().strftime("%H:%M:%S")
                if err_msg:
                    st.warning(f"Niektóre mecze pominięto z powodu błędów:\n{err_msg}")
                if not preds:
                    st.error("Brak meczów do analizy dla wybranych parametrów.")
            except Exception as e:
                st.error(f"❌ Błąd: {e}")

# ── Wyświetl wyniki ──
predictions = st.session_state.predictions

if predictions:
    last = st.session_state.last_run
    mode_label = f"  |  Tryb: **{mode_choice}**" if last != "DEMO" else "  |  🎮 **TRYB DEMO**"
    st.markdown(f"*Ostatnia aktualizacja: **{last}***{mode_label}")

    # Metryki podsumowania
    render_summary_metrics(predictions)
    st.markdown("---")

    # Filtry nad listą
    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        search = st.text_input("🔎 Szukaj drużyny", placeholder="np. Barcelona…")
    with col_f2:
        filter_type = st.selectbox("Filtruj typ", ["Wszystkie", "1 (Gospodarz)", "X (Remis)", "2 (Gość)"])

    # Filtrowanie
    filtered = predictions
    if search:
        s = search.lower()
        filtered = [p for p in filtered if s in p.get("home_team","").lower() or s in p.get("away_team","").lower()]
    if filter_type == "1 (Gospodarz)":
        filtered = [p for p in filtered if "1 (" in p.get("prediction","")]
    elif filter_type == "X (Remis)":
        filtered = [p for p in filtered if "X" in p.get("prediction","")]
    elif filter_type == "2 (Gość)":
        filtered = [p for p in filtered if "2 (" in p.get("prediction","")]

    st.markdown(f"**{len(filtered)} meczów**")

    # Karty meczów
    for pred in filtered:
        render_match_card(pred)

    # ── Eksport CSV ──
    st.markdown("---")
    st.markdown("### 💾 Eksport")

    df = pd.DataFrame([{
        "Data":             p.get("date",""),
        "Liga":             p.get("league",""),
        "Kraj":             p.get("country",""),
        "Gospodarz":        p.get("home_team",""),
        "Gość":             p.get("away_team",""),
        "Prawdop. 1":       f"{p.get('prob_home_win',0):.1f}%",
        "Prawdop. X":       f"{p.get('prob_draw',0):.1f}%",
        "Prawdop. 2":       f"{p.get('prob_away_win',0):.1f}%",
        "Oczek. gole gosp.":p.get("expected_goals_home",0),
        "Oczek. gole gość": p.get("expected_goals_away",0),
        "Najp. wynik":      p.get("most_likely_score",""),
        "Prawdop. wyniku":  f"{p.get('most_likely_score_prob',0):.1f}%",
        "Typ":              p.get("prediction",""),
        "Pewność":          p.get("confidence",""),
        "ELO gosp.":        p.get("home_elo",0),
        "ELO gość":         p.get("away_elo",0),
    } for p in filtered])

    csv_data = df.to_csv(index=False, sep=";", encoding="utf-8-sig")
    st.download_button(
        label="⬇️ Pobierz CSV",
        data=csv_data,
        file_name=f"predykcje_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    with st.expander("📊 Podgląd tabeli"):
        st.dataframe(df, use_container_width=True, hide_index=True)

else:
    st.markdown("""
<div style="text-align:center; padding: 60px 20px; color: #8892b0;">
    <div style="font-size: 4rem;">⚽</div>
    <h3 style="color:#8892b0;">Kliknij <b>Analizuj mecze</b> w panelu bocznym</h3>
    <p>Możesz też użyć trybu demo bez klucza API.</p>
</div>
""", unsafe_allow_html=True)
