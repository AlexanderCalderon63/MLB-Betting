"""
theme.py — Light/dark theme engine for all pages.

Usage in each page:
    from theme import init_theme, palette
    init_theme()              # renders sidebar toggle + injects CSS
    c = palette()             # color dict for Plotly charts
"""

import streamlit as st

# ── Color palettes ─────────────────────────────────────────────────────────────

LIGHT = {
    "bg":           "#f8faff",
    "surface":      "#ffffff",
    "surface2":     "#eef1fc",
    "border":       "#dde4f5",
    "border2":      "#c4cfec",
    "text":         "#0d1433",
    "text2":        "#3d4d80",
    "muted":        "#8896b8",
    "accent":       "#2b5af8",
    "green":        "#059669",
    "green_dim":    "rgba(5,150,105,0.1)",
    "red":          "#dc2626",
    "red_dim":      "rgba(220,38,38,0.1)",
    "amber":        "#d97706",
    # Plotly
    "plot_bg":      "#f8faff",
    "plot_paper":   "#ffffff",
    "plot_grid":    "rgba(0,0,80,0.06)",
    "plot_font":    "#0d1433",
    "plot_green":   "#059669",
    "plot_red":     "#dc2626",
    "plot_blue":    "#2b5af8",
    "plot_amber":   "#d97706",
}

DARK = {
    "bg":           "#07080f",
    "surface":      "#0d0f1b",
    "surface2":     "#13162a",
    "border":       "#1e2237",
    "border2":      "#263045",
    "text":         "#edf2ff",
    "text2":        "#8899bb",
    "muted":        "#4a5572",
    "accent":       "#4f7fff",
    "green":        "#22d47a",
    "green_dim":    "rgba(34,212,122,0.1)",
    "red":          "#f05252",
    "red_dim":      "rgba(240,82,82,0.1)",
    "amber":        "#fbbf24",
    # Plotly
    "plot_bg":      "#07080f",
    "plot_paper":   "#07080f",
    "plot_grid":    "rgba(255,255,255,0.05)",
    "plot_font":    "#edf2ff",
    "plot_green":   "#22d47a",
    "plot_red":     "#f05252",
    "plot_blue":    "#4f7fff",
    "plot_amber":   "#fbbf24",
}


def is_dark() -> bool:
    return bool(st.session_state.get("_dark_mode", False))


def palette() -> dict:
    return DARK if is_dark() else LIGHT


def init_theme():
    """Render sidebar toggle and inject page CSS. Call at the top of every page."""
    if "_dark_mode" not in st.session_state:
        st.session_state["_dark_mode"] = False
    with st.sidebar:
        # No key= — we manage _dark_mode ourselves so Streamlit never clears it on page nav
        new_val = st.toggle("Dark mode", value=st.session_state["_dark_mode"])
        st.session_state["_dark_mode"] = new_val
        st.divider()

    st.markdown(_build_css(), unsafe_allow_html=True)


# ── CSS builder ────────────────────────────────────────────────────────────────

_FONTS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Syne:wght@700;800&family=Space+Mono:wght@400;700&display=swap');
*, *::before, *::after { box-sizing: border-box; }
html, body, [data-testid="stApp"] { font-family: 'Inter', sans-serif !important; }
h1, h2, h3, h4 { font-family: 'Syne', sans-serif !important; }
"""

def _build_css() -> str:
    dark = is_dark()
    c = DARK if dark else LIGHT
    return f"<style>{_FONTS}{_st_overrides(c, dark)}{_custom_css(c)}</style>"


def _st_overrides(c: dict, dark: bool) -> str:
    """Override Streamlit's native widget colors."""
    tmpl = "plotly_dark" if dark else "plotly"
    return f"""
/* ── App shell ── */
[data-testid="stApp"] {{ background-color: {c['bg']} !important; }}
section[data-testid="stSidebar"] {{
    background-color: {c['surface']} !important;
    border-right: 1px solid {c['border']} !important;
}}
[data-testid="stHeader"] {{ background-color: {c['bg']} !important; border-bottom: 1px solid {c['border']}; }}
.main .block-container {{ background-color: {c['bg']} !important; }}

/* ── Text ── */
p, span, div, li, td, th, label, caption, small,
.stMarkdown, .stMarkdown p, .stText,
[data-testid="stCaptionContainer"] p,
[data-testid="stWidgetLabel"] p {{ color: {c['text']} !important; }}
h1, h2, h3, h4, h5, h6 {{ color: {c['text']} !important; }}
[data-testid="stSidebar"] * {{ color: {c['text']} !important; }}
[data-testid="stCaptionContainer"] {{ color: {c['muted']} !important; }}
[data-testid="stCaptionContainer"] p {{ color: {c['muted']} !important; }}

/* ── Metrics ── */
[data-testid="metric-container"] {{
    background: {c['surface']} !important;
    border: 1px solid {c['border']} !important;
    border-radius: 10px !important;
    padding: 0.9rem 1rem !important;
}}
[data-testid="stMetricLabel"] p {{ color: {c['muted']} !important; }}
[data-testid="stMetricValue"] {{ color: {c['text']} !important; }}

/* ── Buttons ── */
.stButton > button {{
    background: {c['surface']} !important;
    border: 1px solid {c['border2']} !important;
    color: {c['text']} !important;
    border-radius: 8px !important;
    font-family: 'Inter', sans-serif !important;
}}
.stButton > button:hover {{
    border-color: {c['accent']} !important;
    color: {c['accent']} !important;
}}
.stButton > button[kind="primary"] {{
    background: {c['accent']} !important;
    border-color: {c['accent']} !important;
    color: #ffffff !important;
}}

/* ── Inputs ── */
.stTextInput > div > div > input,
.stNumberInput > div > div > input,
.stDateInput > div > div > input {{
    background: {c['surface']} !important;
    border-color: {c['border2']} !important;
    color: {c['text']} !important;
    border-radius: 8px !important;
}}
.stSelectbox > div > div,
.stSelectbox > div > div > div {{
    background: {c['surface']} !important;
    color: {c['text']} !important;
    border-color: {c['border2']} !important;
}}

/* ── Expanders ── */
[data-testid="stExpander"] {{
    background: {c['surface']} !important;
    border: 1px solid {c['border']} !important;
    border-radius: 10px !important;
}}
[data-testid="stExpander"] summary {{ color: {c['text']} !important; }}

/* ── Forms ── */
[data-testid="stForm"] {{
    background: {c['surface2']} !important;
    border: 1px solid {c['border']} !important;
    border-radius: 12px !important;
    padding: 1rem !important;
}}

/* ── Toggle ── */
[data-testid="stToggle"] p {{ color: {c['text2']} !important; }}

/* ── Divider ── */
hr {{ border-color: {c['border']} !important; opacity: 1 !important; }}

/* ── Alerts ── */
[data-testid="stAlert"] {{
    background: {c['surface']} !important;
    border-radius: 10px !important;
    border-color: {c['border']} !important;
}}

/* ── Tabs ── */
[data-testid="stTabs"] [data-baseweb="tab-list"] {{ background: {c['surface2']} !important; border-radius: 8px; }}
[data-testid="stTabs"] button {{ color: {c['text2']} !important; }}
[data-testid="stTabs"] button[aria-selected="true"] {{ color: {c['accent']} !important; }}

/* ── Dataframe ── */
[data-testid="stDataFrame"] > div {{ border: 1px solid {c['border']} !important; border-radius: 8px; }}
"""


def _custom_css(c: dict) -> str:
    """Styles for our custom HTML components (cards, badges, box scores, etc.)."""
    return f"""
/* ── Hero (app.py) ── */
.hero {{
    background: {c['surface']} !important;
    border: 1px solid {c['border']} !important;
    border-radius: 16px;
    padding: 2.8rem 3rem;
    margin-bottom: 2rem;
    position: relative;
    overflow: hidden;
}}
.hero::after {{
    content: '⚾';
    position: absolute;
    right: 2.5rem;
    top: 50%;
    transform: translateY(-50%);
    font-size: 7rem;
    opacity: 0.04;
    pointer-events: none;
}}
.hero-eyebrow {{
    font-family: 'Space Mono', monospace;
    font-size: 0.7rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {c['accent']};
    margin-bottom: 0.8rem;
}}
.hero h1 {{
    font-family: 'Syne', sans-serif;
    font-size: 2.6rem;
    font-weight: 800;
    color: {c['text']} !important;
    margin: 0 0 0.7rem 0;
    line-height: 1.1;
}}
.hero-sub {{
    color: {c['muted']};
    font-size: 0.95rem;
    margin: 0;
    line-height: 1.5;
}}

/* ── Info grid (app.py) ── */
.info-grid {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1rem;
    margin: 1.5rem 0;
}}
.info-row {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 12px;
    padding: 1.1rem 1.3rem;
    display: flex;
    gap: 0.9rem;
    align-items: flex-start;
}}
.info-row .dot {{ width: 8px; height: 8px; border-radius: 50%; background: {c['accent']}; margin-top: 6px; flex-shrink: 0; }}
.info-row .label {{ font-family: 'Syne', sans-serif; font-size: 0.9rem; font-weight: 700; color: {c['text']}; margin-bottom: 3px; }}
.info-row .desc {{ font-size: 0.8rem; color: {c['muted']}; line-height: 1.45; margin: 0; }}

/* ── Setup box (app.py) ── */
.setup-box {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 12px;
    padding: 1.2rem 1.5rem;
    font-size: 0.9rem;
    color: {c['text2']};
    line-height: 1.7;
}}
.setup-box a {{ color: {c['accent']}; text-decoration: none; }}
.setup-box code {{
    background: {c['surface2']};
    padding: 1px 6px;
    border-radius: 4px;
    font-family: 'Space Mono', monospace;
    font-size: 0.8rem;
    color: {c['green']};
}}

/* ── Today's Games ── */
.game-block {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 14px;
    padding: 1.2rem 1.5rem;
    margin-bottom: 1.2rem;
}}
.pitcher-box {{
    background: {c['surface2']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    padding: 0.8rem 1rem;
    margin: 0.5rem 0 0.8rem 0;
    font-size: 0.85rem;
}}
.pitcher-stat {{ color: {c['muted']}; font-size: 0.78rem; }}
.pitcher-name {{ color: {c['green']}; font-weight: 700; }}
.trend-better {{ color: {c['green']}; }}
.trend-worse  {{ color: {c['red']}; }}
.trend-flat   {{ color: {c['muted']}; }}
.book-badge {{
    display: inline-block;
    padding: 2px 10px;
    border-radius: 20px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    font-family: 'Space Mono', monospace;
}}
.badge-caesars    {{ background: {c['green_dim']}; color: {c['green']}; border: 1px solid {c['green']}33; }}
.badge-betmgm     {{ background: {c['red_dim']}; color: {c['red']}; border: 1px solid {c['red']}33; }}
.badge-default    {{ background: {c['surface2']}; color: {c['muted']}; border: 1px solid {c['border']}; }}
.value-hot   {{ color: {c['amber']}; font-weight: 700; }}
.value-yes   {{ color: {c['green']}; font-weight: 700; }}
.value-edge  {{ color: {c['accent']}; }}
.value-no    {{ color: {c['muted']}; }}
.value-avoid {{ color: {c['red']}; }}

/* ── Bet Sizing ── */
.page-header {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 14px;
    padding: 1.5rem 2rem;
    margin-bottom: 1.5rem;
}}
.page-header h2 {{ margin: 0 0 0.3rem 0; color: {c['text']} !important; font-family: 'Syne', sans-serif; font-size: 1.6rem; font-weight: 800; }}
.page-header p  {{ margin: 0; color: {c['muted']}; font-size: 0.9rem; }}
.rec-badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 12px;
    font-size: 0.7rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-family: 'Space Mono', monospace;
}}
.rec-hot   {{ background: rgba(217,119,6,0.12); color: {c['amber']}; border: 1px solid {c['amber']}44; }}
.rec-value {{ background: {c['green_dim']}; color: {c['green']}; border: 1px solid {c['green']}33; }}
.rec-edge  {{ background: rgba(43,90,248,0.08); color: {c['accent']}; border: 1px solid {c['accent']}33; }}
.rec-none  {{ background: {c['surface2']}; color: {c['muted']}; border: 1px solid {c['border']}; }}
.payout-row {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    padding: 0.9rem 1.2rem;
    margin-bottom: 0.5rem;
}}
.total-bar {{
    background: {c['surface2']};
    border: 1px solid {c['border2']};
    border-radius: 12px;
    padding: 1.2rem 1.5rem;
    margin-top: 1rem;
}}
.stat-box {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 10px;
    padding: 1rem;
    text-align: center;
}}

/* ── Live Scores ── */
.status-live    {{ color: {c['red']}; font-weight: 700; font-family: 'Space Mono', monospace; font-size: 0.85rem; }}
.status-final   {{ color: {c['green']}; font-weight: 700; font-family: 'Space Mono', monospace; font-size: 0.85rem; }}
.status-pre     {{ color: {c['accent']}; font-weight: 700; font-family: 'Space Mono', monospace; font-size: 0.85rem; }}
.status-delayed {{ color: {c['amber']}; font-weight: 700; font-family: 'Space Mono', monospace; font-size: 0.85rem; }}
.bet-pill {{
    display: inline-block;
    padding: 2px 12px;
    border-radius: 20px;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    margin-left: 8px;
    vertical-align: middle;
    font-family: 'Space Mono', monospace;
}}
.bet-active {{ background: {c['green_dim']}; color: {c['green']}; border: 1px solid {c['green']}33; }}
.bet-win    {{ background: {c['green_dim']}; color: {c['green']}; border: 1px solid {c['green']}33; }}
.bet-loss   {{ background: {c['red_dim']}; color: {c['red']}; border: 1px solid {c['red']}33; }}
.bet-push   {{ background: {c['surface2']}; color: {c['muted']}; border: 1px solid {c['border']}; }}
.boxscore-table {{
    width: 100%;
    border-collapse: collapse;
    font-family: 'Space Mono', monospace;
    font-size: 0.8rem;
    margin-top: 0.7rem;
}}
.boxscore-table th {{
    background: {c['surface2']};
    color: {c['muted']};
    padding: 5px 10px;
    text-align: center;
    border-bottom: 1px solid {c['border']};
    font-weight: 700;
    font-size: 0.68rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
}}
.boxscore-table th:first-child {{ text-align: left; min-width: 150px; }}
.boxscore-table td {{
    padding: 7px 10px;
    text-align: center;
    border-bottom: 1px solid {c['border']};
    color: {c['text']};
}}
.boxscore-table td:first-child {{ text-align: left; font-weight: 700; color: {c['text']}; }}
.boxscore-table tr:last-child td {{ border-bottom: none; }}
.boxscore-table .sep {{ border-left: 1px solid {c['border']}; }}
.boxscore-table .totals {{ font-weight: 700; color: {c['text']}; }}
.boxscore-table .current-inn {{ color: {c['amber']}; }}
.boxscore-table .dim {{ color: {c['border2']}; }}
.pitcher-line {{ font-size: 0.78rem; color: {c['muted']}; margin-top: 8px; }}
.pitcher-line strong {{ color: {c['text2']}; }}

/* ── Context badges (park factor + weather) ── */
.context-row {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 8px;
    margin-bottom: 2px;
}}
.ctx-badge {{
    display: inline-flex;
    align-items: center;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 0.72rem;
    font-weight: 600;
    font-family: 'Inter', sans-serif;
    letter-spacing: 0.02em;
}}
.park-extreme   {{ background: rgba(217,119,6,0.12);   color: {c['amber']};  border: 1px solid {c['amber']}44; }}
.park-hitter    {{ background: {c['green_dim']};        color: {c['green']};  border: 1px solid {c['green']}33; }}
.park-pitcher   {{ background: rgba(43,90,248,0.08);   color: {c['accent']}; border: 1px solid {c['accent']}33; }}
.wx-cold        {{ background: rgba(96,165,250,0.1);   color: #60a5fa;       border: 1px solid rgba(96,165,250,0.3); }}
.wx-hot         {{ background: rgba(251,146,60,0.1);   color: #fb923c;       border: 1px solid rgba(251,146,60,0.3); }}
.wx-wind        {{ background: {c['surface2']};         color: {c['muted']};  border: 1px solid {c['border']}; }}
.wx-wind-strong {{ background: rgba(251,191,36,0.12);  color: {c['amber']};  border: 1px solid {c['amber']}44; }}
"""
