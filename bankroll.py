"""
bankroll.py — bankroll tracking + daily-budget recommendation.

One self-contained home for the money-management feature:

  • storage        the user's starting bankroll lives in the `bankroll` table
                   (one row, written once). Current balance is *derived* —
                   initial + realized P&L from resolved REAL bets — so it stays
                   accurate with zero manual upkeep and can go negative.
  • startup gate   require_balance() shows a one-time prompt if no bankroll is
                   set yet, then never touches the DB again this session.
  • display        render_balance_card() — the bankroll hero on Bet Tracker.
  • recommendation recommend_daily_budget() — fractional-Kelly across today's
                   value bets, scaled by a risk level and capped as a share of
                   bankroll. Auto-assigned into the Real bet slip budget.

Real bets only — paper bets never touch the bankroll.
Colors always come from theme.palette(); never hardcode hex here.
"""

from __future__ import annotations

from database import get_connection

# streamlit + theme are presentation-only and imported lazily inside the UI
# functions, so the pure recommend_daily_budget() math stays importable (and
# self-checkable) in any environment.

# Risk levels scale the Kelly engine two ways: kelly_mult shrinks/grows the
# fraction of bankroll each value bet claims (¼-Kelly is the Moderate baseline),
# and cap_pct hard-limits a single day's total exposure as a share of bankroll.
RISK_LEVELS: dict[str, dict[str, float]] = {
    "Conservative": {"kelly_mult": 0.5, "cap_pct": 0.05},
    "Moderate":     {"kelly_mult": 1.0, "cap_pct": 0.10},
    "Aggressive":   {"kelly_mult": 1.5, "cap_pct": 0.20},
}
DEFAULT_RISK = "Moderate"

# Outcomes that count as settled money (mirrors the Bet Tracker's `completed`).
_RESOLVED = ("Win", "Loss", "Push", "Cashout")


# ── Storage ──────────────────────────────────────────────────────────────────

def get_initial_balance(user_id: int) -> float | None:
    """The starting bankroll this user entered once, or None if never set (1.4)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT initial_balance FROM bankroll WHERE user_id = ? ORDER BY id LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return float(row["initial_balance"]) if row else None


def set_initial_balance(amount: float, user_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "INSERT INTO bankroll (initial_balance, user_id) VALUES (?, ?)",
        (float(amount), user_id),
    )
    conn.commit()
    conn.close()


def get_balance_state(user_id: int) -> dict | None:
    """{'initial', 'current', 'delta'} for one user, or None if no bankroll set.

    current = initial + that user's realized real-bet P&L; delta = current −
    initial. One round-trip: the starting bankroll and the resolved-bet P&L in a
    single query, since this runs on every rerun of Today's Games.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT b.initial_balance AS initial, "
        "COALESCE((SELECT SUM(profit_loss) FROM bets "
        "          WHERE outcome IN (?, ?, ?, ?) AND user_id = ?), 0) AS pnl "
        "FROM bankroll b WHERE b.user_id = ? ORDER BY b.id LIMIT 1",
        (*_RESOLVED, user_id, user_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    initial = float(row["initial"])
    delta = float(row["pnl"] or 0.0)
    return {"initial": initial, "current": initial + delta, "delta": delta}


# ── Startup gate + prompt ────────────────────────────────────────────────────

def require_balance() -> None:
    """Block the page until the logged-in user has a bankroll. No-op (no query)
    once known set this session.

    Call right after require_login() on the entry pages. The first page load
    does one tiny indexed read; every call after is a dict lookup, so a user who
    already has a bankroll pays nothing on subsequent navigations. The flag is
    cleared on login (auth._login), so each user is gated on their own balance.
    """
    import streamlit as st
    from auth import current_user_id
    uid = current_user_id()
    if uid is None or st.session_state.get("_bankroll_ok"):
        return
    if get_initial_balance(uid) is not None:
        st.session_state["_bankroll_ok"] = True
        return
    _render_prompt(uid)
    st.stop()


def _render_prompt(user_id: int) -> None:
    """One-time, theme-matched bankroll setup card. Centered; gates the app."""
    import streamlit as st
    from theme import palette
    c = palette()
    _, mid, _ = st.columns([1, 1.5, 1])
    with mid:
        with st.form("bankroll_setup", clear_on_submit=False):
            st.markdown(
                f'<span class="eyebrow-pill">Set up · Bankroll</span>'
                f'<div style="font-family:\'Manrope\',sans-serif; font-weight:800; '
                f'font-size:1.7rem; line-height:1.1; letter-spacing:-0.025em; '
                f'color:{c["text"]}; margin:0.5rem 0 0.4rem;">What\'s your betting bankroll?</div>'
                f'<p style="color:{c["text2"]}; font-size:0.95rem; line-height:1.55; margin:0 0 0.4rem;">'
                f'The total you\'ve set aside for betting. We\'ll use it to size a recommended '
                f'daily budget — and track how it grows as your real bets settle.</p>',
                unsafe_allow_html=True,
            )
            amount = st.number_input(
                "Starting bankroll ($)",
                min_value=0.0, value=0.0, step=10.0, format="%.2f",
                help="You can enter any starting amount. It's logged once as your baseline.",
            )
            submitted = st.form_submit_button(
                "Save & continue", type="primary", use_container_width=True
            )
        if submitted:
            if amount > 0:
                set_initial_balance(amount, user_id)
                st.session_state["_bankroll_ok"] = True
                st.rerun()
            else:
                st.error("Enter your bankroll to continue — it must be more than $0.")


# ── Bet Tracker display ──────────────────────────────────────────────────────

def render_balance_card(state: dict) -> None:
    """Bankroll hero: big current balance + signed delta vs. the starting value.

    Up since start → green '+'; below start → red '−' (mirrors the ROI metric).
    """
    import streamlit as st
    from theme import palette
    c = palette()
    initial, current, delta = state["initial"], state["current"], state["delta"]
    pct = (delta / initial * 100) if initial else 0.0

    if delta > 0:
        color, sign = c["green"], "+"
        note = f'{sign}${abs(delta):,.2f} &nbsp;({sign}{abs(pct):.1f}%) since start'
    elif delta < 0:
        color, sign = c["red"], "−"
        note = f'{sign}${abs(delta):,.2f} &nbsp;({sign}{abs(pct):.1f}%) since start'
    else:
        color = c["muted"]
        note = "Even with your starting bankroll"

    # Balance can go negative (1.6) — render the sign outside the $ ("−$50.00").
    cur_str = f"${current:,.2f}" if current >= 0 else f"−${abs(current):,.2f}"

    st.markdown(
        f'<div class="balance-hero">'
        f'  <div class="balance-main">'
        f'    <div class="bh-label">Current Bankroll</div>'
        f'    <div class="bh-value" style="color:{color};">{cur_str}</div>'
        f'    <div class="bh-note" style="color:{color};">{note}</div>'
        f'  </div>'
        f'  <div class="balance-aside">'
        f'    <div class="bh-aside-label">Starting</div>'
        f'    <div class="bh-aside-value">${initial:,.2f}</div>'
        f'    <div class="bh-aside-sub">Realized real-bet P&amp;L only · paper bets excluded</div>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Recommendation ───────────────────────────────────────────────────────────

def recommend_daily_budget(
    kelly_fractions: list[float], balance: float, risk: str = DEFAULT_RISK
) -> float:
    """Recommended total stake for today from a bankroll and the day's value bets.

    kelly_fractions are the per-bet ¼-Kelly fractions (from evaluate_value) of
    the bets worth backing. Sum them into a bankroll share, scale by the risk
    level, and cap total exposure so one big slate can't overcommit the roll.
    Returns 0 when there's nothing worth betting or the bankroll is depleted.
    """
    cfg = RISK_LEVELS.get(risk, RISK_LEVELS[DEFAULT_RISK])
    if balance <= 0 or not kelly_fractions:
        return 0.0
    raw = sum(max(k, 0.0) for k in kelly_fractions) * balance * cfg["kelly_mult"]
    capped = min(raw, balance * cfg["cap_pct"])
    return round(max(capped, 0.0), 2)


if __name__ == "__main__":
    # ponytail: money-path self-check — runs without a DB.
    assert recommend_daily_budget([], 1000) == 0.0
    assert recommend_daily_budget([0.02], -5) == 0.0
    # Cap binds: huge Kelly sum clipped to cap_pct of bankroll.
    assert recommend_daily_budget([1.0, 1.0], 1000, "Moderate") == 100.0   # 10% cap
    assert recommend_daily_budget([1.0], 1000, "Conservative") == 50.0     # 5% cap
    # Below the cap, risk ordering holds for the same bets/bankroll.
    lo = recommend_daily_budget([0.01], 1000, "Conservative")
    mid = recommend_daily_budget([0.01], 1000, "Moderate")
    hi = recommend_daily_budget([0.01], 1000, "Aggressive")
    assert lo < mid < hi, (lo, mid, hi)
    assert mid == round(0.01 * 1000 * 1.0, 2) == 10.0
    print("bankroll self-check OK")
