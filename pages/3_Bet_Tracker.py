"""
pages/3_Bet_Tracker.py — Log bets, track outcomes, measure ROI and CLV
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import sqlite3
from datetime import date
from database import get_connection, init_db
from ingestion.auto_resolver import batch_resolve_bets, refresh_closing_odds, _lookup_closing_odds_cache
from theme import init_theme, palette

init_db()

st.set_page_config(page_title="Bet Tracker", page_icon="📒", layout="wide")
init_theme()

st.title("📒 Bet Tracker")
st.caption("Log your bets, track ROI, and measure Closing Line Value to validate your edge")

# --- Log a New Bet ---
with st.expander("➕ Log a New Bet", expanded=False):
    with st.form("new_bet"):
        fc1, fc2 = st.columns(2)
        game_date = fc1.date_input("Game Date", value=date.today())
        home_team = fc1.text_input("Home Team")
        away_team = fc1.text_input("Away Team")
        # Build team options dynamically so the label shows the actual team name
        home_label = home_team.strip() if home_team.strip() else "Home Team"
        away_label = away_team.strip() if away_team.strip() else "Away Team"
        bet_on = fc2.selectbox("Bet On", [home_label, away_label],
                               help="Select the team you bet on")
        odds = fc2.number_input("Odds (American, e.g. -150 or +130)", value=-110)
        stake = fc2.number_input("Stake ($)", min_value=1.0, value=10.0)
        model_prob = fc2.number_input("Model Win Prob (%)", min_value=0.0, max_value=100.0, value=55.0) / 100
        implied_prob = fc2.number_input("Market Implied Prob (%)", min_value=0.0, max_value=100.0, value=50.0) / 100
        notes = st.text_input("Notes (optional)")
        submitted = st.form_submit_button("Log Bet")

    if submitted and home_team and away_team:
        conn = get_connection()
        conn.execute("""
            INSERT INTO bets (game_date, home_team, away_team, bet_on, odds, stake, model_prob, implied_prob, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (str(game_date), home_team, away_team, bet_on, int(odds), stake, model_prob, implied_prob, notes))
        conn.commit()
        conn.close()
        st.success("Bet logged!")
        st.rerun()

st.divider()

# --- Load Bets ---
conn = get_connection()
bets_raw = pd.read_sql("SELECT * FROM bets ORDER BY game_date DESC, id DESC", conn)
conn.close()

if bets_raw.empty:
    st.info("No bets logged yet. Use the form above to start tracking.")
    st.stop()

# --- Delete a Bet ---
with st.expander("🗑️ Delete a Bet", expanded=False):
    options = {
        row["id"]: (
            f"{row['game_date']} — {row['away_team']} @ {row['home_team']} — "
            f"Bet: {row['bet_on']} @ {'+' if int(row['odds']) > 0 else ''}{int(row['odds'])}"
            + (f" — {row['outcome']}" if row["outcome"] else "")
        )
        for _, row in bets_raw.iterrows()
    }
    del_id = st.selectbox(
        "Select a bet to delete",
        options=list(options.keys()),
        format_func=lambda i: options[i],
        key="del_bet_id",
    )
    confirm = st.checkbox("I'm sure I want to permanently delete this bet", key="del_bet_confirm")
    if st.button("🗑️ Delete Bet", type="primary", disabled=not confirm, key="del_bet_btn"):
        conn_del = get_connection()
        conn_del.execute("DELETE FROM bets WHERE id = ?", (int(del_id),))
        conn_del.commit()
        conn_del.close()
        st.success("Bet deleted.")
        st.rerun()

# --- Refresh Closing Odds Cache ---
with st.expander("📥 Refresh Closing Odds", expanded=False):
    st.caption(
        "Fetches closing odds for all MLB games on selected date(s) and stores them locally. "
        "Run this before auto-resolving bets — subsequent resolves use the cache with no extra API calls."
    )
    from datetime import date as _date, timedelta as _td
    yesterday = _date.today() - _td(days=1)
    rc1, rc2 = st.columns(2)
    rc_start = rc1.date_input("From date", value=yesterday, key="rc_start")
    rc_end   = rc2.date_input("To date",   value=yesterday, key="rc_end")

    if st.button("⬇️ Refresh Closing Odds", type="primary", key="refresh_close_btn"):
        if rc_end < rc_start:
            st.error("End date must be on or after start date.")
        else:
            dates = []
            d = rc_start
            while d <= rc_end:
                dates.append(str(d))
                d += _td(days=1)
            with st.spinner(f"Fetching closing odds for {len(dates)} date(s)…"):
                result = refresh_closing_odds(dates)
            st.success(
                f"✅ {result['games_cached']} game(s) cached across {result['dates_processed']} date(s) "
                f"using {result['api_calls']} API call(s)."
            )
            if result["errors"]:
                with st.expander(f"⚠️ {len(result['errors'])} warning(s)", expanded=False):
                    for e in result["errors"]:
                        st.caption(e)

# --- Update Outcomes ---
with st.expander("✏️ Update Bet Outcomes", expanded=False):
    pending = bets_raw[bets_raw["outcome"].isna() | (bets_raw["outcome"] == "")]
    if pending.empty:
        st.success("All bets have outcomes recorded!")
    else:
        # ── Auto-resolve section ───────────────────────────────────────────────
        st.markdown("**🤖 Auto-Resolve**")
        st.caption("Fetches game results from MLB Stats API and closing odds from The Odds API.")

        sel_all = st.checkbox("Select all", key="sel_all_bets")
        # Propagate select-all changes to individual checkboxes via session state
        prev_sel_all = st.session_state.get("_prev_sel_all_bets")
        if prev_sel_all is not None and sel_all != prev_sel_all:
            for _, row in pending.iterrows():
                st.session_state[f"chk_{row['id']}"] = sel_all
        st.session_state["_prev_sel_all_bets"] = sel_all

        selected_ids = []
        for _, row in pending.iterrows():
            checked = st.checkbox(
                f"{row['away_team']} @ {row['home_team']} ({row['game_date']}) — "
                f"Bet: {row['bet_on']} @ "
                f"`{'+' if int(row['odds']) > 0 else ''}{int(row['odds'])}`",
                key=f"chk_{row['id']}",
            )
            if checked:
                selected_ids.append(row["id"])

        if st.button("⚡ Auto-Resolve Selected", type="primary",
                     disabled=len(selected_ids) == 0, key="auto_resolve_btn"):
            to_resolve = pending[pending["id"].isin(selected_ids)]
            bets_input = [
                {"id": int(r["id"]), "game_date": r["game_date"],
                 "home_team": r["home_team"], "away_team": r["away_team"],
                 "bet_on": r["bet_on"], "odds": int(r["odds"]), "stake": float(r["stake"])}
                for _, r in to_resolve.iterrows()
            ]
            with st.spinner(f"Resolving {len(bets_input)} bet(s)…"):
                results = batch_resolve_bets(bets_input)

            conn_ar = get_connection()
            resolved_count = 0
            messages = []
            for res in results:
                if res["outcome"] == "Pending":
                    messages.append(f"⏳ Still pending — {res['note']}")
                    continue
                if res["outcome"] == "NoCacheData":
                    messages.append(f"⚠️ Not resolved — {res['note']}")
                    continue
                conn_ar.execute("""
                    UPDATE bets SET outcome=?, profit_loss=?, closing_odds=?,
                                    closing_implied_prob=?, clv=?
                    WHERE id=?
                """, (
                    res["outcome"], res["profit_loss"],
                    int(res["closing_odds"]) if res["closing_odds"] is not None else None,
                    res["closing_implied_prob"], res["clv"], res["id"],
                ))
                resolved_count += 1
                icon = "✅" if res["outcome"] == "Win" else ("❌" if res["outcome"] == "Loss" else "⏸️")
                messages.append(f"{icon} → **{res['outcome']}** · {res['note']}")
            conn_ar.commit()
            conn_ar.close()

            for msg in messages:
                st.markdown(msg)
            if resolved_count:
                st.success(f"✅ {resolved_count} bet(s) resolved.")
                st.rerun()

        st.divider()

        # ── Manual resolve section ─────────────────────────────────────────────
        st.markdown("**✏️ Manual Resolve**")
        st.caption("Use for Push, Cashout, or Postponed. For Win/Loss use Auto-Resolve above — it fetches closing odds automatically.")
        for _, row in pending.iterrows():
            c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 1])
            c1.markdown(f"**{row['away_team']} @ {row['home_team']}** ({row['game_date']})")
            c2.markdown(f"Bet: {row['bet_on']} @ `{'+' if int(row['odds']) > 0 else ''}{int(row['odds'])}`")
            outcome = c3.selectbox("Outcome", ["", "Win", "Loss", "Push", "Cashout", "Postponed"], key=f"out_{row['id']}")

            # Pre-fill from cache if available (no API call)
            cached_close = _lookup_closing_odds_cache(
                row["game_date"], row["home_team"], row["away_team"], row["bet_on"]
            )
            close_default = cached_close if cached_close is not None else int(row["odds"])
            ss_key = f"cl_{row['id']}"
            if st.session_state.get(ss_key, int(row["odds"])) == int(row["odds"]) and cached_close is not None:
                st.session_state[ss_key] = close_default

            if outcome == "Postponed":
                cashout_pnl      = None
                closing_odds_val = int(row["odds"])
            elif outcome == "Cashout":
                cashout_pnl = c4.number_input(
                    "Cashout P&L ($)", value=0.0, step=0.01, key=f"co_{row['id']}",
                    help="Enter the actual profit/loss from your early cashout"
                )
                closing_odds_val = int(row["odds"])
            else:
                cashout_pnl = None
                closing_odds_val = c4.number_input(
                    "Closing Odds", value=close_default, key=f"cl_{row['id']}",
                    help="Pre-filled from cache if available. Run 'Refresh Closing Odds' above first."
                )

            if c5.button("Save", key=f"save_{row['id']}") and outcome:
                def calc_pnl(stake, odds, outcome):
                    if outcome == "Push": return 0
                    if outcome == "Win":
                        return stake * (odds / 100) if odds > 0 else stake * (100 / abs(odds))
                    return -stake

                def to_prob(o):
                    o = int(o)
                    return 100 / (o + 100) if o > 0 else abs(o) / (abs(o) + 100)

                if outcome == "Postponed":
                    pnl     = None
                    cl_prob = None
                    clv     = None
                    closing_odds_val = None
                elif outcome == "Cashout":
                    pnl     = cashout_pnl
                    cl_prob = to_prob(int(row["odds"]))
                    clv     = None
                else:
                    pnl      = calc_pnl(row["stake"], row["odds"], outcome)
                    cl_prob  = to_prob(closing_odds_val)
                    bet_prob = to_prob(row["odds"])
                    clv      = cl_prob - bet_prob

                conn2 = get_connection()
                conn2.execute("""
                    UPDATE bets SET outcome=?, profit_loss=?, closing_odds=?, closing_implied_prob=?, clv=?
                    WHERE id=?
                """, (outcome, pnl,
                      int(closing_odds_val) if closing_odds_val is not None else None,
                      cl_prob, clv, row["id"]))
                conn2.commit()
                conn2.close()
                st.rerun()

st.divider()

# --- Performance Dashboard ---
completed = bets_raw[bets_raw["outcome"].isin(["Win", "Loss", "Push", "Cashout"])].copy()  # Postponed excluded

if not completed.empty:
    completed["profit_loss"] = pd.to_numeric(completed["profit_loss"], errors="coerce")
    completed["stake"] = pd.to_numeric(completed["stake"], errors="coerce")
    completed["clv"] = pd.to_numeric(completed["clv"], errors="coerce")

    total_staked = completed["stake"].sum()
    total_pnl = completed["profit_loss"].sum()
    roi = (total_pnl / total_staked * 100) if total_staked > 0 else 0
    win_rate = (completed["outcome"] == "Win").mean() * 100
    avg_clv = completed["clv"].mean() * 100 if completed["clv"].notna().any() else 0
    n_bets = len(completed)

    _c = palette()
    roi_color = _c["green"] if roi >= 0 else _c["red"]
    pnl_color = _c["green"] if total_pnl >= 0 else _c["red"]
    clv_color = _c["green"] if avg_clv >= 0 else _c["red"]

    st.subheader("Performance Summary")
    st.markdown(f"""
<div style="display:flex; gap:12px; margin-bottom:1.5rem; flex-wrap:wrap;">
  <div class="stat-box" style="flex:1; min-width:110px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Total Bets</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Syne',sans-serif; color:{_c['text']};">{n_bets}</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:110px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Win Rate</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Syne',sans-serif; color:{_c['text']};">{win_rate:.1f}%</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:110px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">ROI</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Syne',sans-serif; color:{roi_color};">{roi:+.1f}%</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:110px;">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Total P&amp;L</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Syne',sans-serif; color:{pnl_color};">${total_pnl:+.2f}</div>
  </div>
  <div class="stat-box" style="flex:1; min-width:110px;" title="Closing Line Value — how much better than closing odds you got on average. Positive = sharp betting.">
    <div style="font-size:0.72rem; color:{_c['muted']}; font-weight:600; text-transform:uppercase; letter-spacing:0.07em; margin-bottom:6px;">Avg CLV ⓘ</div>
    <div style="font-size:2rem; font-weight:800; font-family:'Syne',sans-serif; color:{clv_color};">{avg_clv:+.2f}%</div>
  </div>
</div>""", unsafe_allow_html=True)

    # --- Full Bet Log ---
    st.subheader("Full Bet Log")
    display = bets_raw[["game_date", "away_team", "home_team", "bet_on", "odds",
                         "stake", "outcome", "profit_loss", "closing_odds", "clv", "notes"]].copy()
    display.columns = ["Date", "Away Team", "Home Team", "Bet On (Team)", "Odds", "Stake ($)",
                       "Outcome", "P&L ($)", "Closing Odds", "CLV", "Notes"]
    st.dataframe(display, use_container_width=True, hide_index=True)

    st.divider()

    # Running P&L chart
    st.subheader("Running P&L")
    completed_sorted = completed.sort_values("game_date")
    completed_sorted["running_pnl"] = completed_sorted["profit_loss"].cumsum()
    completed_sorted["running_roi"] = (completed_sorted["running_pnl"] / completed_sorted["stake"].cumsum() * 100)

    fig = go.Figure()
    colors = ["#22d47a" if v >= 0 else "#f05252" for v in completed_sorted["running_pnl"]]
    fig.add_trace(go.Scatter(
        x=list(range(1, len(completed_sorted) + 1)),
        y=completed_sorted["running_pnl"],
        mode="lines+markers",
        line=dict(color="#22d47a", width=2),
        marker=dict(color=colors, size=8),
        name="Cumulative P&L ($)"
    ))
    _tmpl = "plotly_dark" if _c["bg"] == "#07080f" else "plotly"
    fig.add_hline(y=0, line_dash="dash", line_color="gray")
    fig.update_layout(
        template=_tmpl,
        paper_bgcolor=_c["plot_paper"],
        plot_bgcolor=_c["plot_bg"],
        font=dict(family="Syne", color=_c["plot_font"]),
        xaxis_title="Bet #",
        yaxis_title="P&L ($)",
        height=350,
    )
    st.plotly_chart(fig, use_container_width=True)

    # CLV Distribution
    if completed["clv"].notna().sum() >= 5:
        st.subheader("Closing Line Value Distribution")
        st.caption("If your avg CLV is positive over 50+ bets, your model has a real edge — regardless of short-term results.")
        fig2 = go.Figure(go.Histogram(
            x=(completed["clv"] * 100).dropna(),
            nbinsx=20,
            marker_color=_c["plot_blue"],
            opacity=0.8,
        ))
        fig2.add_vline(x=0, line_color="gray", line_dash="dash")
        fig2.add_vline(x=avg_clv, line_color=_c["plot_amber"], line_width=2, annotation_text=f"Avg: {avg_clv:+.2f}%")
        fig2.update_layout(
            template=_tmpl,
            paper_bgcolor=_c["plot_paper"],
            plot_bgcolor=_c["plot_bg"],
            xaxis_title="CLV (%)",
            yaxis_title="Count",
            height=300,
            font=dict(family="Syne", color=_c["plot_font"]),
        )
        st.plotly_chart(fig2, use_container_width=True)
