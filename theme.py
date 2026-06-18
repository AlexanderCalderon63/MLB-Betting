"""
theme.py — Light theme engine for all pages.

Design direction — "Clean Sheet": an airy, modern analytics product. White cards
float on a soft cool canvas with generous whitespace; big bold Manrope headlines,
eyebrow pills, fully-rounded pill buttons, and soft layered shadows. A vivid
blue→violet accent system carries interaction; semantic green/red/amber carry
meaning. Space Mono is reserved for the data ledger (odds, edges, P&L) — the one
place numbers line up like a box score.

Usage in each page:
    from theme import init_theme, palette
    init_theme()              # injects CSS
    c = palette()             # color dict for Plotly charts + inline HTML
    template = c["plotly_template"]   # always "plotly" (light)
"""

import streamlit as st

# ── Color palette (light only) ──────────────────────────────────────────────────
# Every page reads colors from palette(); never hardcode hex in pages.

LIGHT = {
    "bg":           "#f4f7fc",   # airy cool canvas
    "surface":      "#ffffff",   # cards
    "surface2":     "#eef3fb",   # subtle inset / section tint
    "border":       "#e7edf6",
    "border2":      "#d6e0ee",
    "text":         "#0f1b33",   # deep navy ink — headlines
    "text2":        "#475467",   # body secondary
    "muted":        "#64748b",   # captions / meta (≥4.5:1 on white)
    "accent":       "#3b62f6",   # vivid blue — primary
    "accent2":      "#7b5cff",   # violet — secondary pop
    "accent_dim":   "rgba(59,98,246,0.10)",
    "green":        "#0ea672",
    "green_dim":    "rgba(14,166,114,0.12)",
    "red":          "#e5484d",
    "red_dim":      "rgba(229,72,77,0.12)",
    "amber":        "#e0890b",
    "shadow":       "0 1px 2px rgba(15,27,51,0.04), 0 6px 20px rgba(15,27,51,0.06)",
    "shadow_lg":    "0 14px 44px rgba(15,27,51,0.12)",
    # Plotly
    "plotly_template": "plotly",
    "plot_bg":      "#ffffff",
    "plot_paper":   "#ffffff",
    "plot_grid":    "rgba(15,27,51,0.07)",
    "plot_font":    "#0f1b33",
    "plot_green":   "#0ea672",
    "plot_red":     "#e5484d",
    "plot_blue":    "#3b62f6",
    "plot_amber":   "#e0890b",
}


BRAND_ACCENT = "#3b62f6"   # default (dashboard / fallback)


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore


def _rgba(h: str, a: float) -> str:
    r, g, b = _hex_to_rgb(h)
    return f"rgba({r},{g},{b},{a})"


def _lighten(h: str, amt: float) -> str:
    r, g, b = _hex_to_rgb(h)
    r = int(r + (255 - r) * amt)
    g = int(g + (255 - g) * amt)
    b = int(b + (255 - b) * amt)
    return f"#{r:02x}{g:02x}{b:02x}"


def is_dark() -> bool:
    return False


def _resolved_palette(accent: str | None = None) -> dict:
    """LIGHT palette with the active page's accent swapped in."""
    c = dict(LIGHT)
    acc = accent or st.session_state.get("_page_accent")
    if acc:
        c["accent"]     = acc
        c["accent2"]    = _lighten(acc, 0.42)
        c["accent_dim"] = _rgba(acc, 0.10)
        c["plot_blue"]  = acc
    return c


def palette() -> dict:
    return _resolved_palette()


def plot_template() -> str:
    return LIGHT["plotly_template"]


def init_theme(accent: str | None = None):
    """Inject page CSS + the page's tinted decorative layer.

    Pass a hex `accent` to give the page its own colour identity; it tints the
    background glow, shapes, eyebrow pills, section ticks, and buttons.
    """
    if accent:
        st.session_state["_page_accent"] = accent
    c = _resolved_palette(accent)
    st.markdown(_build_css(c) + _decor_html(c), unsafe_allow_html=True)


def _decor_html(c: dict) -> str:
    """A fixed, behind-content layer of soft page-coloured shapes."""
    return f"""
<div class="bg-decor" aria-hidden="true" style="color:{c['accent']};">
  <svg class="decor-ball" viewBox="0 0 100 100" fill="none">
    <circle cx="50" cy="50" r="44" stroke="currentColor" stroke-width="4"/>
    <path d="M24 15 Q41 50 24 85" stroke="currentColor" stroke-width="3" stroke-dasharray="3 6" stroke-linecap="round"/>
    <path d="M76 15 Q59 50 76 85" stroke="currentColor" stroke-width="3" stroke-dasharray="3 6" stroke-linecap="round"/>
  </svg>
  <svg class="decor-plate" viewBox="0 0 100 100">
    <path d="M14 12 H86 V52 L50 90 L14 52 Z" fill="currentColor"/>
  </svg>
  <span class="decor-ring"></span>
  <span class="decor-dot d1"></span>
  <span class="decor-dot d2"></span>
</div>
"""


# ── CSS builder ────────────────────────────────────────────────────────────────

_FONTS = """
@import url('https://fonts.googleapis.com/css2?family=Manrope:wght@600;700;800&family=Inter:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap');
*, *::before, *::after { box-sizing: border-box; }
html, body, [data-testid="stApp"] { font-family: 'Inter', sans-serif !important; }
h1, h2, h3, h4 { font-family: 'Manrope', sans-serif !important; letter-spacing: -0.02em; }
/* Tabular figures wherever numbers matter — the ledger reads as a column. */
.stat-box, .boxscore-table, .slip-meta, .book-badge, .rec-badge, .hc-pnl, .hc-stat b,
[data-testid="stMetricValue"], [class*="status-"] { font-variant-numeric: tabular-nums; }
@keyframes heroIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation: none !important; }
}
"""


def _build_css(c: dict) -> str:
    return f"<style>{_FONTS}{_st_overrides(c)}{_custom_css(c)}</style>"


def _st_overrides(c: dict) -> str:
    """Override Streamlit's native widget colors for the light product look."""
    return f"""
/* ── App shell — airy canvas with a faint accent wash ── */
[data-testid="stApp"] {{
    background-color: {c['bg']} !important;
    background-image: radial-gradient(1100px 620px at 100% -8%, {c['accent_dim']}, transparent 55%) !important;
    background-attachment: fixed !important;
}}
.block-container {{
    max-width: 1260px !important;
    padding-top: 2.4rem !important;
    padding-bottom: 4rem !important;
    position: relative;
    z-index: 1;
}}
[data-testid="stHeader"] {{ background: transparent !important; }}

/* ── Sidebar — clean app shell + page nav ── */
section[data-testid="stSidebar"] {{
    background-color: {c['surface']} !important;
    border-right: 1px solid {c['border']} !important;
    position: relative;
    z-index: 2;
}}
section[data-testid="stSidebar"] p, section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] span, section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3 {{ color: {c['text']} !important; }}
[data-testid="stSidebarNav"] a {{ border-radius: 10px !important; margin: 1px 6px !important; }}
[data-testid="stSidebarNav"] a:hover {{ background: {c['surface2']} !important; }}
[data-testid="stSidebarNav"] a[aria-current="page"] {{ background: {c['accent_dim']} !important; }}
[data-testid="stSidebarNav"] a[aria-current="page"] span {{ color: {c['accent']} !important; font-weight: 700; }}

/* ── Text ── */
[data-testid="stAppViewContainer"] p,
[data-testid="stAppViewContainer"] li,
[data-testid="stAppViewContainer"] label,
.stMarkdown, .stMarkdown p, .stText,
[data-testid="stWidgetLabel"] p {{ color: {c['text']} !important; }}
h1, h2, h3, h4, h5, h6 {{ color: {c['text']} !important; }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{ color: {c['muted']} !important; }}
[data-testid="stAppViewContainer"] a {{ color: {c['accent']} !important; }}

/* ── Page title — big, bold, confident ── */
[data-testid="stAppViewContainer"] .block-container h1 {{
    font-size: 2.3rem !important;
    font-weight: 800 !important;
    letter-spacing: -0.025em !important;
    margin-bottom: 0.5rem !important;
}}
/* ── Section headers — signature diamond tick (a clean nod to the diamond) ── */
[data-testid="stAppViewContainer"] .block-container h3 {{
    position: relative;
    padding-left: 1.3rem;
    font-weight: 800 !important;
}}
[data-testid="stAppViewContainer"] .block-container h3::before {{
    content: '';
    position: absolute; left: 0; top: 0.42em;
    width: 0.62rem; height: 0.62rem;
    background: linear-gradient(135deg, {c['accent']}, {c['accent2']});
    border-radius: 3px;
    transform: rotate(45deg);
}}

/* ── Metrics → cards ── */
[data-testid="stMetric"], [data-testid="metric-container"] {{
    background: {c['surface']} !important;
    border: 1px solid {c['border']} !important;
    border-radius: 16px !important;
    padding: 1rem 1.2rem !important;
    box-shadow: {c['shadow']};
}}
[data-testid="stMetricLabel"] p {{
    color: {c['muted']} !important;
    text-transform: uppercase; letter-spacing: 0.06em; font-size: 0.72rem; font-weight: 600;
}}
[data-testid="stMetricValue"] {{ color: {c['text']} !important; font-weight: 800; font-family: 'Manrope', sans-serif; }}

/* ── Buttons → pills ── */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button {{
    background: {c['surface']} !important;
    border: 1px solid {c['border2']} !important;
    color: {c['text']} !important;
    border-radius: 999px !important;
    padding: 0.55rem 1.4rem !important;
    font-family: 'Inter', sans-serif !important;
    font-weight: 600 !important;
    transition: border-color 0.15s, color 0.15s, background 0.15s, transform 0.15s, box-shadow 0.15s !important;
}}
.stButton > button:hover, .stDownloadButton > button:hover {{
    border-color: {c['accent']} !important;
    color: {c['accent']} !important;
    transform: translateY(-1px) !important;
}}
.stButton > button[kind="primary"], .stFormSubmitButton > button {{
    background: {c['accent']} !important;
    border-color: {c['accent']} !important;
    color: #ffffff !important;
    box-shadow: 0 6px 16px {c['accent']}40 !important;
}}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover {{
    transform: translateY(-1px) !important;
    box-shadow: 0 9px 22px {c['accent']}55 !important;
    color: #ffffff !important;
    filter: brightness(1.04);
}}
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible {{
    outline: 2px solid {c['accent']} !important; outline-offset: 2px !important;
}}

/* ── Inputs ── */
.stTextInput > div > div > input,
.stNumberInput > div > div > input,
.stDateInput > div > div > input,
.stTextArea textarea {{
    background: {c['surface']} !important;
    border-color: {c['border2']} !important;
    color: {c['text']} !important;
    border-radius: 10px !important;
}}
.stTextInput input::placeholder, .stNumberInput input::placeholder {{ color: {c['muted']} !important; }}
[data-baseweb="select"] > div, [data-baseweb="input"] > div {{
    background: {c['surface']} !important;
    border-color: {c['border2']} !important;
    color: {c['text']} !important;
    border-radius: 10px !important;
}}
[data-baseweb="select"] svg {{ fill: {c['muted']} !important; }}
[data-baseweb="popover"], [data-baseweb="menu"], ul[role="listbox"] {{
    background: {c['surface']} !important;
    border: 1px solid {c['border']} !important;
    border-radius: 12px !important;
    box-shadow: {c['shadow_lg']} !important;
}}
[role="option"]:hover, li[role="option"][aria-selected="true"] {{ background: {c['surface2']} !important; }}
[data-baseweb="tag"] {{ background: {c['accent_dim']} !important; color: {c['accent']} !important; border: none !important; }}

/* ── Radio / checkbox ── */
[data-testid="stRadio"] label, [data-testid="stRadio"] p,
[data-testid="stCheckbox"] label, [data-testid="stCheckbox"] p {{ color: {c['text']} !important; }}

/* ── Expanders ── */
[data-testid="stExpander"] {{
    background: {c['surface']} !important;
    border: 1px solid {c['border']} !important;
    border-radius: 16px !important;
    box-shadow: {c['shadow']};
}}
[data-testid="stExpander"] summary {{ color: {c['text']} !important; font-weight: 600; }}
[data-testid="stExpander"] summary:hover {{ color: {c['accent']} !important; }}

/* ── Forms ── */
[data-testid="stForm"] {{
    background: {c['surface']} !important;
    border: 1px solid {c['border']} !important;
    border-radius: 18px !important;
    padding: 1.4rem !important;
    box-shadow: {c['shadow']};
}}

/* ── Divider ── */
hr {{ border-color: {c['border']} !important; opacity: 1 !important; }}

/* ── Alerts ── */
[data-testid="stAlert"] {{
    background: {c['surface']} !important;
    border: 1px solid {c['border']} !important;
    border-left-width: 4px !important;
    border-radius: 12px !important;
    box-shadow: {c['shadow']};
}}
[data-testid="stAlert"] p, [data-testid="stAlert"] div {{ color: {c['text']} !important; }}

/* ── Tabs → pills ── */
[data-testid="stTabs"] [data-baseweb="tab-list"] {{ background: {c['surface2']} !important; border-radius: 999px; padding: 4px; gap: 4px; }}
[data-testid="stTabs"] button {{ color: {c['text2']} !important; border-radius: 999px; }}
[data-testid="stTabs"] button[aria-selected="true"] {{ color: {c['accent']} !important; background: {c['surface']} !important; box-shadow: {c['shadow']}; }}

/* ── Dataframe ── */
[data-testid="stDataFrame"] > div {{ border: 1px solid {c['border']} !important; border-radius: 12px; }}
"""


def _custom_css(c: dict) -> str:
    """Styles for custom HTML components (cards, badges, etc.)."""
    return f"""
/* ── Background decor — soft, page-coloured shapes behind content ── */
.bg-decor {{ position: fixed; inset: 0; z-index: 0; pointer-events: none; overflow: hidden; }}
.bg-decor > * {{ position: absolute; }}
.decor-ball  {{ width: 158px; height: 158px; right: 4%; bottom: 7%; opacity: 0.09; transform: rotate(-12deg); }}
.decor-plate {{ width: 122px; height: 122px; right: -30px; top: 42%; opacity: 0.07; transform: rotate(8deg); }}
.decor-ring  {{ width: 118px; height: 118px; right: 9%; top: 8%; border: 14px solid currentColor; border-radius: 50%; opacity: 0.06; }}
.decor-dot   {{ border-radius: 50%; background: currentColor; }}
.decor-dot.d1 {{ width: 14px; height: 14px; left: 41%; top: 13%; opacity: 0.14; }}
.decor-dot.d2 {{ width: 24px; height: 24px; right: 27%; bottom: 12%; opacity: 0.10; }}
@media (max-width: 1100px) {{ .bg-decor {{ display: none; }} }}

/* ── Eyebrow pill — kicker above a section/headline ── */
.eyebrow-pill {{
    display: inline-block;
    font-family: 'Space Mono', monospace;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: {c['accent']};
    background: {c['accent_dim']};
    border: 1px solid {c['accent']}33;
    padding: 5px 13px;
    border-radius: 999px;
    margin-bottom: 0.6rem;
}}

/* ── Hero (app.py) — two-column statement ── */
.hero {{
    display: flex;
    gap: 2.5rem;
    align-items: center;
    flex-wrap: wrap;
    background: linear-gradient(135deg, {c['surface']} 0%, {c['surface2']} 100%) !important;
    border: 1px solid {c['border']} !important;
    border-radius: 24px;
    padding: 2.8rem 3rem;
    margin-bottom: 1.8rem;
    position: relative;
    overflow: hidden;
    box-shadow: {c['shadow_lg']};
    animation: heroIn 0.5s ease-out;
}}
.hero::before {{   /* soft accent glow */
    content: '';
    position: absolute;
    top: -45%; right: -6%;
    width: 480px; height: 480px;
    border-radius: 50%;
    background: radial-gradient(circle, {c['accent']}22 0%, {c['accent2']}14 45%, transparent 70%);
    pointer-events: none;
}}
.hero-main {{ flex: 1 1 360px; position: relative; z-index: 1; }}
.hero-eyebrow {{   /* legacy alias → eyebrow pill look */
    display: inline-block;
    font-family: 'Space Mono', monospace;
    font-size: 0.68rem; font-weight: 700;
    letter-spacing: 0.14em; text-transform: uppercase;
    color: {c['accent']}; background: {c['accent_dim']};
    border: 1px solid {c['accent']}33; padding: 5px 13px;
    border-radius: 999px; margin-bottom: 0.85rem;
}}
.block-container .hero h1 {{
    font-family: 'Manrope', sans-serif;
    font-size: 2.9rem !important;
    font-weight: 800 !important;
    color: {c['text']} !important;
    margin: 0 0 0.8rem 0 !important;
    line-height: 1.05 !important;
    letter-spacing: -0.03em !important;
    max-width: 18ch;
}}
.hero-sub {{
    color: {c['text2']};
    font-size: 1.02rem;
    margin: 0;
    line-height: 1.6;
    max-width: 52ch;
}}
.hero-meta {{
    font-family: 'Space Mono', monospace;
    font-size: 0.78rem;
    color: {c['muted']};
    margin-top: 1.2rem;
}}
/* Hero scorecard */
.hero-card {{
    flex: 0 1 320px;
    position: relative; z-index: 1;
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 18px;
    padding: 1.6rem 1.7rem;
    box-shadow: {c['shadow_lg']};
}}
.hc-label {{
    font-family: 'Space Mono', monospace;
    font-size: 0.64rem; letter-spacing: 0.1em; text-transform: uppercase;
    color: {c['muted']}; margin-bottom: 0.35rem;
}}
.hc-pnl {{
    font-family: 'Manrope', sans-serif; font-weight: 800;
    font-size: 2.5rem; line-height: 1; margin-bottom: 1.2rem;
}}
.hc-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0.9rem 1rem; }}
.hc-stat {{ display: flex; flex-direction: column; gap: 3px; }}
.hc-stat span {{ font-size: 0.66rem; color: {c['muted']}; text-transform: uppercase; letter-spacing: 0.06em; }}
.hc-stat b {{ font-family: 'Manrope', sans-serif; font-weight: 800; font-size: 1.2rem; color: {c['text']}; }}

/* ── Info grid (app.py) ── */
.info-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin: 1.5rem 0; }}
.info-row {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 16px;
    padding: 1.1rem 1.3rem;
    display: flex; gap: 0.9rem; align-items: flex-start;
    box-shadow: {c['shadow']};
}}
.info-row .dot {{ width: 8px; height: 8px; border-radius: 50%; background: {c['accent']}; margin-top: 6px; flex-shrink: 0; }}
.info-row .label {{ font-family: 'Manrope', sans-serif; font-size: 0.9rem; font-weight: 800; color: {c['text']}; margin-bottom: 3px; }}
.info-row .desc {{ font-size: 0.8rem; color: {c['muted']}; line-height: 1.45; margin: 0; }}

/* ── Setup box ── */
.setup-box {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 16px;
    padding: 1.2rem 1.5rem; font-size: 0.9rem; color: {c['text2']}; line-height: 1.7;
    box-shadow: {c['shadow']};
}}
.setup-box a {{ color: {c['accent']}; text-decoration: none; }}
.setup-box code {{
    background: {c['surface2']}; padding: 1px 6px; border-radius: 4px;
    font-family: 'Space Mono', monospace; font-size: 0.8rem; color: {c['accent']};
}}

/* ── Today's Games: game card ── */
.game-block {{
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-left: 4px solid {c['border']};
    border-radius: 18px;
    padding: 1.4rem 1.7rem;
    margin-bottom: 0.6rem;
    transition: border-color 0.2s, box-shadow 0.2s, transform 0.2s;
    box-shadow: {c['shadow']};
}}
.game-block:hover {{ transform: translateY(-2px); box-shadow: {c['shadow_lg']}; }}
.game-block.signal-hot   {{ border-left-color: {c['amber']}; }}
.game-block.signal-value {{ border-left-color: {c['green']}; }}
.game-block.signal-edge  {{ border-left-color: {c['accent']}; }}
.game-block.signal-none  {{ border-left-color: {c['border']}; }}

.game-matchup {{
    font-family: 'Manrope', sans-serif;
    font-size: 1.25rem;
    font-weight: 800;
    color: {c['text']};
    letter-spacing: -0.02em;
}}
.game-at {{ color: {c['muted']}; font-weight: 500; font-size: 1rem; margin: 0 0.4rem; }}
.game-meta {{ font-size: 0.78rem; color: {c['muted']}; margin-top: 0.2rem; margin-bottom: 0.5rem; }}
.pitcher-badge {{
    display: inline-block; font-size: 0.65rem; font-weight: 700;
    font-family: 'Space Mono', monospace; letter-spacing: 0.06em; text-transform: uppercase;
    color: {c['green']}; background: {c['green_dim']}; border: 1px solid {c['green']}33;
    padding: 2px 9px; border-radius: 999px; margin-left: 10px; vertical-align: middle;
}}

/* ── Pitcher boxes ── */
.pitcher-box {{
    background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 12px;
    padding: 0.8rem 1rem; margin: 0.5rem 0 0.8rem 0; font-size: 0.85rem;
}}
.pitcher-stat {{ color: {c['muted']}; font-size: 0.78rem; }}
.pitcher-name {{ color: {c['green']}; font-weight: 700; }}
.trend-better {{ color: {c['green']}; }}
.trend-worse  {{ color: {c['red']}; }}
.trend-flat   {{ color: {c['muted']}; }}

/* ── Sportsbook badges ── */
.book-badge {{
    display: inline-block; padding: 3px 11px; border-radius: 999px;
    font-size: 0.7rem; font-weight: 700; letter-spacing: 0.05em; text-transform: uppercase;
    font-family: 'Space Mono', monospace;
}}
.badge-caesars    {{ background: {c['green_dim']}; color: {c['green']}; border: 1px solid {c['green']}33; }}
.badge-betmgm     {{ background: {c['red_dim']}; color: {c['red']}; border: 1px solid {c['red']}33; }}
.badge-default    {{ background: {c['surface2']}; color: {c['text2']}; border: 1px solid {c['border']}; }}

/* ── Value signal classes ── */
.value-hot   {{ color: {c['amber']}; font-weight: 700; }}
.value-yes   {{ color: {c['green']}; font-weight: 700; }}
.value-edge  {{ color: {c['accent']}; }}
.value-no    {{ color: {c['muted']}; }}
.value-avoid {{ color: {c['red']}; }}

/* ── Page header + Rec badges ── */
.page-header {{
    background: linear-gradient(135deg, {c['surface']} 0%, {c['surface2']} 100%);
    border: 1px solid {c['border']};
    border-radius: 20px;
    padding: 1.7rem 2.1rem;
    margin-bottom: 1.5rem;
    box-shadow: {c['shadow']};
}}
.page-header h2 {{ margin: 0 0 0.3rem 0; color: {c['text']} !important; font-family: 'Manrope', sans-serif; font-size: 1.7rem; font-weight: 800; letter-spacing: -0.02em; }}
.page-header p  {{ margin: 0; color: {c['muted']}; font-size: 0.92rem; }}
.rec-badge {{
    display: inline-block; padding: 3px 9px; border-radius: 999px;
    font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em;
    font-family: 'Space Mono', monospace;
}}
.rec-hot   {{ background: {c['amber']}22; color: {c['amber']}; border: 1px solid {c['amber']}44; }}
.rec-value {{ background: {c['green_dim']}; color: {c['green']}; border: 1px solid {c['green']}33; }}
.rec-edge  {{ background: {c['accent_dim']}; color: {c['accent']}; border: 1px solid {c['accent']}33; }}
.rec-none  {{ background: {c['surface2']}; color: {c['muted']}; border: 1px solid {c['border']}; }}
.payout-row {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px;
    padding: 0.9rem 1.2rem; margin-bottom: 0.5rem; box-shadow: {c['shadow']};
}}
.total-bar {{
    background: {c['surface2']}; border: 1px solid {c['border2']}; border-radius: 14px;
    padding: 1.2rem 1.5rem; margin-top: 1rem;
}}
.stat-box {{
    position: relative;
    background: {c['surface']};
    border: 1px solid {c['border']};
    border-radius: 16px;
    padding: 1.3rem 1.1rem 1.1rem;
    text-align: center;
    overflow: hidden;
    box-shadow: {c['shadow']};
    transition: transform 0.18s, box-shadow 0.18s;
}}
.stat-box::before {{
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, {c['accent']}, {c['accent2']});
}}
.stat-box:hover {{ transform: translateY(-3px); box-shadow: {c['shadow_lg']}; }}

/* ── Bet slip sidebar ── */
.slip-header {{ font-family: 'Manrope', sans-serif; font-size: 1.05rem; font-weight: 800; color: {c['text']}; margin: 0.3rem 0 0.8rem 0; }}
.slip-section {{ font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: {c['muted']}; margin: 0.6rem 0 0.4rem 0; }}
.slip-game {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 12px; padding: 0.6rem 0.8rem; margin-bottom: 0.5rem; font-size: 0.82rem; }}
.slip-game-label {{ font-weight: 700; color: {c['text']}; font-size: 0.85rem; margin-bottom: 0.2rem; }}
.slip-meta {{ font-size: 0.72rem; color: {c['muted']}; font-family: 'Space Mono', monospace; }}
.slip-summary {{ background: {c['surface2']}; border: 1px solid {c['border2']}; border-radius: 12px; padding: 0.6rem 0.8rem; margin: 0.5rem 0; font-size: 0.82rem; }}

/* ── Live Scores ── */
.status-live    {{ color: {c['red']}; font-weight: 700; font-family: 'Space Mono', monospace; font-size: 0.85rem; }}
.status-final   {{ color: {c['green']}; font-weight: 700; font-family: 'Space Mono', monospace; font-size: 0.85rem; }}
.status-pre     {{ color: {c['accent']}; font-weight: 700; font-family: 'Space Mono', monospace; font-size: 0.85rem; }}
.status-delayed {{ color: {c['amber']}; font-weight: 700; font-family: 'Space Mono', monospace; font-size: 0.85rem; }}
.bet-pill {{
    display: inline-block; padding: 2px 12px; border-radius: 999px;
    font-size: 0.7rem; font-weight: 700; letter-spacing: 0.04em;
    margin-left: 8px; vertical-align: middle; font-family: 'Space Mono', monospace;
}}
.bet-active {{ background: {c['green_dim']}; color: {c['green']}; border: 1px solid {c['green']}33; }}
.bet-win    {{ background: {c['green_dim']}; color: {c['green']}; border: 1px solid {c['green']}33; }}
.bet-loss   {{ background: {c['red_dim']}; color: {c['red']}; border: 1px solid {c['red']}33; }}
.bet-push   {{ background: {c['surface2']}; color: {c['muted']}; border: 1px solid {c['border']}; }}
.boxscore-table {{ width: 100%; border-collapse: collapse; font-family: 'Space Mono', monospace; font-size: 0.8rem; margin-top: 0.7rem; }}
.boxscore-table th {{
    background: {c['surface2']}; color: {c['muted']}; padding: 6px 10px; text-align: center;
    border-bottom: 1px solid {c['border']}; font-weight: 700; font-size: 0.68rem;
    letter-spacing: 0.08em; text-transform: uppercase;
}}
.boxscore-table th:first-child {{ text-align: left; min-width: 150px; }}
.boxscore-table td {{ padding: 7px 10px; text-align: center; border-bottom: 1px solid {c['border']}; color: {c['text']}; }}
.boxscore-table td:first-child {{ text-align: left; font-weight: 700; color: {c['text']}; }}
.boxscore-table tr:last-child td {{ border-bottom: none; }}
.boxscore-table .sep {{ border-left: 1px solid {c['border']}; }}
.boxscore-table .totals {{ font-weight: 700; color: {c['text']}; }}
.boxscore-table .current-inn {{ color: {c['amber']}; }}
.boxscore-table .dim {{ color: {c['muted']}; }}
.pitcher-line {{ font-size: 0.78rem; color: {c['muted']}; margin-top: 8px; }}
.pitcher-line strong {{ color: {c['text2']}; }}

/* ── Context badges (park factor + weather) ── */
.context-row {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; margin-bottom: 2px; }}
.ctx-badge {{
    display: inline-flex; align-items: center; padding: 3px 11px; border-radius: 999px;
    font-size: 0.72rem; font-weight: 600; font-family: 'Inter', sans-serif; letter-spacing: 0.02em;
}}
.park-extreme   {{ background: {c['amber']}22;  color: {c['amber']};  border: 1px solid {c['amber']}44; }}
.park-hitter    {{ background: {c['green_dim']}; color: {c['green']};  border: 1px solid {c['green']}33; }}
.park-pitcher   {{ background: {c['accent_dim']}; color: {c['accent']}; border: 1px solid {c['accent']}33; }}
.wx-cold        {{ background: {c['accent_dim']}; color: {c['accent']}; border: 1px solid {c['accent']}33; }}
.wx-hot         {{ background: {c['amber']}1a;  color: {c['amber']};  border: 1px solid {c['amber']}44; }}
.wx-wind        {{ background: {c['surface2']}; color: {c['muted']};  border: 1px solid {c['border']}; }}
.wx-wind-strong {{ background: {c['amber']}22;  color: {c['amber']};  border: 1px solid {c['amber']}44; }}
"""
