# customer_app.py

import io
from datetime import datetime, date
from calendar import monthrange

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar
from supabase import create_client, Client

# ---------------- Config ----------------
st.set_page_config(page_title="Customer Order Portal", layout="wide")

REQUIRED_COLS = [
    "WO", "Quote", "PO Number", "Status",
    "Customer Name", "Model Description", "Scheduled Date", "Price",
]

SUPABASE_TABLE = "order_book"

STATUS_COLORS = {
    "open":        {"backgroundColor": "#2563eb", "borderColor": "#1d4ed8", "textColor": "#ffffff"},
    "in progress": {"backgroundColor": "#d97706", "borderColor": "#b45309", "textColor": "#ffffff"},
    "completed":   {"backgroundColor": "#16a34a", "borderColor": "#15803d", "textColor": "#ffffff"},
    "on hold":     {"backgroundColor": "#6b7280", "borderColor": "#4b5563", "textColor": "#ffffff"},
    "cancelled":   {"backgroundColor": "#dc2626", "borderColor": "#b91c1c", "textColor": "#ffffff"},
    "default":     {"backgroundColor": "#0f766e", "borderColor": "#115e59", "textColor": "#ffffff"},
}

SOLD_COLORS = {"backgroundColor": "#cbd5e1", "borderColor": "#94a3b8", "textColor": "#475569"}

STATUS_KEYWORDS = {
    "open":        ["open", "new", "pending"],
    "in progress": ["in progress", "inprogress", "wip", "started", "working"],
    "completed":   ["completed", "complete", "done", "closed", "shipped", "delivered"],
    "on hold":     ["on hold", "hold", "paused", "waiting"],
    "cancelled":   ["cancelled", "canceled", "void"],
}


def normalize_status_key(status: str) -> str:
    import re
    raw = str(status or "").strip().lower()
    if not raw:
        return "default"
    compact = re.sub(r"[^a-z0-9]+", " ", raw).strip()
    if compact in STATUS_COLORS:
        return compact
    for canonical, keywords in STATUS_KEYWORDS.items():
        if any(k in compact for k in keywords):
            return canonical
    return "default"


def status_to_colors(status: str) -> dict:
    return STATUS_COLORS[normalize_status_key(status)]


# ================================================================
#  AUTH
#  Credentials live in .streamlit/secrets.toml under [customers]
#
#  Example secrets.toml layout:
#
#  [customers.user1]
#  password       = "hunter2"
#  customer_names = ["Acme Corp", "Beta Industries"]
#
#  [customers.user2]
#  password       = "letmein"
#  customer_names = ["Beta Industries"]
#
#  customer_names values MUST match exactly how names appear
#  in the Customer Name column of your order book.
# ================================================================

def verify_login(username: str, password: str) -> list[str] | None:
    """
    Checks credentials against st.secrets['customers'].
    Returns a list of customer names the user can see, or None if invalid.
    Supports both legacy single `customer_name` and new `customer_names` list.
    """
    try:
        customers_cfg = st.secrets.get("customers", {})
    except Exception:
        customers_cfg = {}

    entry = customers_cfg.get(username.strip())
    if not entry or entry.get("password") != password:
        return None

    # New list format
    if "customer_names" in entry:
        names = entry["customer_names"]
        return [names] if isinstance(names, str) else list(names)

    # Legacy single customer_name (backwards compatible)
    if "customer_name" in entry:
        return [entry["customer_name"]]

    return None


def show_login_screen():
    """Renders a centered login card. Stops execution until successful login."""

    st.markdown(
        """
        <style>
        [data-testid="stAppViewContainer"] > .main {
            background: linear-gradient(135deg, #1e3a5f 0%, #2563eb 100%);
            min-height: 100vh;
        }
        [data-testid="collapsedControl"] { display: none; }
        section[data-testid="stSidebar"] { display: none; }
        .login-card {
            background: white;
            padding: 2.5rem 3rem 2rem 3rem;
            border-radius: 18px;
            box-shadow: 0 24px 64px rgba(0,0,0,0.35);
            width: 100%;
            max-width: 420px;
        }
        .login-icon  { text-align: center; font-size: 3rem; margin-bottom: 4px; }
        .login-title { text-align: center; font-size: 1.45rem; font-weight: 700;
                       color: #1e3a5f; margin: 0; }
        .login-sub   { text-align: center; font-size: 0.85rem; color: #64748b;
                       margin: 6px 0 1.8rem 0; }
        .footer-note { text-align: center; color: rgba(255,255,255,0.55);
                       font-size: 0.78rem; margin-top: 1.5rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    _, mid, _ = st.columns([1, 2.2, 1])
    with mid:
        st.markdown('<div class="login-card">', unsafe_allow_html=True)
        st.markdown('<div class="login-icon">🏭</div>', unsafe_allow_html=True)
        st.markdown('<div class="login-title">Customer Portal</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="login-sub">Sign in to view your production schedule</div>',
            unsafe_allow_html=True,
        )

        username = st.text_input("Username", placeholder="Enter your username", key="li_user")
        password = st.text_input("Password", placeholder="Enter your password",
                                 type="password", key="li_pass")

        if st.button("Sign In →", type="primary", use_container_width=True):
            if not username.strip() or not password:
                st.error("Please enter both username and password.")
            else:
                customer_names = verify_login(username, password)
                if customer_names:
                    st.session_state.authenticated       = True
                    st.session_state.logged_in_customers = customer_names
                    st.session_state.login_username      = username.strip()
                    st.session_state.customer_display    = ", ".join(customer_names)
                    st.rerun()
                else:
                    st.error("❌ Incorrect username or password.")

        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown(
        '<div class="footer-note">Contact your administrator if you need access.</div>',
        unsafe_allow_html=True,
    )


# ---------------- Supabase ----------------

@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


@st.cache_data(ttl=300)
def load_all_data() -> pd.DataFrame:
    """Fetch full order book from Supabase (cached 5 min)."""
    supabase = get_supabase()
    response = supabase.table(SUPABASE_TABLE).select("*").execute()
    rows = response.data

    if not rows:
        return pd.DataFrame(columns=REQUIRED_COLS)

    df = pd.DataFrame(rows)
    df = df.rename(columns={
        "wo": "WO", "quote": "Quote", "po_number": "PO Number",
        "status": "Status", "customer_name": "Customer Name",
        "model_description": "Model Description",
        "scheduled_date": "Scheduled Date", "price": "Price",
    })
    df = df.drop(columns=[c for c in ["uploaded_name", "id"] if c in df.columns], errors="ignore")
    df["Scheduled Date"] = df["Scheduled Date"].apply(_parse_date)
    df["Price"] = df["Price"].apply(lambda x: float(x) if x is not None else pd.NA)
    for c in ["Quote", "PO Number", "Status", "Customer Name", "Model Description"]:
        df[c] = df[c].fillna("").astype(str)

    present = [c for c in REQUIRED_COLS if c in df.columns]
    return df[present]


# ---------------- Helpers ----------------

def _parse_date(x):
    if x is None or str(x).strip() in ("", "None", "NaT"):
        return pd.NaT
    try:
        if pd.isna(x):
            return pd.NaT
    except Exception:
        pass
    dt = pd.to_datetime(x, errors="coerce")
    return pd.NaT if pd.isna(dt) else dt.date()


def _is_mine(cust_col_value: str, my_customers: list[str]) -> bool:
    """True if this row belongs to any of the logged-in user's customers."""
    val = cust_col_value.strip().lower()
    return any(val == c.strip().lower() for c in my_customers)


def df_to_calendar_events(df: pd.DataFrame, my_customers: list[str]):
    """
    Own orders → full detail with status colour.
    All other booked dates → gray SOLD block (no details leaked).
    """
    events = []
    for _, r in df.iterrows():
        wo  = str(r.get("WO", "")).strip()
        d   = r.get("Scheduled Date", pd.NaT)
        if not wo or pd.isna(d):
            continue

        cust   = str(r.get("Customer Name", "")).strip()
        model  = str(r.get("Model Description", "")).strip()
        status = str(r.get("Status", "")).strip()

        if _is_mine(cust, my_customers):
            title = " | ".join(filter(None, [wo, cust]))
            if model:
                title += f" — {model}"
            events.append({
                "id": wo, "title": title,
                "start": d.isoformat(), "allDay": True,
                **status_to_colors(status),
                "extendedProps": {
                    "wo": wo, "customer_name": cust,
                    "model_description": model, "status": status,
                },
            })
        else:
            events.append({
                "id": f"sold_{wo}",
                "title": "● SOLD",
                "start": d.isoformat(), "allDay": True,
                **SOLD_COLORS,
                "extendedProps": {"sold": True},
            })

    return events


def build_excel_bytes(df: pd.DataFrame) -> bytes:
    out = df.copy()
    out["Scheduled Date"] = pd.to_datetime(out["Scheduled Date"], errors="coerce")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        out.to_excel(writer, index=False, sheet_name="My Orders")
        ws = writer.sheets["My Orders"]
        col_map = {cell.value: cell.column for cell in ws[1]}
        dc = col_map.get("Scheduled Date")
        pc = col_map.get("Price")
        if dc:
            for r in range(2, ws.max_row + 1):
                ws.cell(row=r, column=dc).number_format = "yyyy-mm-dd"
        if pc:
            for r in range(2, ws.max_row + 1):
                ws.cell(row=r, column=pc).number_format = '"$"#,##0.00'
        for col in range(1, ws.max_column + 1):
            max_len = max(
                (len(str(ws.cell(row=r, column=col).value or "")) for r in range(1, ws.max_row + 1)),
                default=10,
            )
            ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = min(max(10, max_len + 2), 60)
    return buf.getvalue()


def generate_monthly_print_view(df: pd.DataFrame, month: int, year: int, my_customers: list[str]) -> str:
    """Printable HTML calendar: own orders shown in full, all others shown as SOLD."""
    month_label     = datetime(year, month, 1).strftime("%B %Y")
    customers_label = ", ".join(my_customers)

    df_month = df[
        (pd.to_datetime(df["Scheduled Date"], errors="coerce").dt.month == month) &
        (pd.to_datetime(df["Scheduled Date"], errors="coerce").dt.year == year)
    ].copy()

    my_events: dict[str, list] = {}
    sold_dates: set[str] = set()

    for _, row in df_month.iterrows():
        d = row["Scheduled Date"]
        if pd.isna(d):
            continue
        dk   = d.isoformat()
        cust = str(row.get("Customer Name", "")).strip()
        if _is_mine(cust, my_customers):
            my_events.setdefault(dk, []).append({
                "wo":     str(row.get("WO", "")).strip(),
                "cust":   cust,
                "model":  str(row.get("Model Description", "")).strip(),
                "status": str(row.get("Status", "")).strip(),
            })
        else:
            sold_dates.add(dk)

    fdw        = (datetime(year, month, 1).weekday() + 1) % 7
    num_days   = monthrange(year, month)[1]
    weeks_need = ((num_days + fdw - 1) // 7) + 1

    html = f"""<!DOCTYPE html>
<html>
<head>
<title>{month_label} — {customers_label}</title>
<style>
@media print {{ @page {{ size: letter landscape; margin: 0.4in; }} body {{ margin:0; }} }}
* {{ -webkit-print-color-adjust:exact !important; print-color-adjust:exact !important; }}
body {{ font-family:Arial,sans-serif; padding:10px; background:white; max-width:10.2in; margin:0 auto; }}
.header {{ text-align:center; margin-bottom:10px; }}
.header h2 {{ margin:0; font-size:16px; color:#1e3a5f; font-weight:700; }}
.header .sub {{ font-size:12px; color:#64748b; margin-top:4px; }}
table {{ width:100%; border-collapse:collapse; table-layout:fixed; }}
th {{ background:#2563eb; color:white; padding:6px; text-align:center;
      border:1px solid #999; font-size:11px; width:14.28%; }}
td {{ border:1px solid #ccc; padding:5px; vertical-align:top; width:14.28%;
      height:110px; background:white; }}
td.sold {{ background:#f8fafc; }}
.dn {{ font-weight:bold; font-size:12px; color:#333; margin-bottom:4px; }}
.ev {{ margin-bottom:4px; padding:4px; border-radius:3px; font-size:9px; line-height:1.3; }}
.s-open       {{ background:#dbeafe; border-left:3px solid #2563eb; }}
.s-inprogress {{ background:#fed7aa; border-left:3px solid #d97706; }}
.s-completed  {{ background:#dcfce7; border-left:3px solid #16a34a; }}
.s-onhold     {{ background:#e5e7eb; border-left:3px solid #6b7280; }}
.s-cancelled  {{ background:#fee2e2; border-left:3px solid #dc2626; }}
.s-default    {{ background:#ccfbf1; border-left:3px solid #0f766e; }}
.wo  {{ font-weight:bold; font-size:10px; color:#000; }}
.cu  {{ font-size:9.5px; color:#1f2937; font-weight:500; }}
.md  {{ font-size:9px; color:#374151; }}
.sold-badge {{ text-align:center; margin-top:8px; padding:5px; background:#cbd5e1;
               color:#475569; border-radius:3px; font-weight:bold; font-size:10px; }}
.legend {{ margin-top:12px; padding:8px 12px; background:#f9fafb;
           border:1px solid #ddd; border-radius:4px; }}
.lt {{ font-weight:bold; font-size:11px; margin-bottom:6px; }}
.li {{ display:inline-flex; align-items:center; gap:5px; font-size:10px; margin-right:12px; }}
.lc {{ width:14px; height:14px; border-radius:2px; display:inline-block; }}
</style>
</head>
<body>
<div class="header">
  <h2>{month_label}</h2>
  <div class="sub">Production Schedule — <strong>{customers_label}</strong></div>
</div>
<table>
<thead><tr>
  <th>Sunday</th><th>Monday</th><th>Tuesday</th>
  <th>Wednesday</th><th>Thursday</th><th>Friday</th><th>Saturday</th>
</tr></thead>
<tbody>"""

    cur = 1
    for week in range(weeks_need):
        html += "<tr>"
        for dow in range(7):
            if week == 0 and dow < fdw:
                html += "<td></td>"
            elif cur > num_days:
                html += "<td></td>"
            else:
                dk      = date(year, month, cur).isoformat()
                is_sold = (dk in sold_dates) and (dk not in my_events)
                td_cls  = ' class="sold"' if is_sold else ''
                html += f'<td{td_cls}><div class="dn">{cur}</div>'
                if dk in my_events:
                    for ev in my_events[dk]:
                        sk = normalize_status_key(ev["status"]).replace(" ", "")
                        html += f'<div class="ev s-{sk}"><div class="wo">WO: {ev["wo"]}</div>'
                        if ev["cust"]:
                            html += f'<div class="cu">{ev["cust"]}</div>'
                        if ev["model"]:
                            html += f'<div class="md">{ev["model"]}</div>'
                        html += '</div>'
                elif is_sold:
                    html += '<div class="sold-badge">SOLD</div>'
                html += '</td>'
                cur += 1
        html += "</tr>"

    html += """
</tbody></table>
<div class="legend"><div class="lt">Legend:</div>
  <div class="li"><span class="lc" style="background:#2563eb"></span> Open</div>
  <div class="li"><span class="lc" style="background:#d97706"></span> In Progress</div>
  <div class="li"><span class="lc" style="background:#16a34a"></span> Completed</div>
  <div class="li"><span class="lc" style="background:#6b7280"></span> On Hold</div>
  <div class="li"><span class="lc" style="background:#dc2626"></span> Cancelled</div>
  <div class="li"><span class="lc" style="background:#cbd5e1"></span> SOLD — Date Unavailable</div>
</div>
</body></html>"""

    return html


# ================================================================
#  SESSION STATE INIT
# ================================================================
for key, default in [
    ("authenticated",       False),
    ("logged_in_customers", []),
    ("customer_display",    ""),
    ("login_username",      None),
    ("df_version",          0),
    ("show_print_preview",  False),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ================================================================
#  AUTH GATE
# ================================================================
if not st.session_state.authenticated:
    show_login_screen()
    st.stop()

# ================================================================
#  AUTHENTICATED SECTION
# ================================================================
my_customers: list[str] = st.session_state.logged_in_customers
customer_display: str   = st.session_state.customer_display

df_all = load_all_data()
my_df  = df_all[
    df_all["Customer Name"].str.strip().str.lower().isin(
        [c.strip().lower() for c in my_customers]
    )
].copy()


# ---- Sidebar ----
with st.sidebar:
    st.markdown(f"### 👤 {customer_display}")
    st.caption(f"Signed in as `{st.session_state.login_username}`")
    if len(my_customers) > 1:
        st.caption("**Viewing orders for:**")
        for c in my_customers:
            st.caption(f"• {c}")
    st.divider()
    st.caption("📖 Use the page selector to open **Table View**.")
    st.caption("🔒 Read-only — contact admin to make changes.")
    st.divider()
    if st.button("🚪 Log Out", use_container_width=True):
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# ---- Page title ----
st.title("📅 My Production Schedule")
st.caption(
    f"Showing orders for **{customer_display}**.  "
    "Gray **SOLD** blocks are dates already taken by other customers."
)

# ---- Calendar ----
events = df_to_calendar_events(df_all, my_customers)

calendar(
    events=events,
    options={
        "initialView": "dayGridMonth",
        "editable":    False,
        "selectable":  False,
        "height":      880,
        "eventDisplay": "block",
        "dayMaxEvents": False,
        "headerToolbar": {
            "left":   "today prev,next",
            "center": "title",
            "right":  "dayGridMonth,timeGridWeek,timeGridDay,listWeek",
        },
    },
    custom_css="""
        .fc .fc-daygrid-event { white-space: normal !important; cursor: default !important; }
        .fc .fc-event-title   { white-space: normal !important; overflow: visible !important;
                                text-overflow: clip !important; }
    """,
    key=f"cal_{st.session_state.df_version}",
)

st.divider()

# ---- Stats ----
c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    st.metric("My Orders", int(len(my_df)))
with c2:
    total_val = my_df["Price"].dropna().sum() if not my_df.empty else 0
    st.metric("Total Value", f"${total_val:,.2f}")
with c3:
    st.caption("🟦 Your orders   🔘 SOLD — unavailable dates")

st.caption("Status colours:  🔵 Open  🟠 In Progress  🟢 Completed  ⚫ On Hold  🔴 Cancelled")

# ---- Download ----
safe_name = customer_display.replace(" ", "_").replace(",", "").replace("/", "")
st.subheader("📥 Download My Orders")
st.download_button(
    "⬇️ Download Excel",
    data=build_excel_bytes(my_df),
    file_name=f"my_orders_{safe_name}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()

# ---- Print View ----
st.subheader("🖨️ Print Monthly Schedule")
st.caption("Generates a printable calendar: your orders + sold dates")

pc1, pc2, pc3 = st.columns([2, 2, 3])
with pc1:
    print_month = st.selectbox(
        "Month", range(1, 13),
        format_func=lambda x: datetime(2000, x, 1).strftime("%B"),
        index=datetime.now().month - 1, key="print_month",
    )
with pc2:
    print_year = st.number_input(
        "Year", min_value=2020, max_value=2030,
        value=datetime.now().year, key="print_year",
    )
with pc3:
    st.write("")
    if st.button("📄 Generate Print View", type="primary"):
        st.session_state.show_print_preview = True
        st.session_state.print_html = generate_monthly_print_view(
            df_all, print_month, print_year, my_customers
        )
        st.session_state.print_month_name = datetime(
            print_year, print_month, 1
        ).strftime("%B_%Y")

if st.session_state.show_print_preview:
    dl_col, hide_col = st.columns([1, 4])
    with dl_col:
        st.download_button(
            "💾 Download HTML to Print",
            data=st.session_state.print_html,
            file_name=f"schedule_{safe_name}_{st.session_state.print_month_name}.html",
            mime="text/html",
            help="Open in browser → Ctrl+P / Cmd+P",
        )
    with hide_col:
        if st.button("Hide Preview"):
            st.session_state.show_print_preview = False
            st.rerun()
    st.info("👁️ Preview — download HTML and open in browser for proper printing")
    st.components.v1.html(st.session_state.print_html, height=1000, scrolling=True)
