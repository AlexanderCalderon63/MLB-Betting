"""
auth.py — user accounts, login gate, and per-user data scoping.

Self-contained authentication for the multi-user app, in the same spirit as
bankroll.py (storage + gate + UI in one file):

  • storage      the `users` table — username, a salted one-way password hash
                 (NOT reversible encryption — passwords are never recoverable),
                 role ('admin' | 'user'), and a security Q&A for self-service
                 password reset. See database.py for the schema.
  • gate         require_login() blocks every page until the visitor signs in,
                 and auto-logs-out after 10 minutes of inactivity (1.1.2). Once
                 authenticated it's a pure session_state read — no DB cost per
                 rerun, mirroring bankroll.require_balance().
  • scoping      selected_user_id() + user_clause() turn the logged-in identity
                 into a WHERE filter so each user sees only their own rows. The
                 admin sees everyone and gets a sidebar "Viewing data for"
                 picker that defaults to themselves (1.5.1).

Security notes (1.7): every query is parameterized with ? placeholders; the
only value ever interpolated into SQL text is an integer user id we generated,
never anything typed by a user. Passwords/answers are hashed with pbkdf2_hmac
(stdlib) — no third-party dependency, no plaintext at rest.

streamlit + theme are imported lazily inside the UI functions so the pure
logic (hashing, validation, clause builder) stays importable and self-checkable
anywhere — run `python auth.py` for the self-check.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import time

from database import get_connection

IDLE_SECONDS = 600           # auto-logout after 10 min of inactivity (1.1.2.1)
_PBKDF2_ROUNDS = 200_000     # stdlib pbkdf2; tune up over time, the tag stores it


# ── Password / answer hashing (stdlib, salted, one-way) ──────────────────────

def hash_secret(secret: str) -> str:
    """Salted pbkdf2 hash, stored as 'pbkdf2$rounds$salt$hash' — self-describing
    so the rounds can be raised later without breaking existing rows."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, _PBKDF2_ROUNDS)
    return f"pbkdf2${_PBKDF2_ROUNDS}${salt.hex()}${dk.hex()}"


def verify_secret(secret: str, stored: str | None) -> bool:
    """Constant-time check of a candidate against a stored hash_secret() value."""
    if not stored:
        return False
    try:
        algo, rounds, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", secret.encode(), bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _norm_answer(answer: str) -> str:
    """Security answers compare case/space-insensitively ('Boston' == ' boston ')."""
    return answer.strip().lower()


def password_problem(pw: str) -> str | None:
    """First reason a password is too weak, or None if it's strong enough (1.1.6)."""
    if len(pw) < 8:
        return "Use at least 8 characters."
    if not re.search(r"[a-z]", pw):
        return "Add a lowercase letter."
    if not re.search(r"[A-Z]", pw):
        return "Add an uppercase letter."
    if not re.search(r"\d", pw):
        return "Add a number."
    if not re.search(r"[^A-Za-z0-9]", pw):
        return "Add a symbol (e.g. ! ? # $)."
    return None


# ── Storage ──────────────────────────────────────────────────────────────────

def count_users() -> int:
    conn = get_connection()
    n = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    conn.close()
    return int(n)


def get_user(username: str) -> dict | None:
    """Case-insensitive username lookup (parameterized — no injection surface)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE LOWER(username) = LOWER(?)", (username.strip(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_users() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT id, username, role FROM users ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_user(username, password, *, role="user", email=None,
                security_question=None, security_answer=None) -> int:
    """Insert a user (password + answer hashed). The first user is the admin and
    adopts every pre-existing row of betting data (user_id IS NULL → admin), per
    1.3.7. Table names are fixed literals here — never user input."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role, email, "
        "security_question, security_answer_hash, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, TRUE)",
        (username.strip(), hash_secret(password), role, (email or "").strip() or None,
         (security_question or "").strip() or None,
         hash_secret(_norm_answer(security_answer)) if security_answer else None),
    )
    uid = cur.lastrowid
    if role == "admin":
        for table in ("bets", "paper_bets", "parlays", "bankroll"):
            conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id IS NULL", (uid,))
    conn.commit()
    conn.close()
    return int(uid)


def authenticate(username: str, password: str) -> dict | None:
    u = get_user(username)
    if not u or not u.get("is_active", True):
        return None
    return u if verify_secret(password, u["password_hash"]) else None


def get_security_question(username: str) -> str | None:
    u = get_user(username)
    return u.get("security_question") if u else None


def reset_password(username: str, answer: str, new_password: str) -> bool:
    """Verify the security answer, then set a new password. False if no match."""
    u = get_user(username)
    if not u or not verify_secret(_norm_answer(answer), u.get("security_answer_hash")):
        return False
    conn = get_connection()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                 (hash_secret(new_password), u["id"]))
    conn.commit()
    conn.close()
    return True


# ── Per-user data scoping ─────────────────────────────────────────────────────

def user_clause(uid: int | None, *, has_where: bool = False) -> tuple[str, tuple]:
    """SQL fragment + params to scope a query to one user. uid=None → no filter
    (the admin's "all users" view). The id is bound as a parameter, never
    interpolated, so this is injection-safe."""
    if uid is None:
        return "", ()
    return (" AND user_id = ?" if has_where else " WHERE user_id = ?"), (uid,)


def owner_clause() -> tuple[str, tuple]:
    """Extra guard for UPDATE/DELETE ... WHERE id = ?: a regular user can only
    touch their own rows; the admin may edit anyone's."""
    if is_admin():
        return "", ()
    return " AND user_id = ?", (current_user_id(),)


# ── Session identity (pure session_state — no DB) ────────────────────────────

def current_user() -> dict | None:
    import streamlit as st
    return st.session_state.get("_auth_user")


def current_user_id() -> int | None:
    u = current_user()
    return u["id"] if u else None


def is_admin() -> bool:
    u = current_user()
    return bool(u and u["role"] == "admin")


def _login(u: dict) -> None:
    import streamlit as st
    st.session_state["_auth_user"] = {
        "id": u["id"], "username": u["username"], "role": u["role"], "email": u.get("email"),
    }
    st.session_state["_auth_last_active"] = time.time()
    # Force a fresh per-user bankroll check on next page (balance is per user, 1.4).
    for k in ("_bankroll_ok", "_admin_filter_uid"):
        st.session_state.pop(k, None)


def logout() -> None:
    import streamlit as st
    for k in ("_auth_user", "_auth_last_active", "_bankroll_ok", "_admin_filter_uid"):
        st.session_state.pop(k, None)
    st.rerun()


# ── The gate ──────────────────────────────────────────────────────────────────

def require_login() -> None:
    """Block the page until signed in; enforce the 10-min idle timeout.

    Call right after init_theme() on every page. Logged in and active → returns
    after a session_state read (plus the cheap sidebar account card + role CSS).
    Idle too long, or not signed in → renders the auth screen and st.stop()s.
    """
    import streamlit as st
    u = st.session_state.get("_auth_user")
    now = time.time()
    if u:
        if now - st.session_state.get("_auth_last_active", now) > IDLE_SECONDS:
            for k in ("_auth_user", "_auth_last_active", "_bankroll_ok", "_admin_filter_uid"):
                st.session_state.pop(k, None)
            st.session_state["_auth_expired"] = True
        else:
            st.session_state["_auth_last_active"] = now
            _render_account_sidebar()
            _hide_admin_pages()
            return
    _render_auth_screen()
    st.stop()


# ── Auth UI ───────────────────────────────────────────────────────────────────

def _hide_admin_pages() -> None:
    """Hide the Model Performance nav entry from non-admins (1.5.1). The page
    itself also hard-stops non-admins; this just keeps it out of the sidebar.
    ponytail: CSS match on the nav href — if Streamlit changes its nav markup,
    switch to st.navigation with a role-filtered page list."""
    import streamlit as st
    if not is_admin():
        st.markdown(
            "<style>[data-testid='stSidebarNav'] a[href$='Model_Performance']{display:none!important;}</style>",
            unsafe_allow_html=True,
        )


def _render_account_sidebar() -> None:
    """Signed-in identity card + Sign out, pinned in the sidebar on every page (1.2)."""
    import streamlit as st
    from theme import palette
    c = palette()
    u = st.session_state["_auth_user"]
    role_label = "Admin" if u["role"] == "admin" else "Member"
    initial = (u["username"][:1] or "?").upper()
    with st.sidebar:
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:0.6rem;background:{c["surface2"]};'
            f'border:1px solid {c["border"]};border-radius:14px;padding:0.6rem 0.75rem;margin:0.4rem 0 0.5rem;">'
            f'<div style="flex:0 0 auto;width:2.1rem;height:2.1rem;border-radius:50%;'
            f'background:linear-gradient(135deg,{c["accent"]},{c["accent2"]});color:#fff;'
            f'display:flex;align-items:center;justify-content:center;font-family:\'Manrope\',sans-serif;'
            f'font-weight:800;font-size:0.95rem;">{initial}</div>'
            f'<div style="min-width:0;line-height:1.2;">'
            f'<div style="font-weight:700;color:{c["text"]};font-size:0.86rem;white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis;">{u["username"]}</div>'
            f'<div style="font-family:\'Space Mono\',monospace;font-size:0.6rem;text-transform:uppercase;'
            f'letter-spacing:0.08em;color:{c["muted"]};">{role_label}</div></div></div>',
            unsafe_allow_html=True,
        )
        if st.button("Sign out", key="_logout_btn", use_container_width=True):
            logout()


def _render_auth_screen() -> None:
    """The signed-out experience: a centered, on-theme auth card. First run (no
    users yet) shows only the one-time admin setup; afterwards it's
    Sign in / Create account / Forgot password."""
    import streamlit as st
    from theme import palette
    c = palette()

    # Dedicated screen: hide the page nav/sidebar so login reads as its own moment.
    st.markdown(
        "<style>section[data-testid='stSidebar']{display:none!important;}</style>",
        unsafe_allow_html=True,
    )

    bootstrap = count_users() == 0
    _, mid, _ = st.columns([1, 1.5, 1])
    with mid:
        if st.session_state.pop("_auth_expired", False):
            st.info("⏱️ Signed out after 10 minutes of inactivity. Sign in to pick up where you left off.")

        eyebrow = "Set up · Admin account" if bootstrap else "⚾ MLB Value Finder"
        title = "Create the admin account" if bootstrap else "Welcome back."
        sub = (
            "You're the first one here. This account runs the show — full access to every "
            "page and every user's data."
            if bootstrap else
            "Sign in to your bets, bankroll, and today's value board."
        )
        st.markdown(
            f'<span class="eyebrow-pill">{eyebrow}</span>'
            f'<div style="font-family:\'Manrope\',sans-serif;font-weight:800;font-size:1.9rem;'
            f'line-height:1.08;letter-spacing:-0.03em;color:{c["text"]};margin:0.5rem 0 0.4rem;">{title}</div>'
            f'<p style="color:{c["text2"]};font-size:0.96rem;line-height:1.55;margin:0 0 0.9rem;">{sub}</p>',
            unsafe_allow_html=True,
        )

        if bootstrap:
            _register_form(is_admin_setup=True)
        else:
            tab_in, tab_new, tab_reset = st.tabs(["Sign in", "Create account", "Forgot password"])
            with tab_in:
                _signin_form()
            with tab_new:
                _register_form(is_admin_setup=False)
            with tab_reset:
                _reset_form()

        st.markdown(
            f'<p style="text-align:center;color:{c["muted"]};font-size:0.72rem;margin-top:0.8rem;">'
            f'Bet responsibly · Not financial advice</p>',
            unsafe_allow_html=True,
        )


def _signin_form() -> None:
    import streamlit as st
    with st.form("signin"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary", use_container_width=True):
            u = authenticate(username, password)
            if u:
                _login(u)
                st.rerun()
            else:
                st.error("That username and password don't match. Try again, or reset your password.")


def _prefill_email() -> str:
    """Best-effort: the Streamlit-authenticated viewer's email (set when the app
    is shared privately on Community Cloud). Just a convenience prefill."""
    import streamlit as st
    try:
        return st.experimental_user.get("email") or ""
    except Exception:
        return ""


def _register_form(*, is_admin_setup: bool) -> None:
    import streamlit as st
    label = "Create admin account" if is_admin_setup else "Create account"
    with st.form("register", clear_on_submit=False):
        username = st.text_input("Username")
        email = st.text_input("Email (optional)", value="" if is_admin_setup else _prefill_email())
        password = st.text_input("Password", type="password",
                                 help="8+ chars with upper, lower, a number, and a symbol.")
        confirm = st.text_input("Confirm password", type="password")
        st.caption("For password recovery — you'll answer this if you forget your password.")
        question = st.text_input("Security question", placeholder="e.g. First team you ever bet on?")
        answer = st.text_input("Answer", type="password")
        if st.form_submit_button(label, type="primary", use_container_width=True):
            problem = password_problem(password)
            if not username.strip():
                st.error("Pick a username.")
            elif get_user(username):
                st.error("That username is taken. Try another.")
            elif problem:
                st.error(problem)
            elif password != confirm:
                st.error("Those passwords don't match.")
            elif not question.strip() or not answer.strip():
                st.error("Set a security question and answer so you can recover your account.")
            else:
                create_user(
                    username, password,
                    role="admin" if is_admin_setup else "user",
                    email=email, security_question=question, security_answer=answer,
                )
                _login(authenticate(username, password))
                st.rerun()


def _reset_form() -> None:
    import streamlit as st
    username = st.text_input("Username", key="_reset_user")
    if not username:
        st.caption("Enter your username to see your security question.")
        return
    question = get_security_question(username)
    if not question:
        st.info("No account with a recoverable security question matches that username.")
        return
    st.caption(f"Security question: **{question}**")
    with st.form("reset"):
        answer = st.text_input("Your answer", type="password")
        new1 = st.text_input("New password", type="password")
        new2 = st.text_input("Confirm new password", type="password")
        if st.form_submit_button("Reset password", type="primary", use_container_width=True):
            problem = password_problem(new1)
            if problem:
                st.error(problem)
            elif new1 != new2:
                st.error("Those passwords don't match.")
            elif reset_password(username, answer, new1):
                st.success("Password updated. Head to Sign in with your new password.")
            else:
                st.error("That answer doesn't match what's on file for this account.")


# ── Admin per-user filter ─────────────────────────────────────────────────────

def selected_user_id() -> int | None:
    """Which user's data this page should show. Regular users always get their
    own id. The admin gets a sidebar picker (default: themselves, 1.5.1) and may
    choose any single user or "All users" (returns None → unfiltered)."""
    if not is_admin():
        return current_user_id()

    import streamlit as st
    from theme import palette
    c = palette()
    me = current_user_id()
    users = list_users()

    labels = {None: "👥 All users"}
    options = [me, None]
    for u in users:
        labels[u["id"]] = ("⭐ " if u["id"] == me else "") + u["username"]
        if u["id"] != me:
            options.append(u["id"])

    with st.sidebar:
        st.markdown(
            f'<div style="font-family:\'Space Mono\',monospace;font-size:0.62rem;font-weight:700;'
            f'letter-spacing:0.1em;text-transform:uppercase;color:{c["muted"]};margin:0.3rem 0 0.2rem;">'
            f'Admin · Viewing data for</div>',
            unsafe_allow_html=True,
        )
        return st.selectbox(
            "Viewing data for", options, format_func=lambda i: labels.get(i, str(i)),
            key="_admin_filter_uid", label_visibility="collapsed",
        )


if __name__ == "__main__":
    # ponytail: security-path self-check — runs without a DB.
    h = hash_secret("Sup3r$ecret")
    assert verify_secret("Sup3r$ecret", h)
    assert not verify_secret("wrong", h)
    assert verify_secret(_norm_answer(" Boston "), hash_secret(_norm_answer("boston")))
    assert hash_secret("x") != hash_secret("x")          # random salt per call
    assert password_problem("short1!A") is None
    for bad in ("Sh0rt!", "alllower1!", "ALLUPPER1!", "NoDigits!", "NoSymbol1"):
        assert password_problem(bad) is not None, bad
    assert user_clause(None) == ("", ())
    assert user_clause(7) == (" WHERE user_id = ?", (7,))
    assert user_clause(7, has_where=True) == (" AND user_id = ?", (7,))
    print("auth self-check OK")
