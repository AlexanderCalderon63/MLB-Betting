"""
pages/4_Help.py — Tutorial and reference guide for the MLB Betting app
"""

import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import streamlit as st
from database import init_db
from theme import init_theme, palette

init_db()

st.set_page_config(page_title="Help & Tutorial", page_icon="📖", layout="wide")
init_theme("#6366f1")   # violet — help/docs
c = palette()


# ── Helper components ──────────────────────────────────────────────────────────

def render_steps(steps: list[dict], title: str = "") -> None:
    items = ""
    for s in steps:
        tip_html = ""
        if s.get("tip"):
            tip_html = (
                f'<div style="margin-top:8px; padding:8px 12px; border-radius:10px; '
                f'background:{c["surface2"]}; border:1px solid {c["border"]}; '
                f'font-size:0.8rem; color:{c["muted"]};">'
                f'💡 {s["tip"]}</div>'
            )
        items += f"""
<div style="display:flex; gap:1rem; align-items:flex-start; margin-bottom:1.1rem;">
  <div style="flex:0 0 auto; width:2rem; height:2rem; border-radius:50%;
              background:{c['accent_dim']}; border:1px solid {c['accent']}33;
              display:flex; align-items:center; justify-content:center;
              font-family:'Space Mono',monospace; font-size:0.78rem;
              font-weight:700; color:{c['accent']};">{s['step']}</div>
  <div style="flex:1;">
    <div style="font-family:'Manrope',sans-serif; font-weight:700;
                color:{c['text']}; font-size:0.95rem; margin-bottom:4px;">{s['title']}</div>
    <div style="font-size:0.88rem; color:{c['text2']}; line-height:1.6;">{s['body']}</div>
    {tip_html}
  </div>
</div>"""
    header = (
        f'<div style="font-family:\'Manrope\',sans-serif; font-weight:800; '
        f'font-size:1.05rem; color:{c["text"]}; margin-bottom:1rem;">{title}</div>'
        if title else ""
    )
    st.markdown(
        f'<div style="padding:1.2rem 1.4rem; background:{c["surface"]}; '
        f'border:1px solid {c["border"]}; border-radius:16px; box-shadow:{c["shadow"]};">'
        + header + items + "</div>",
        unsafe_allow_html=True,
    )


def render_callout(term: str, definition: str, example: str = "") -> None:
    ex = (
        f'<div style="margin-top:6px; font-family:\'Space Mono\',monospace; '
        f'font-size:0.78rem; color:{c["muted"]};">{example}</div>'
        if example else ""
    )
    st.markdown(
        f'<div style="padding:0.9rem 1.1rem; background:{c["surface2"]}; '
        f'border-left:3px solid {c["accent"]}; border-radius:0 12px 12px 0; margin:0.5rem 0;">'
        f'<span style="font-family:\'Manrope\',sans-serif; font-weight:700; color:{c["text"]};">{term}</span>'
        f'<div style="font-size:0.88rem; color:{c["text2"]}; margin-top:4px; line-height:1.55;">{definition}</div>'
        + ex + "</div>",
        unsafe_allow_html=True,
    )


def render_feature_grid(features: list[dict]) -> None:
    cards = ""
    for f in features:
        page_link = (
            f'<div style="margin-top:8px; font-family:\'Space Mono\',monospace; '
            f'font-size:0.68rem; color:{c["accent"]};">{f["page"]}</div>'
            if f.get("page") else ""
        )
        cards += f"""
<div style="background:{c['surface']}; border:1px solid {c['border']}; border-radius:16px;
            padding:1.2rem 1.3rem; box-shadow:{c['shadow']};">
  <div style="font-size:1.6rem; margin-bottom:0.5rem;">{f['icon']}</div>
  <div style="font-family:'Manrope',sans-serif; font-weight:800; font-size:1rem; color:{c['text']}; margin-bottom:4px;">{f['name']}</div>
  <div style="font-size:0.83rem; color:{c['muted']}; line-height:1.5;">{f['desc']}</div>
  {page_link}
</div>"""
    st.markdown(
        f'<div style="display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:1rem; margin:1rem 0;">'
        + cards + "</div>",
        unsafe_allow_html=True,
    )


# ── Page header ────────────────────────────────────────────────────────────────

st.title("📖 Help & Tutorial")
st.caption("Everything you need to get the most out of the MLB Betting app")

st.divider()


# ── Section 1: What this app does ─────────────────────────────────────────────

st.markdown(
    f'<div style="padding:1.4rem 1.6rem; background:{c["surface"]}; border:1px solid {c["border"]}; '
    f'border-radius:16px; box-shadow:{c["shadow"]}; margin-bottom:1.5rem;">'
    f'<div style="font-family:\'Manrope\',sans-serif; font-weight:800; font-size:1.25rem; '
    f'color:{c["text"]}; margin-bottom:0.6rem;">What this app does</div>'
    f'<div style="font-size:0.93rem; color:{c["text2"]}; line-height:1.7;">'
    f'Every morning the app pulls live Caesars moneylines and compares them against a machine-learning model '
    f'trained on team stats and starting pitcher data. When the model thinks a team is <b>at least 4 percentage '
    f'points more likely to win</b> than the odds imply, it flags that game as a value bet. '
    f'You log your bets, resolve outcomes after the game, and the results feed back into the model '
    f'so it keeps improving over time.'
    f'</div>'
    f'</div>',
    unsafe_allow_html=True,
)


# ── Section 2: Quick start ─────────────────────────────────────────────────────

st.subheader("Quick start")

render_steps(
    [
        {
            "step": 1,
            "title": "Open Today's Games and refresh odds",
            "body": "Navigate to <b>Today's Games</b> in the sidebar. Click <b>Refresh Odds</b> to pull the latest Caesars moneylines. This also loads team stats and any probable pitchers already announced.",
            "tip": "Do this after 10 AM ET — lines are usually posted by then and probable pitchers start appearing.",
        },
        {
            "step": 2,
            "title": "Read the signal badges",
            "body": "Each game card shows an edge badge. <b>🔥 Strong Value</b> and <b>✅ Value Bet</b> mean the model sees a meaningful gap between its win estimate and what the market is offering. <b>➖ No Edge</b> means skip it.",
            "tip": "Add probable pitchers in the pitcher panel to sharpen the model's estimate before reading signals.",
        },
        {
            "step": 3,
            "title": "Stage a bet on the Bet Slip",
            "body": "Click <b>+ Add Real Bet</b> in a game card to add it to the sidebar Bet Slip. Use <b>+ Add Paper Bet</b> if you want to track it hypothetically without real money. You can add multiple games.",
        },
        {
            "step": 4,
            "title": "Log your bets",
            "body": "Open the Bet Slip in the sidebar. Set your stake for each bet, then click <b>Log Bets</b>. Your bets are saved to the Bet Tracker (or Paper Bet Tracker) with today's odds.",
        },
        {
            "step": 5,
            "title": "Resolve outcomes and retrain",
            "body": "After games finish, go to <b>Bet Tracker</b> and resolve your bets. Once you have a few months of resolved data, visit <b>Model Performance</b> and click <b>Fetch 2026 Games &amp; Retrain</b> to improve the model.",
            "tip": "Paper bets also feed model training — log them even if you're not betting real money yet.",
        },
    ],
    title="Your daily workflow — 5 steps",
)

st.divider()


# ── Section 3: Page-by-page guide ─────────────────────────────────────────────

st.subheader("Pages at a glance")

render_feature_grid([
    {
        "icon": "🏠",
        "name": "Dashboard",
        "desc": "Your 30-day P&L summary and today's top value bets at a glance. Start here each morning.",
        "page": "app.py (home)",
    },
    {
        "icon": "⚾",
        "name": "Today's Games",
        "desc": "Full odds table with model probabilities, pitcher context, park factors, and the Bet Slip. Main daily workflow.",
        "page": "1_Todays_Games",
    },
    {
        "icon": "📊",
        "name": "Stats Explorer",
        "desc": "Team standings, Pythagorean win%, run differential, and home/away splits. Use for background research on a matchup.",
        "page": "2_Stats_Explorer",
    },
    {
        "icon": "📒",
        "name": "Bet Tracker",
        "desc": "Log real bets, resolve outcomes after games, and view your P&L chart, ROI, and Closing Line Value.",
        "page": "3_Bet_Tracker",
    },
    {
        "icon": "🧠",
        "name": "Model Performance",
        "desc": "Calibration curve, tier win rates, edge vs. outcome scatter, and the retrain button. Check monthly.",
        "page": "5_Model_Performance",
    },
    {
        "icon": "📺",
        "name": "Live Scores",
        "desc": "Inning-by-inning box scores for today's games. Refresh during games to follow along.",
        "page": "6_Live_Scores",
    },
    {
        "icon": "📝",
        "name": "Paper Bet Tracker",
        "desc": "Track hypothetical bets risk-free. Outcomes feed the model's training data — useful even before you bet real money.",
        "page": "7_Paper_Bet_Tracker",
    },
    {
        "icon": "🎰",
        "name": "Parlay Builder",
        "desc": "Stack today's value bets into a parlay or build one manually. Shows combined odds and estimated payout.",
        "page": "8_Parlay_Builder",
    },
    {
        "icon": "📈",
        "name": "Parlay Tracker",
        "desc": "Log parlay bets and track outcomes with a P&L curve over time.",
        "page": "9_Parlay_Tracker",
    },
])

st.divider()


# ── Section 4: Glossary ────────────────────────────────────────────────────────

st.subheader("Glossary")

render_callout(
    "Edge",
    "The gap between the model's estimated win probability and the market's implied probability. "
    "Positive edge means the model thinks the odds are undervaluing a team — that's where value bets come from.",
    "Example: model says 58% win chance, market implies 52% → edge = +6%",
)

render_callout(
    "Signal Tiers",
    "How the app categorizes edge size: "
    "<b>🔥 ≥8%</b> Strong Value · "
    "<b>✅ ≥4%</b> Value Bet · "
    "<b>⚠️ ≥1%</b> Slight Edge · "
    "<b>➖</b> No Edge · "
    "<b>❌ ≤−4%</b> Avoid. "
    "Only 🔥 and ✅ are worth betting — the others don't have enough cushion to overcome variance.",
)

render_callout(
    "Implied Probability",
    "What the sportsbook's odds are saying the team's win chance is, after removing the vig (the bookmaker's built-in cut). "
    "This is the number the model's estimate is compared against.",
    "Example: Caesars −150 on a team → implied probability ≈ 57.7% after vig removal",
)

render_callout(
    "Kelly Fraction",
    "A formula that tells you what percentage of your bankroll to risk, based on your edge and the odds. "
    "The app shows a ¼-Kelly recommendation — a conservative version that reduces variance. "
    "Never bet more than the full Kelly figure.",
    "Example: edge 6%, odds −120 → full Kelly ≈ 9% of bankroll, ¼-Kelly ≈ 2.3%",
)

render_callout(
    "CLV (Closing Line Value)",
    "How much better the odds you got were compared to where the line closed just before game time. "
    "Positive CLV means you beat the closing line — a strong long-term signal that your process is sound, "
    "regardless of whether that individual bet won or lost.",
    "Example: you bet +110, line closed at −105 → you had positive CLV",
)

render_callout(
    "Pythagorean Win%",
    "An expected win percentage calculated from runs scored and runs allowed (exponent 1.83), "
    "rather than actual wins. Over small samples it's a better predictor of true team quality than the real record.",
    "Example: a team 5-8 in close games might be 9-4 in Pythagorean terms — likely to improve",
)

render_callout(
    "Paper Bets",
    "Hypothetical bets tracked without real money. They're not just practice — their resolved outcomes "
    "are included in model training data, so logging paper bets actively improves future predictions.",
)

render_callout(
    "Bet Slip",
    "The staging cart in the sidebar on Today's Games. Add bets here from game cards, set your stake, "
    "and click Log Bets to save them. The slip clears after logging.",
)

st.divider()


# ── Section 5: Tips & gotchas ──────────────────────────────────────────────────

st.subheader("Tips & common mistakes")

with st.expander("Show tips", expanded=False):
    tips = [
        (
            "Add probable pitchers before reading signals",
            "The model defaults to season-level team stats only. On Today's Games, open the pitcher panel "
            "and enter the probable starters — ERA, WHIP, K/9 differences can swing the edge estimate by several points. "
            "Always do this before deciding whether a game is worth betting.",
        ),
        (
            "Closing Line Value matters more than outcomes",
            "A bet can win and have negative CLV (you got lucky). A bet can lose and have positive CLV "
            "(you made the right call but variance went against you). Long-term profitability tracks CLV, "
            "not win/loss streaks. Don't change your process after a short losing run if your CLV is positive.",
        ),
        (
            "Log paper bets even if you're not betting real money",
            "Paper bet outcomes feed model training just like real bets. If you're not ready to bet real money, "
            "logging paper bets for a month or two will improve the model before you start.",
        ),
        (
            "Retrain the model after you have 200+ resolved bets",
            "The model improves with more resolved data. Go to Model Performance → Fetch 2026 Games & Retrain "
            "once you have a solid base of outcomes. Don't retrain every week — wait for meaningful new data.",
        ),
        (
            "Refresh odds after lineup changes or weather delays",
            "Lines move when pitchers are scratched or weather affects the game. Re-open Today's Games and "
            "click Refresh Odds to pull updated lines and re-run the model before placing a bet.",
        ),
        (
            "The app only shows Caesars lines",
            "Caesars (williamhill_us) is the only legally available sportsbook in Puerto Rico shown in the app. "
            "All odds, CLV, and edge calculations are relative to Caesars lines.",
        ),
    ]
    for title_text, body_text in tips:
        st.markdown(
            f'<div style="padding:0.9rem 1.1rem; background:{c["surface"]}; '
            f'border:1px solid {c["border"]}; border-radius:12px; margin-bottom:0.75rem;">'
            f'<div style="font-family:\'Manrope\',sans-serif; font-weight:700; '
            f'color:{c["text"]}; margin-bottom:4px;">✦ {title_text}</div>'
            f'<div style="font-size:0.87rem; color:{c["text2"]}; line-height:1.6;">{body_text}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
