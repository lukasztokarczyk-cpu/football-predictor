"""
app.py - Predykcja meczu piłkarskiego (football-data.org)
"""

import streamlit as st
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
    .team-name { font-size: 1.6rem; font-weight: 900; color: #fff; text-align: center; }
    .vs-text   { font-size: 2rem; font-weight: 900; color: #fbbf24; text-align: center; padding-top: 20px; }
    .prob-box  { border-radius: 12px; padding: 20px 10px; text-align: center; font-weight: 700; }
    .prob-home { background: #1a472a; border: 2px solid #4ade80; }
    .prob-draw { background: #3d2e00; border: 2px solid #fbbf24; }
    .prob-away { background: #3d1515; border: 2px solid #f87171; }
    .prob-number { font-size: 2.5rem; }
    .best-score {
        background: #0f3460; color: #00d4aa; border-radius: 10px;
        padding: 10px 28px; font-size: 2.2rem; font-weight: 900;
        border: 2px solid #00d4aa; display: inline-block;
    }
    .stat-row {
        background: #12172a; border-radius: 8px; padding: 10px 16px;
        margin: 6px 0; display: flex; justify-content: space-between; font-size: 0.9rem;
    }
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

API_KEY = "0239f610e5474033ba919718886d7688"

def get_api_key():
    try:
        k = st.secrets.get("FOOTBALL_DATA_KEY", "")
        if k and k.strip(): return k.strip()
    except Exception:
        pass
    return os.getenv("FOOTBALL_DATA_KEY", API_KEY)


@st.cache_data(ttl=600, show_spinner=False)
def search_teams(name: str, api_key: str):
    from api import FootballDataClient
    client = FootballDataClient(api_key)
    return client.search_teams(name)


@st.cache_data(ttl=600, show_spinner=False)
def fetch_prediction(home_id: int, away_id: int, home_name: str, away_name: str, api_key: str) -> dict:
    from api import FootballDataClient, convert_match_to_fixture
    from model import MatchPredictor, EloRating

    client = FootballDataClient(api_key)

    # Pobierz mecze obu drużyn
    home_matches_raw = client.get_team_matches(home_id, limit=20)
    away_matches_raw = client.get_team_matches(away_id, limit=20)
    h2h_raw          = client.get_h2h(home_id, away_id, limit=10)

    # Konwertuj do formatu model.py
    home_fixtures = [convert_match_to_fixture(m, home_id) for m in home_matches_raw]
    away_fixtures = [convert_match_to_fixture(m, away_id) for m in away_matches_raw]
    h2h_fixtures  = [convert_match_to_fixture(m, home_id) for m in h2h_raw]

    # Statystyki
    home_stats = client.get_team_statistics(home_id)
    away_stats = client.get_team_statistics(away_id)

    # ELO
    elo = EloRating()
    elo.build_from_fixtures(home_fixtures + away_fixtures + h2h_fixtures)

    predictor = MatchPredictor(elo_ratings=elo)

    fixture = {
        "fixture": {"id": 0, "date": datetime.now().strftime("%Y-%m-%d")},
        "teams": {
            "home": {"id": home_id, "name": home_name},
            "away": {"id": away_id, "name": away_name},
        },
        "league": {"id": 0, "name": "", "country": ""},
        "goals": {"home": None, "away": None},
    }

    pred = predictor.predict_match(
        fixture=fixture,
        home_team_fixtures=home_fixtures,
        away_team_fixtures=away_fixtures,
        home_stats=home_stats,
        away_stats=away_stats,
        home_injuries=[],
        away_injuries=[],
        h2h_fixtures=h2h_fixtures,
    )

    pred["h2h_summary"]       = _summarize_h2h(h2h_raw, home_id, away_id, home_name, away_name)
    pred["home_form_details"] = _form_details(home_matches_raw, home_id)
    pred["away_form_details"] = _form_details(away_matches_raw, away_id)
    return pred


def _summarize_h2h(matches, home_id, away_id, home_name, away_name):
    home_wins = draws = away_wins = 0
    last_results = []
    for m in sorted(matches, key=lambda x: x.get("utcDate",""), reverse=True)[:10]:
        score = m.get("score",{}).get("fullTime",{})
        gh, ga = score.get("home"), score.get("away")
        if gh is None or ga is None: continue
        fhome_id = m.get("homeTeam",{}).get("id")
        is_home  = (fhome_id == home_id)
        scored   = gh if is_home else ga
        conceded = ga if is_home else gh
        date = m.get("utcDate","")[:10]
        if scored > conceded:
            home_wins += 1
            last_results.append(f"{date}  ✅ {home_name} {scored}:{conceded} {away_name}")
        elif scored == conceded:
            draws += 1
            last_results.append(f"{date}  🟡 {home_name} {scored}:{conceded} {away_name}")
        else:
            away_wins += 1
            last_results.append(f"{date}  ❌ {home_name} {scored}:{conceded} {away_name}")
    return {"home_wins": home_wins, "draws": draws, "away_wins": away_wins,
            "last_results": last_results, "home_name": home_name, "away_name": away_name}


def _form_details(matches, team_id):
    results = []
    for m in sorted(matches, key=lambda x: x.get("utcDate",""), reverse=True):
        if m.get("status") != "FINISHED": continue
        score = m.get("score",{}).get("fullTime",{})
        gh, ga = score.get("home"), score.get("away")
        if gh is None or ga is None: continue
        home_id  = m.get("homeTeam",{}).get("id")
        is_home  = (home_id == team_id)
        scored   = gh if is_home else ga
        conceded = ga if is_home else gh
        opp  = m.get("awayTeam" if is_home else "homeTeam",{}).get("name","?")
        date = m.get("utcDate","")[:10]
        emoji = "🟢" if scored > conceded else ("🟡" if scored == conceded else "🔴")
        results.append(f"{emoji} {date}  {'vs' if is_home else '@'} {opp}  **{scored}:{conceded}**")
        if len(results) >= 5: break
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
    c1, c2, c3 = st.columns([5,2,5])
    with c1: st.markdown(f'<div class="team-name">🏠 {home_name}</div>', unsafe_allow_html=True)
    with c2: st.markdown('<div class="vs-text">VS</div>', unsafe_allow_html=True)
    with c3: st.markdown(f'<div class="team-name">✈️ {away_name}</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f'<div class="prob-box prob-home"><div class="prob-number" style="color:#4ade80">{p1:.0f}%</div><div style="color:#4ade80;font-weight:700;font-size:1.1rem">1</div><div style="color:#8892b0;font-size:.8rem">Wygrana {home_name[:15]}</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="prob-box prob-draw"><div class="prob-number" style="color:#fbbf24">{px:.0f}%</div><div style="color:#fbbf24;font-weight:700;font-size:1.1rem">X</div><div style="color:#8892b0;font-size:.8rem">Remis</div></div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="prob-box prob-away"><div class="prob-number" style="color:#f87171">{p2:.0f}%</div><div style="color:#f87171;font-weight:700;font-size:1.1rem">2</div><div style="color:#8892b0;font-size:.8rem">Wygrana {away_name[:15]}</div></div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 🎯 Przewidywany wynik")
        st.markdown(f'<div style="text-align:center;margin:10px 0"><span class="best-score">{score}</span></div>', unsafe_allow_html=True)
        st.markdown(f'<div style="text-align:center;color:#8892b0">prawdopodobieństwo: {score_prob:.1f}%</div>', unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### 📊 Top 5 wyników")
        for s in pred.get("top_scores", []): st.markdown(f"- {s}")
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
    total_h2h = h2h.get("home_wins",0) + h2h.get("draws",0) + h2h.get("away_wins",0)
    if total_h2h > 0:
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown("#### 🔁 Historia H2H")
        hc1, hc2, hc3 = st.columns(3)
        hc1.metric(f"✅ {home_name[:12]}", h2h.get("home_wins",0))
        hc2.metric("🟡 Remisy", h2h.get("draws",0))
        hc3.metric(f"✅ {away_name[:12]}", h2h.get("away_wins",0))
        with st.expander("Pokaż mecze H2H"):
            for r in h2h.get("last_results",[]): st.markdown(r)

    hf = pred.get("home_form_details",[])
    af = pred.get("away_form_details",[])
    if hf or af:
        st.markdown("<br>", unsafe_allow_html=True)
        fc1, fc2 = st.columns(2)
        with fc1:
            st.markdown(f"#### 📈 Forma {home_name}")
            for r in hf: st.markdown(r)
        with fc2:
            st.markdown(f"#### 📈 Forma {away_name}")
            for r in af: st.markdown(r)


# ── SIDEBAR ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚽ Predykcja Meczu")
    st.markdown("Wybierz dwie drużyny i sprawdź kto wygra!")
    st.markdown("---")
    api_key = get_api_key()
    st.markdown("---")
    st.markdown("""
**ℹ️ Wyszukiwanie:**
- Wpisz nazwę po **angielsku**
- Minimum **3 litery**
- Np. *Barcelona*, *Liverpool*, *Legia*
""")

# ── GŁÓWNA STRONA ─────────────────────────────────────────────────────────────
st.markdown("# ⚽ Predykcja Meczu Piłkarskiego")
st.markdown("Wyszukaj dwie drużyny i sprawdź przewidywany wynik.")
st.markdown("---")

st.markdown("### 🔍 Wybierz drużyny")
col1, col2 = st.columns(2)

home_team = None
away_team = None

with col1:
    st.markdown("**🏠 Gospodarz**")
    home_search = st.text_input("Wyszukaj gospodarz", placeholder="np. Barcelona…", key="home_search")
    if home_search and len(home_search) >= 3:
        with st.spinner("Szukam…"):
            home_results, home_err = search_teams(home_search, api_key)
        if home_results:
            home_options = {f"{t.get('name','?')} ({t.get('area',{}).get('name','')})": t for t in home_results[:10]}
            home_choice = st.selectbox("Wybierz", list(home_options.keys()), key="home_choice")
            home_team = home_options[home_choice]
            st.success(f"✅ {home_team.get('name','?')}")
        else:
            st.error(f"Nie znaleziono. {home_err or 'Spróbuj po angielsku, min. 3 znaki.'}")

with col2:
    st.markdown("**✈️ Gość**")
    away_search = st.text_input("Wyszukaj gość", placeholder="np. Real Madrid…", key="away_search")
    if away_search and len(away_search) >= 3:
        with st.spinner("Szukam…"):
            away_results, away_err = search_teams(away_search, api_key)
        if away_results:
            away_options = {f"{t.get('name','?')} ({t.get('area',{}).get('name','')})": t for t in away_results[:10]}
            away_choice = st.selectbox("Wybierz", list(away_options.keys()), key="away_choice")
            away_team = away_options[away_choice]
            st.success(f"✅ {away_team.get('name','?')}")
        else:
            st.error(f"Nie znaleziono. {away_err or 'Spróbuj po angielsku, min. 3 znaki.'}")

st.markdown("<br>", unsafe_allow_html=True)
predict_btn = st.button(
    "🔮 Oblicz predykcję",
    use_container_width=True,
    type="primary",
    disabled=(home_team is None or away_team is None)
)

if predict_btn and home_team and away_team:
    home_id   = home_team.get("id")
    away_id   = away_team.get("id")
    home_name = home_team.get("name","?")
    away_name = away_team.get("name","?")

    if home_id == away_id:
        st.error("Wybierz dwie różne drużyny!")
    else:
        with st.spinner(f"Analizuję {home_name} vs {away_name}… (może potrwać ~30 sek.)"):
            try:
                pred = fetch_prediction(home_id, away_id, home_name, away_name, api_key)
                st.markdown("---")
                render_result(pred, home_name, away_name)
            except Exception as e:
                st.error(f"❌ Błąd: {e}")
