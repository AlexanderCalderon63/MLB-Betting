"""ui.py — responsive render helpers shared across pages.

Pairs with theme.py: theme.py owns the design tokens and CSS (including the mobile
breakpoints); this module owns the Python-side render logic that produces
mobile-friendly Plotly charts and tables. Colors always come from theme.palette()
— never hardcode hex here.

Two helpers:

    responsive_chart(fig, key)   Locks the chart's axes so it never hijacks page
                                 scroll on touch devices, hides the modebar, and
                                 styles tooltips. A "⤢ Zoom & pan" toggle swaps in
                                 an unlocked, taller copy for detailed inspection
                                 (rendered only when on — no wasted Plotly payload).

    responsive_table(df, key)    Renders a DataFrame as a styled HTML table that the
                                 theme CSS folds into stacked label/value cards on
                                 phones. Use instead of st.dataframe when a table
                                 should read well on mobile (st.dataframe is a canvas
                                 grid and cannot reflow).
"""

from __future__ import annotations

import html

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from theme import palette

# Inline view: no interaction, so the page scrolls naturally past it on touch.
_LOCKED_CONFIG = {
    "displayModeBar": False,
    "scrollZoom": False,
    "doubleClick": False,
    "displaylogo": False,
    "responsive": True,
    "staticPlot": False,  # keep hover tooltips alive
}

# Expanded view: full pan/zoom for deliberate inspection.
_INTERACTIVE_CONFIG = {
    "scrollZoom": True,
    "displaylogo": False,
    "responsive": True,
    "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d", "toggleSpikelines"],
}


def _apply_common(fig: go.Figure) -> None:
    """Styling shared by both views: tight margins, tabular tooltip, no menubar."""
    c = palette()
    title_present = bool(getattr(fig.layout.title, "text", None))
    fig.update_layout(
        autosize=True,
        margin=dict(l=8, r=14, t=46 if title_present else 18, b=8),
        hoverlabel=dict(
            bgcolor=c["surface"],
            bordercolor=c["border2"],
            font=dict(family="Inter", color=c["text"], size=13),
        ),
    )
    # automargin lets axis titles claim space back despite the tight margins above.
    fig.update_xaxes(automargin=True)
    fig.update_yaxes(automargin=True)


def responsive_chart(
    fig: go.Figure,
    key: str,
    *,
    height: int | None = None,
    expandable: bool = True,
    interactive_height: int = 560,
    zoom_label: str = "⤢  Zoom & pan",
) -> None:
    """Render a Plotly figure that behaves well on touch screens.

    The inline chart has both axes locked (fixedrange) so dragging scrolls the page
    instead of panning the plot. Flip the toggle to get an unlocked, taller copy.
    """
    _apply_common(fig)
    if height is not None:
        fig.update_layout(height=height)

    interactive = False
    if expandable:
        interactive = st.toggle(
            zoom_label,
            key=f"{key}__zoom",
            value=False,
            help="Unlock pinch-zoom and panning for a closer look. The page won't scroll while you interact.",
        )

    if interactive:
        big = go.Figure(fig)
        big.update_xaxes(fixedrange=False)
        big.update_yaxes(fixedrange=False)
        big.update_layout(height=interactive_height, dragmode="pan")
        st.plotly_chart(big, use_container_width=True, config=_INTERACTIVE_CONFIG, key=f"{key}__big")
        st.markdown(
            '<p class="chart-hint">Drag to pan · scroll or pinch to zoom · double-click to reset</p>',
            unsafe_allow_html=True,
        )
    else:
        locked = go.Figure(fig)
        locked.update_xaxes(fixedrange=True)
        locked.update_yaxes(fixedrange=True)
        locked.update_layout(dragmode=False)
        st.plotly_chart(locked, use_container_width=True, config=_LOCKED_CONFIG, key=f"{key}__locked")


def _fmt_cell(value) -> str:
    """Display-ready string for a cell; blanks out missing values."""
    try:
        if value is None or pd.isna(value):
            return "—"
    except (TypeError, ValueError):
        pass  # non-scalar (list/array) — fall through and stringify
    return html.escape(str(value))


def _cell_class(raw: str, numeric: bool, color_signed: bool) -> str:
    classes = []
    if numeric:
        classes.append("num")
    if color_signed:
        s = raw.strip()
        if s.startswith("+") or (s.startswith("$") and "-" not in s and s not in ("$0", "$0.00")):
            classes.append("pos")
        elif s.startswith("-") or s.startswith("-$") or s.startswith("$-"):
            classes.append("neg")
    return f' class="{" ".join(classes)}"' if classes else ""


def responsive_table(
    df: pd.DataFrame,
    *,
    key: str | None = None,
    numeric_cols: list[str] | None = None,
    signed_cols: list[str] | None = None,
) -> None:
    """Render a DataFrame as a responsive HTML table (cards on phones).

    numeric_cols  right-align + monospace these columns (defaults to dtype sniff).
    signed_cols   tint values green/red by leading +/− (e.g. P&L, edge, ROI).
    """
    if df is None or len(df) == 0:
        st.caption("No data to display.")
        return

    cols = list(df.columns)
    if numeric_cols is None:
        numeric_cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    numeric_set = set(numeric_cols)
    signed_set = set(signed_cols or [])

    head_cells = []
    for c in cols:
        th_cls = ' class="num"' if c in numeric_set else ""
        head_cells.append(f"<th{th_cls}>{html.escape(str(c))}</th>")
    head = "".join(head_cells)

    body_rows = []
    for _, row in df.iterrows():
        cells = []
        for c in cols:
            raw = _fmt_cell(row[c])
            cls = _cell_class(raw, c in numeric_set, c in signed_set)
            label = html.escape(str(c))
            cells.append(f'<td data-label="{label}"{cls}>{raw}</td>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    table_html = (
        '<div class="rtable-wrap">'
        f'<table class="rtable"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body_rows)}</tbody></table></div>'
    )
    st.markdown(table_html, unsafe_allow_html=True)
