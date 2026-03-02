"""
app.py - Predykcja meczu (TheSportsDB - darmowe, bez klucza, Ekstraklasa!)
"""

import streamlit as st
from datetime import datetime

st.set_page_config(page_title="⚽ Predykcja Meczu", page_icon="⚽", layout="wide")

st.markdown("""
<style>
    .main { background-color: #0e1117; }
    h1 { color: #00d4aa !important; }
    .vs-card { background:linear-gradient(135deg,#1a1f2e,#16213e); border:1px solid #2d3561; border-radius:16px; padding:30px; margin:20px 0; box-shadow:0 8px 32px rgba(0,0,0,.4); }
    .team-name { font-size:1.6rem; font-weight:900; color:#fff; text-align:center; }
    .vs-text { font-size:2rem; font-weight:900; color:#fbbf24; text-align:center; padding-top:20px; }
    .prob-box { border-radius:12px; padding:20px 10px; text-align:center; font-weight:700; }
    .prob-home { background:#1a472a; border:2px solid #4ade80; }
    .prob-draw { background:#3d2e00; border:2px solid #fbbf24; }
    .prob-away { background:#3d1515; border:2px solid #f87171; }
    .prob-number { font-size:2.5rem; }
    .best-score { background:#0f3460; color:#00d4aa; border-radius:10px; padding:10px 28px; font-size:2.2rem; font-weight:900; border:2px solid #00d4aa; display:inline-block; }
    .stat-row { background:#12172a; border-radius:8px; padding:10px 16px; margin:6px 0; display:flex; justify-content:space-between; font-size:.9rem; }
    #MainMenu{visibility:hidden;} footer{visibility:hidden;}
</style>
""", unsafe_allow_html=True)

from api import TheSportsDBClient, convert_event_to_fixture, LEAGUES

client = TheSportsDBClient()

@st.cache_data(ttl=3600, show_spinner=False)
def load_all_teams() -> list:
    c = TheSportsDBClient()
    return c.get_all_teams()

@st.cache_data(ttl=600, show_spinner=False)
def fetch_prediction(home_id: str, away_id: str, home_name: str, away_name: str) -> dict:
    from model import MatchPredictor, EloRating
    c = TheSportsDBClient()

    home_raw = c.get_team_last_matches(home_id, limit=20)
    away_raw = c.get_team_last_matches(away_id, limit=20)
    h2h_raw  = c.get_h2h(home_id, away_id, limit=10)

    home_fix = [convert_event_to_fixture(m, home_id) for m in home_raw]
    away_fix = [convert_event_to_fixture(m, away_id) for m in away_raw]
    h2h_fix  = [convert_event_to_fixture(m, home_id) for m in h2h_raw]

    home_stats = c.get_team_statistics(home_id)
    away_stats = c.get_team_statistics(away_id)

    elo = EloRating()
    elo.build_from_fixtures(home_fix + away_fix + h2h_fix)
    predictor = MatchPredictor(elo_ratings=elo)

    # Konwertuj ID do int dla modelu
    try: hid_int = int(home_id)
    except: hid_int = 0
    try: aid_int = int(away_id)
    except: aid_int = 0

    fixture = {
        "fixture": {"id":0, "date": datetime.now().strftime("%Y-%m-%d")},
        "teams": {"home":{"id":hid_int,"name":home_name}, "away":{"id":aid_int,"name":away_name}},
        "league": {"id":0,"name":"","country":""},
        "goals": {"home":None,"away":None},
    }
    pred = predictor.predict_match(
        fixture=fixture,
        home_team_fixtures=home_fix, away_team_fixtures=away_fix,
        home_stats=home_stats, away_stats=away_stats,
        home_injuries=[], away_injuries=[], h2h_fixtures=h2h_fix,
    )
    pred["h2h_summary"]       = _summarize_h2h(h2h_raw, home_id, away_id, home_name, away_name)
    pred["home_form_details"] = _form_details(home_raw, home_id)
    pred["away_form_details"] = _form_details(away_raw, away_id)
    return pred


def _summarize_h2h(matches, home_id, away_id, home_name, away_name):
    hw=dr=aw=0; last=[]
    for m in sorted(matches, key=lambda x: x.get("dateEvent",""), reverse=True)[:10]:
        try: gh=int(m.get("intHomeScore") or -1); ga=int(m.get("intAwayScore") or -1)
        except: continue
        if gh<0 or ga<0: continue
        is_home = (m.get("idHomeTeam","") == home_id)
        sc=gh if is_home else ga; co=ga if is_home else gh
        date=m.get("dateEvent","")[:10]
        if sc>co: hw+=1; last.append(f"{date}  ✅ {home_name} {sc}:{co} {away_name}")
        elif sc==co: dr+=1; last.append(f"{date}  🟡 {home_name} {sc}:{co} {away_name}")
        else: aw+=1; last.append(f"{date}  ❌ {home_name} {sc}:{co} {away_name}")
    return {"home_wins":hw,"draws":dr,"away_wins":aw,"last_results":last}


def _form_details(matches, team_id):
    results=[]
    for m in sorted(matches, key=lambda x: x.get("dateEvent",""), reverse=True):
        if m.get("strStatus") != "Match Finished": continue
        try: gh=int(m.get("intHomeScore") or -1); ga=int(m.get("intAwayScore") or -1)
        except: continue
        if gh<0 or ga<0: continue
        is_home=(m.get("idHomeTeam","") == team_id)
        sc=gh if is_home else ga; co=ga if is_home else gh
        opp=m.get("strAwayTeam" if is_home else "strHomeTeam","?")
        date=m.get("dateEvent","")[:10]
        e="🟢" if sc>co else ("🟡" if sc==co else "🔴")
        results.append(f"{e} {date}  {'vs' if is_home else '@'} {opp}  **{sc}:{co}**")
        if len(results)>=5: break
    return results


def render_result(pred, home_name, away_name):
    p1=pred.get("prob_home_win",0); px=pred.get("prob_draw",0); p2=pred.get("prob_away_win",0)
    score=pred.get("most_likely_score","?:?"); sp=pred.get("most_likely_score_prob",0)
    prediction=pred.get("prediction","?"); confidence=pred.get("confidence","?")
    exp_h=pred.get("expected_goals_home",0); exp_a=pred.get("expected_goals_away",0)
    elo_h=pred.get("home_elo",0); elo_a=pred.get("away_elo",0)

    st.markdown('<div class="vs-card">', unsafe_allow_html=True)
    c1,c2,c3=st.columns([5,2,5])
    with c1: st.markdown(f'<div class="team-name">🏠 {home_name}</div>', unsafe_allow_html=True)
    with c2: st.markdown('<div class="vs-text">VS</div>', unsafe_allow_html=True)
    with c3: st.markdown(f'<div class="team-name">✈️ {away_name}</div>', unsafe_allow_html=True)
    st.markdown("<br>", unsafe_allow_html=True)

    c1,c2,c3=st.columns(3)
    with c1: st.markdown(f'<div class="prob-box prob-home"><div class="prob-number" style="color:#4ade80">{p1:.0f}%</div><div style="color:#4ade80;font-weight:700">1</div><div style="color:#8892b0;font-size:.8rem">Wygrana {home_name[:15]}</div></div>', unsafe_allow_html=True)
    with c2: st.markdown(f'<div class="prob-box prob-draw"><div class="prob-number" style="color:#fbbf24">{px:.0f}%</div><div style="color:#fbbf24;font-weight:700">X</div><div style="color:#8892b0;font-size:.8rem">Remis</div></div>', unsafe_allow_html=True)
    with c3: st.markdown(f'<div class="prob-box prob-away"><div class="prob-number" style="color:#f87171">{p2:.0f}%</div><div style="color:#f87171;font-weight:700">2</div><div style="color:#8892b0;font-size:.8rem">Wygrana {away_name[:15]}</div></div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    c1,c2=st.columns(2)
    with c1:
        st.markdown("#### 🎯 Przewidywany wynik")
        st.markdown(f'<div style="text-align:center;margin:10px 0"><span class="best-score">{score}</span></div>', unsafe_allow_html=True)
        st.markdown(f'<div style="text-align:center;color:#8892b0">prawdopodobieństwo: {sp:.1f}%</div>', unsafe_allow_html=True)
        st.markdown("<br>"); st.markdown("#### 📊 Top 5 wyników")
        for s in pred.get("top_scores",[]): st.markdown(f"- {s}")
    with c2:
        st.markdown("#### ⚡ Statystyki modelu")
        st.markdown(f'<div class="stat-row"><span>Typ</span><span><b>{prediction}</b></span></div><div class="stat-row"><span>Pewność</span><span><b>{confidence}</b></span></div><div class="stat-row"><span>Oczek. gole</span><span><b>{exp_h:.2f} – {exp_a:.2f}</b></span></div><div class="stat-row"><span>ELO Gosp.</span><span><b>{elo_h}</b></span></div><div class="stat-row"><span>ELO Gość</span><span><b>{elo_a}</b></span></div><div class="stat-row"><span>Forma gosp.</span><span><b>{pred.get("home_form_score",0):.2f}</b></span></div><div class="stat-row"><span>Forma gość</span><span><b>{pred.get("away_form_score",0):.2f}</b></span></div>', unsafe_allow_html=True)

    h2h=pred.get("h2h_summary",{})
    if h2h.get("home_wins",0)+h2h.get("draws",0)+h2h.get("away_wins",0)>0:
        st.markdown("<br>"); st.markdown("#### 🔁 Historia H2H")
        hc1,hc2,hc3=st.columns(3)
        hc1.metric(f"✅ {home_name[:12]}", h2h["home_wins"])
        hc2.metric("🟡 Remisy", h2h["draws"])
        hc3.metric(f"✅ {away_name[:12]}", h2h["away_wins"])
        with st.expander("Pokaż mecze H2H"):
            for r in h2h.get("last_results",[]): st.markdown(r)

    hf=pred.get("home_form_details",[]); af=pred.get("away_form_details",[])
    if hf or af:
        st.markdown("<br>")
        fc1,fc2=st.columns(2)
        with fc1:
            st.markdown(f"#### 📈 Forma {home_name}")
            for r in hf: st.markdown(r)
        with fc2:
            st.markdown(f"#### 📈 Forma {away_name}")
            for r in af: st.markdown(r)


# ── SIDEBAR ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚽ Predykcja Meczu")
    st.markdown("---")
    st.markdown("**✅ Dostępne ligi:**")
    for name in LEAGUES.keys():
        st.markdown(f"- {name}")
    st.markdown("---")
    st.markdown("*Wpisz min. 3 litery, działa po polsku i angielsku*")

# ── GŁÓWNA STRONA ─────────────────────────────────────────────────────────────
st.markdown("# ⚽ Predykcja Meczu Piłkarskiego")
st.markdown("Wybierz dwie drużyny i sprawdź kto wygra — z H2H, formą i rankingiem ELO.")
st.markdown("---")

with st.spinner("Ładowanie drużyn ze wszystkich lig… (tylko raz)"):
    all_teams = load_all_teams()

if not all_teams:
    st.error("Nie udało się pobrać listy drużyn.")
    st.stop()

st.success(f"✅ Załadowano {len(all_teams)} drużyn z {len(LEAGUES)} lig (w tym Ekstraklasa!)")
st.markdown("### 🔍 Wybierz drużyny")

col1, col2 = st.columns(2)
home_team = away_team = None

with col1:
    st.markdown("**🏠 Gospodarz**")
    home_search = st.text_input("Wyszukaj gospodarz", placeholder="np. Legia, Barcelona, Liverpool…", key="hs")
    if home_search and len(home_search) >= 3:
        results = client.search_teams_local(home_search, all_teams)
        if results:
            opts = {f"{t.get('strTeam','?')} — {t.get('_competition','')}": t for t in results[:15]}
            choice = st.selectbox("Wybierz", list(opts.keys()), key="hc")
            home_team = opts[choice]
            st.success(f"✅ {home_team.get('strTeam','?')}")
        else:
            st.warning("Nie znaleziono. Spróbuj inaczej.")

with col2:
    st.markdown("**✈️ Gość**")
    away_search = st.text_input("Wyszukaj gość", placeholder="np. Wisła, Real Madrid, Bayern…", key="as")
    if away_search and len(away_search) >= 3:
        results = client.search_teams_local(away_search, all_teams)
        if results:
            opts = {f"{t.get('strTeam','?')} — {t.get('_competition','')}": t for t in results[:15]}
            choice = st.selectbox("Wybierz", list(opts.keys()), key="ac")
            away_team = opts[choice]
            st.success(f"✅ {away_team.get('strTeam','?')}")
        else:
            st.warning("Nie znaleziono. Spróbuj inaczej.")

st.markdown("<br>", unsafe_allow_html=True)
predict_btn = st.button("🔮 Oblicz predykcję", use_container_width=True, type="primary",
                         disabled=(home_team is None or away_team is None))

if predict_btn and home_team and away_team:
    home_id   = home_team.get("idTeam","")
    away_id   = away_team.get("idTeam","")
    home_name = home_team.get("strTeam","?")
    away_name = away_team.get("strTeam","?")
    if home_id == away_id:
        st.error("Wybierz dwie różne drużyny!")
    else:
        with st.spinner(f"Analizuję {home_name} vs {away_name}…"):
            try:
                pred = fetch_prediction(home_id, away_id, home_name, away_name)
                st.markdown("---")
                render_result(pred, home_name, away_name)
            except Exception as e:
                st.error(f"❌ Błąd: {e}")
