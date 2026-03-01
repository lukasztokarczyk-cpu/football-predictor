"""
app.py - Webowa aplikacja Streamlit: Predykcja meczu drużyna vs drużyna
"""

import streamlit as st
import pandas as pd
import os
from datetime import datetime

st.set_page_config(
    page_title="⚽ Predykcja Meczu",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    .main { background-color: #0e1117; }
    h1 { color: #00d4aa !important; }

    .vs-card {
        background: linear-gradient(135deg, #1a1f2e 0%, #16213e 100%);
        border: 1px solid #2d3561;
        border-radius: 16px;
        padding: 30px;
        margin: 20px 0;
        box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    }
    .team-name {
        font-size: 1.6rem;
        font-weight: 900;
        color: #ffffff;
        text-align: center;
    }
    .vs-text {
        font-size: 2rem;
        font-weight: 900;
        color: #fbbf24;
        text-align: center;
        padding-top: 20px;
    }
    .prob-box {
        border-radius: 12px;
        padding: 20px 10px;
        text-align: center;
        font-weight: 700;
    }
    .prob-home { background: #1a472a; border: 2px solid #4ade80; }
    .prob-draw { background: #3d2e00; border: 2px solid #fbbf24; }
    .prob-away { background: #3d1515; border: 2px solid #f87171; }
    .prob-number { font-size: 2.5rem; }
    .prob-label  { font-size: 0.85rem; color: #8892b0; margin-top: 4px; }
    .best-score {
        background: #0f3460;
        color: #00d4aa;
        border-radius: 10px;
        padding: 10px 28px;
        font-size: 2.2rem;
        font-weight: 900;
        border: 2px solid #00d4aa;
        display: inline-block;
    }
    .stat-row {
        background: #12172a;
        border-radius: 8px;
        padding: 10px 16px;
        margin: 6px 0;
        display: flex;
        justify-content: space-between;
        font-size: 0.9rem;
    }
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def get_api_key():
    try:
        key = st.secrets.get("API_FOOTBALL_KEY", "")
    except Exception:
        key = ""
    return key or os.getenv("API_FOOTBALL_KEY", "")


@st.cache_data(ttl=600, show_spinner=False)
def search_teams(name: str, api_key: str) -> list:
    from api import APIFootballClient
    client = APIFootballClient(api_key)
    data = client._get("/teams", {"search": name})
    return data.get("response", [])


@st.cache_data(ttl=600, show_spinner=False)
def fetch_prediction(home_id: int, away_id: int,
                     home_name: str, away_name: str,
                     season: int, api_key: str) -> dict:
    from api import APIFootballClient
    from model import MatchPredictor, EloRating

    client = APIFootballClient(api_key)

    home_fixtures = client.get_team_last_fixtures(home_id, last=15)
    away_fixtures = client.get_team_last_fixtures(away_id, last=15)
    h2h = client.get_h2h(home_id, away_id, last=10)

    home_league_id = _find_main_league(home_fixtures)
    away_league_id = _find_main_league(away_fixtures)

    home_stats = client.get_team_statistics(home_id, home_league_id, season) if home_league_id else {}
    away_stats = client.get_team_statistics(away_id, away_league_id, season) if away_league_id else {}

    home_injuries = client.get_injuries(home_id, season=season)
    away_injuries = client.get_injuries(away_id, season=season)

    elo = EloRating()
    elo.build_from_fixtures(home_fixtures + away_fixtures + h2h)

    predictor = MatchPredictor(elo_ratings=elo)

    fixture = {
        "fixture": {"id": 0, "date": datetime.now().strftime("%Y-%m-%d")},
        "teams": {
            "home": {"id": home_id, "name": home_name},
            "away": {"id": away_id, "name": away_name},
        },
        "league": {"id": home_league_id or 0, "name": "", "country": ""},
        "goals": {"home": None, "away": None},
    }

    pred = predictor.predict_match(
        fixture=fixture,
        home_team_fixtures=home_fixtures,
        away_team_fixtures=away_fixtures,
        home_stats=home_stats,
        away_stats=away_stats,
        home_injuries=home_injuries,
        away_injuries=away_injuries,
        h2h_fixtures=h2h,
    )

    pred["h2h_summary"] = _summarize_h2h(h2h, home_id, away_id, home_name, away_name)
    pred["home_form_details"] = _form_details(home_fixtures, home_id)
    pred["away_form_details"] = _form_details(away_fixtures, away_id)

    return pred


def _find_main_league(fixtures: list):
    counts: dict = {}
    for f in fixtures:
        lid = f.get("league", {}).get("id")
        if lid:
            counts[lid] = counts.get(lid, 0) + 1
    return max(counts, key=counts.get) if counts else None


def _summarize_h2h(fixtures, home_id, away_id, home_name, away_name):
    home_wins = draws = away_wins = 0
    last_results = []
    sorted_f = sorted(fixtures,
                      key=lambda f: f.get("fixture", {}).get("date", ""),
                      reverse=True)
    for f in sorted_f[:10]:
        gh = f.get("goals", {}).get("home")
        ga = f.get("goals", {}).get("away")
        fhome_id = f.get("teams", {}).get("home", {}).get("id")
        if gh is None or ga is None:
            continue
        is_home = (fhome_id == home_id)
        scored   = gh if is_home else ga
        conceded = ga if is_home else gh
        if scored > conceded:
            home_wins += 1
            result = f"✅ {home_name} {scored}:{conceded} {away_name}"
        elif scored == conceded:
            draws += 1
            result = f"🟡 {home_name} {scored}:{conceded} {away_name}"
        else:
            away_wins += 1
            result = f"❌ {home_name} {scored}:{conceded} {away_name}"
        date = f.get("fixture", {}).get("date", "")[:10]
        last_results.append(f"{date}  {result}")
    return {"home_wins": home_wins, "draws": draws, "away_wins": away_wins,
            "last_results": last_results, "home_name": home_name, "away_name": away_name}


def _form_details(fixtures, team_id):
    results = []
    sorted_f = sorted(fixtures,
                      key=lambda f: f.get("fixture", {}).get("date", ""),
                      reverse=True)
    for f in sorted_f:
        if f.get("fixture", {}).get("status", {}).get("short") != "FT":
            continue
        home_id = f.get("teams", {}).get("home", {}).get("id")
        gh = f.get("goals", {}).get("home")
        ga = f.get("goals", {}).get("away")
        if gh is None or ga is None:
            continue
        is_home = (home_id == team_id)
        scored   = gh if is_home else ga
        conceded = ga if is_home else gh
        opp = f.get("teams", {}).get("away" if is_home else "home", {}).get("name", "?")
        date = f.get("fixture", {}).get("date", "")[:10]
        emoji = "🟢" if scored > conceded else ("🟡" if scored == conceded else "🔴")
        results.append(f"{emoji} {date}  {'vs' if is_home else '@'} {opp}  **{scored}:{conceded}**")
        if len(results) >= 5:
            break
    return results


def render_result(pred, home_name, away_name):
    p1  = pred.get("prob_home_win", 0)
    px  = pred.get("prob_draw", 0)
    p2  = pred.get("prob_away_win", 0)
    score      = pred.get("most_likely_score", "?:?")
    score_prob = pred.get("most_likely_score_prob", 0)
    prediction = pred.get("prediction", "?")
    confidence = pred.get("confidence", "?")
    exp_h = pred.get("expected_goals_home", 0)
    exp_a = pred.get("expected_goals_away", 0)
    elo_h = pred.get("home_elo", 0)
    elo_a = pred.get("away_elo", 0)

    st.markdown('<div class="vs-card">', unsafe_allow_html=True)

    c1, c2, c3 = st.columns([5, 2, 5])
    with c1:
        st.markdown(f'<div class="team-name">🏠 {home_name}</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="vs-text">VS</div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="team-name">✈️ {away_name}</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"""
        <div class="prob-box prob-home">
            <div class="prob-number" style="color:#4ade80">{p1:.0f}%</div>
            <div style="font-size:1.1rem;color:#4ade80;font-weight:700;">1</div>
            <div class="prob-label">Wygrana {home_name[:15]}</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="prob-box prob-draw">
            <div class="prob-number" style="color:#fbbf24">{px:.0f}%</div>
            <div style="font-size:1.1rem;color:#fbbf24;font-weight:700;">X</div>
            <div class="prob-label">Remis</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
        <div class="prob-box prob-away">
            <div class="prob-number" style="color:#f87171">{p2:.0f}%</div>
            <div style="font-size:1.1rem;color:#f87171;font-weight:700;">2</div>
            <div class="prob-label">Wygrana {away_name[:15]}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)

    with c1:
        st.markdown("#### 🎯 Przewidywany wynik")
        st.markdown(f'<div style="text-align:center;margin:10px 0"><span class="best-score">{score}</span></div>', unsafe_allow_html=True)
        st.markdown(f'<div style="text-align:center;color:#8892b0">prawdopodobieństwo: {score_prob:.1f}%</div>', unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### 📊 Top 5 wyników")
        for s in pred.get("top_scores", []):
            st.markdown(f"- {s}")

    with c2:
        st.markdown("#### ⚡ Statystyki modelu")
        st.markdown(f"""
        <div class="stat-row"><span>Typ</span><span><b>{prediction}</b></span></div>
        <div class="stat-row"><span>Pewność</span><span><b>{confidence}</b></span></div>
        <div class="stat-row"><span>Oczek. gole</span><span><b>{exp_h:.2f} – {exp_a:.2f}</b></span></div>
        <div class="stat-row"><span>ELO Gospodarz</span><span><b>{elo_h}</b></span></div>
        <div class="stat-row"><span>ELO Gość</span><span><b>{elo_a}</b></span></div>
        <div class="stat-row"><span>Forma gosp.</span><span><b>{pred.get('home_form_score',0):.2f}</b></span></div>
        <div class="stat-row"><span>Forma gość</span><span><b>{pred.get('away_form_score',0):.2f}</b></span></div>
        """, unsafe_allow_html=True)

    h2h = pred.get("h2h_summary", {})
    if h2h and (h2h.get("home_wins", 0) + h2h.get("draws", 0) + h2h.get("away_wins", 0)) > 0:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### 🔁 Historia bezpośrednich meczów (H2H)")
        hc1, hc2, hc3 = st.columns(3)
        hc1.metric(f"✅ Wygrane {home_name[:12]}", h2h.get("home_wins", 0))
        hc2.metric("🟡 Remisy", h2h.get("draws", 0))
        hc3.metric(f"✅ Wygrane {away_name[:12]}", h2h.get("away_wins", 0))
        with st.expander("Pokaż ostatnie mecze H2H"):
            for r in h2h.get("last_results", []):
                st.markdown(r)

    home_form = pred.get("home_form_details", [])
    away_form = pred.get("away_form_details", [])
    if home_form or away_form:
        st.markdown("<br>", unsafe_allow_html=True)
        fc1, fc2 = st.columns(2)
        with fc1:
            st.markdown(f"#### 📈 Forma {home_name}")
            for r in home_form:
                st.markdown(r)
        with fc2:
            st.markdown(f"#### 📈 Forma {away_name}")
            for r in away_form:
                st.markdown(r)


# ── SIDEBAR ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚽ Predykcja Meczu")
    st.markdown("Wybierz dwie drużyny i sprawdź kto wygra!")
    st.markdown("---")

    api_key = get_api_key()
    if not api_key:
        api_key = st.text_input(
            "🔑 Klucz API",
            type="password",
            placeholder="Wklej klucz z api-football.com…",
        )

    season = st.selectbox("📅 Sezon", [2024, 2023], index=0)
    st.markdown("---")
    st.markdown("""
**ℹ️ Jak zdobyć klucz:**
1. [dashboard.api-football.com](https://dashboard.api-football.com/register)
2. Zarejestruj się (bez karty)
3. Skopiuj klucz z dashboardu
""")


# ── GŁÓWNA STRONA ─────────────────────────────────────────────────────────────

st.markdown("# ⚽ Predykcja Meczu Piłkarskiego")
st.markdown("Wyszukaj dwie drużyny i sprawdź przewidywany wynik — z analizą formy, H2H i rankingiem ELO.")
st.markdown("---")

if not api_key:
    st.warning("⚠️ Wklej klucz API w panelu bocznym.")
    st.info("Zdobądź darmowy klucz na [dashboard.api-football.com](https://dashboard.api-football.com/register)")
    st.stop()

st.markdown("### 🔍 Wybierz drużyny")
col1, col2 = st.columns(2)

home_team = None
away_team = None

with col1:
    st.markdown("**🏠 Gospodarz**")
    home_search = st.text_input("Wyszukaj gospodarz", placeholder="np. Barcelona…", key="home_search")

    if home_search and len(home_search) >= 3:
        with st.spinner("Szukam…"):
            home_results = search_teams(home_search, api_key)
        if home_results:
            home_options = {f"{t['team']['name']} ({t['team'].get('country','')})": t for t in home_results[:10]}
            home_choice = st.selectbox("Wybierz", list(home_options.keys()), key="home_choice")
            home_team = home_options[home_choice]
            st.success(f"✅ {home_team['team']['name']}")
        else:
            st.error("Nie znaleziono. Spróbuj innej nazwy.")

with col2:
    st.markdown("**✈️ Gość**")
    away_search = st.text_input("Wyszukaj gość", placeholder="np. Real Madrid…", key="away_search")

    if away_search and len(away_search) >= 3:
        with st.spinner("Szukam…"):
            away_results = search_teams(away_search, api_key)
        if away_results:
            away_options = {f"{t['team']['name']} ({t['team'].get('country','')})": t for t in away_results[:10]}
            away_choice = st.selectbox("Wybierz", list(away_options.keys()), key="away_choice")
            away_team = away_options[away_choice]
            st.success(f"✅ {away_team['team']['name']}")
        else:
            st.error("Nie znaleziono. Spróbuj innej nazwy.")

st.markdown("<br>", unsafe_allow_html=True)
predict_btn = st.button(
    "🔮 Oblicz predykcję",
    use_container_width=True,
    type="primary",
    disabled=(home_team is None or away_team is None)
)

if predict_btn and home_team and away_team:
    home_id   = home_team["team"]["id"]
    away_id   = away_team["team"]["id"]
    home_name = home_team["team"]["name"]
    away_name = away_team["team"]["name"]

    if home_id == away_id:
        st.error("Wybierz dwie różne drużyny!")
    else:
        with st.spinner(f"Analizuję {home_name} vs {away_name}… (15–30 sek.)"):
            try:
                pred = fetch_prediction(home_id, away_id, home_name, away_name, season, api_key)
                st.markdown("---")
                render_result(pred, home_name, away_name)
            except Exception as e:
                st.error(f"❌ Błąd: {e}")
                st.info("Sprawdź klucz API i limit zapytań (100/dzień na darmowym planie).")
