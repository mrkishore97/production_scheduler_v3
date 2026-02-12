# app.py

import hashlib
import io
import re
from datetime import datetime, date
from calendar import monthrange

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar
from supabase import create_client, Client

# ---------------- Config ----------------
st.set_page_config(page_title="Order Book Calendar", layout="wide")

REQUIRED_COLS = [
    "WO",
    "Quote",
    "PO Number",
    "Status",
    "Customer Name",
    "Model Description",
    "Scheduled Date",
    "Price",
]

SUPABASE_TABLE = "order_book"

COLUMN_ALIASES = {
    "wo": "WO", "work order": "WO", "workorder": "WO",
    "quote": "Quote", "quotation": "Quote",
    "po number": "PO Number", "po #": "PO Number", "po#": "PO Number",
    "ponumber": "PO Number", "purchase order": "PO Number",
    "status": "Status",
    "customer": "Customer Name", "customer name": "Customer Name",
    "client": "Customer Name", "client name": "Customer Name",
    "model description": "Model Description", "description": "Model Description",
    "model": "Model Description",
    "scheduled date": "Scheduled Date", "schedule date": "Scheduled Date",
    "scheduled": "Scheduled Date", "ship date": "Scheduled Date",
    "delivery date": "Scheduled Date", "date": "Scheduled Date",
    "price": "Price", "amount": "Price", "value": "Price",
}

STATUS_COLORS = {
    "open":       {"backgroundColor": "#2563eb", "borderColor": "#1d4ed8", "textColor": "#ffffff"},
    "in progress":{"backgroundColor": "#d97706", "borderColor": "#b45309", "textColor": "#ffffff"},
    "completed":  {"backgroundColor": "#16a34a", "borderColor": "#15803d", "textColor": "#ffffff"},
    "on hold":    {"backgroundColor": "#6b7280", "borderColor": "#4b5563", "textColor": "#ffffff"},
    "cancelled":  {"backgroundColor": "#dc2626", "borderColor": "#b91c1c", "textColor": "#ffffff"},
    "default":    {"backgroundColor": "#0f766e", "borderColor": "#115e59", "textColor": "#ffffff"},
}

STATUS_KEYWORDS = {
    "open":        ["open", "new", "pending"],
    "in progress": ["in progress", "inprogress", "wip", "started", "working"],
    "completed":   ["completed", "complete", "done", "closed", "shipped", "delivered"],
    "on hold":     ["on hold", "hold", "paused", "waiting"],
    "cancelled":   ["cancelled", "canceled", "void"],
}


def normalize_status_key(status: str) -> str:
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


# ---------------- Supabase ----------------

@st.cache_resource
def get_supabase() -> Client:
    """Cached Supabase client — created once per app lifetime."""
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


def save_data(df: pd.DataFrame, last_uploaded_name: str):
    """Replace all rows in Supabase with the current DataFrame (delete-all + insert)."""
    supabase = get_supabase()
    # Delete everything. We use neq on a column that always has a value.
    supabase.table(SUPABASE_TABLE).delete().neq("wo", "___never___").execute()

    if df.empty:
        return

    rows = []
    for _, r in df.iterrows():
        d = r.get("Scheduled Date", pd.NaT)
        price = r.get("Price", None)
        rows.append({
            "wo":                str(r.get("WO", "")).strip(),
            "quote":             str(r.get("Quote", "")),
            "po_number":         str(r.get("PO Number", "")),
            "status":            str(r.get("Status", "")),
            "customer_name":     str(r.get("Customer Name", "")),
            "model_description": str(r.get("Model Description", "")),
            "scheduled_date":    d.isoformat() if not pd.isna(d) else None,
            "price":             float(price) if price is not None and not pd.isna(price) else None,
            "uploaded_name":     last_uploaded_name or "",
        })

    # Insert in batches of 500 to stay within Supabase request limits
    for i in range(0, len(rows), 500):
        supabase.table(SUPABASE_TABLE).insert(rows[i : i + 500]).execute()


def load_data() -> tuple[pd.DataFrame, str | None]:
    """Fetch all rows from Supabase and return as a normalized DataFrame."""
    supabase = get_supabase()
    response = supabase.table(SUPABASE_TABLE).select("*").execute()
    rows = response.data

    if not rows:
        return pd.DataFrame(columns=REQUIRED_COLS), None

    df = pd.DataFrame(rows)
    last_name = df["uploaded_name"].iloc[0] if "uploaded_name" in df.columns else None

    df = df.rename(columns={
        "wo": "WO", "quote": "Quote", "po_number": "PO Number",
        "status": "Status", "customer_name": "Customer Name",
        "model_description": "Model Description",
        "scheduled_date": "Scheduled Date", "price": "Price",
    })
    df = df.drop(columns=[c for c in ["uploaded_name", "id"] if c in df.columns], errors="ignore")

    df["Scheduled Date"] = df["Scheduled Date"].apply(parse_date_to_date)
    df["Price"] = df["Price"].apply(lambda x: float(x) if x is not None else pd.NA)
    for c in ["Quote", "PO Number", "Status", "Customer Name", "Model Description"]:
        df[c] = df[c].fillna("").astype(str)

    present = [c for c in REQUIRED_COLS if c in df.columns]
    return df[present], last_name


# ---------------- Helpers ----------------

def clean_header(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip()).lower()


def parse_date_to_date(x):
    if x is None or str(x).strip() in ("", "None", "NaT"):
        return pd.NaT
    try:
        if pd.isna(x):
            return pd.NaT
    except Exception:
        pass
    dt = pd.to_datetime(x, errors="coerce")
    return pd.NaT if pd.isna(dt) else dt.date()


def parse_price_to_float(x):
    if x is None or str(x).strip() == "":
        return pd.NA
    try:
        if pd.isna(x):
            return pd.NA
    except Exception:
        pass
    s = re.sub(r"[^0-9.\-]", "", str(x).replace("$", "").replace(",", ""))
    try:
        return float(s)
    except Exception:
        return pd.NA


def standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = df.rename(columns={c: COLUMN_ALIASES.get(clean_header(c), str(c).strip()) for c in df.columns})
    df.columns = [str(c).strip() for c in df.columns]
    return df


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = standardize_columns(df)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        st.error("Your file is missing required columns.")
        st.write("**Missing:**", missing)
        st.write("**Found columns:**", list(df.columns))
        raise KeyError(f"Missing columns: {missing}")

    ordered = REQUIRED_COLS + [c for c in df.columns if c not in REQUIRED_COLS]
    df = df[ordered].copy()

    # Remove spreadsheet footer/blank rows before type coercion.
    df = df.dropna(how="all", subset=REQUIRED_COLS)

    text_cols = ["WO", "Quote", "PO Number", "Status", "Customer Name", "Model Description"]
    for c in text_cols:
        df[c] = df[c].where(df[c].notna(), "").astype(str).str.strip()
        df[c] = df[c].replace({"nan": "", "NaN": "", "None": "", "<NA>": ""})

    df["Scheduled Date"] = df["Scheduled Date"].apply(parse_date_to_date)
    df["Price"] = df["Price"].apply(parse_price_to_float)

    # Drop Excel summary rows (e.g., WO count + total price footer).
    summary_like = (
        df["WO"].str.fullmatch(r"\d+")
        & df["Quote"].eq("")
        & df["PO Number"].eq("")
        & df["Status"].eq("")
        & df["Customer Name"].eq("")
        & df["Model Description"].eq("")
        & df["Scheduled Date"].isna()
        & df["Price"].notna()
    )
    df = df[~summary_like]

    # Remove trailing empty-looking rows that became blanks after cleanup.
    blank_text = df[["WO", "Quote", "PO Number", "Status", "Customer Name", "Model Description"]].eq("").all(axis=1)
    df = df[~(blank_text & df["Scheduled Date"].isna() & df["Price"].isna())]

    return df


def df_to_calendar_events(df: pd.DataFrame):
    events = []
    for _, r in df.iterrows():
        wo = str(r.get("WO", "")).strip()
        d = r.get("Scheduled Date", pd.NaT)
        if not wo or pd.isna(d):
            continue
        cust  = str(r.get("Customer Name", "")).strip()
        model = str(r.get("Model Description", "")).strip()
        status = str(r.get("Status", "")).strip()

        title = " | ".join([p for p in [wo, cust] if p])
        if model:
            title += f" — {model}"

        events.append({
            "id": wo, "title": title,
            "start": d.isoformat(), "allDay": True,
            **status_to_colors(status),
            "extendedProps": {"wo": wo, "customer_name": cust,
                              "model_description": model, "status": status},
        })
    return events




def uploaded_file_signature(file) -> str:
    return hashlib.sha256(file.getvalue()).hexdigest()


def build_excel_bytes(df: pd.DataFrame) -> bytes:
    export_df = df.copy()
    export_df["Scheduled Date"] = pd.to_datetime(export_df["Scheduled Date"], errors="coerce")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="Order Book")
        ws = writer.sheets["Order Book"]
        col_map = {cell.value: cell.column for cell in ws[1]}
        date_col  = col_map.get("Scheduled Date")
        price_col = col_map.get("Price")
        if date_col:
            for r in range(2, ws.max_row + 1):
                ws.cell(row=r, column=date_col).number_format = "yyyy-mm-dd"
        if price_col:
            for r in range(2, ws.max_row + 1):
                ws.cell(row=r, column=price_col).number_format = '"$"#,##0.00'
        for col in range(1, ws.max_column + 1):
            max_len = max(
                (len(str(ws.cell(row=r, column=col).value or "")) for r in range(1, ws.max_row + 1)),
                default=10,
            )
            ws.column_dimensions[ws.cell(row=1, column=col).column_letter].width = min(max(10, max_len + 2), 60)
    return buf.getvalue()


def generate_monthly_print_view(df: pd.DataFrame, month: int, year: int) -> str:
    """Generate HTML for a printable monthly calendar view."""
    month_name = datetime(year, month, 1).strftime("%B %Y")
    
    # Filter data for the selected month
    df_month = df[
        (pd.to_datetime(df["Scheduled Date"], errors="coerce").dt.month == month) &
        (pd.to_datetime(df["Scheduled Date"], errors="coerce").dt.year == year)
    ].copy()
    
    # Group by date
    events_by_date = {}
    for _, row in df_month.iterrows():
        d = row["Scheduled Date"]
        if pd.isna(d):
            continue
        date_key = d.isoformat()
        if date_key not in events_by_date:
            events_by_date[date_key] = []
        
        wo = str(row.get("WO", "")).strip()
        cust = str(row.get("Customer Name", "")).strip()
        model = str(row.get("Model Description", "")).strip()
        status = str(row.get("Status", "")).strip()
        
        events_by_date[date_key].append({
            "wo": wo,
            "customer": cust,
            "model": model,
            "status": status,
        })
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{month_name}</title>
        <style>
            @media print {{
                @page {{ 
                    size: letter landscape;
                    margin: 0.4in; 
                }}
                body {{ margin: 0; }}
                .no-print {{ display: none; }}
            }}
            
            * {{
                -webkit-print-color-adjust: exact !important;
                print-color-adjust: exact !important;
                color-adjust: exact !important;
            }}
            
            body {{
                font-family: Arial, sans-serif;
                padding: 10px;
                background: white;
                max-width: 10.2in;
                margin: 0 auto;
            }}
            
            .header {{
                text-align: center;
                margin-bottom: 10px;
            }}
            
            .header h2 {{
                margin: 0;
                font-size: 16px;
                color: #333;
                font-weight: 600;
            }}
            
            .calendar-table {{
                width: 100%;
                border-collapse: collapse;
                table-layout: fixed;
                margin-bottom: 10px;
            }}
            
            .calendar-table th {{
                background-color: #2563eb;
                color: white;
                padding: 6px;
                text-align: center;
                border: 1px solid #999;
                font-size: 11px;
                width: 14.28%;
            }}
            
            .calendar-table td {{
                border: 1px solid #999;
                padding: 5px;
                vertical-align: top;
                width: 14.28%;
                height: 105px;
            }}
            
            .date-number {{
                font-weight: bold;
                font-size: 12px;
                margin-bottom: 4px;
                color: #333;
            }}
            
            .event-item {{
                margin-bottom: 5px;
                padding: 4px;
                border-radius: 3px;
                font-size: 9px;
                line-height: 1.3;
            }}
            
            .status-open {{ background-color: #dbeafe; border-left: 3px solid #2563eb; }}
            .status-inprogress {{ background-color: #fed7aa; border-left: 3px solid #d97706; }}
            .status-completed {{ background-color: #dcfce7; border-left: 3px solid #16a34a; }}
            .status-onhold {{ background-color: #e5e7eb; border-left: 3px solid #6b7280; }}
            .status-cancelled {{ background-color: #fee2e2; border-left: 3px solid #dc2626; }}
            .status-default {{ background-color: #ccfbf1; border-left: 3px solid #0f766e; }}
            
            .event-wo {{
                font-weight: bold;
                color: #000;
                font-size: 10px;
            }}
            
            .event-customer {{
                color: #1f2937;
                font-size: 9.5px;
                font-weight: 500;
            }}
            
            .event-model {{
                color: #374151;
                font-size: 9px;
            }}
            
            .legend {{
                margin-top: 10px;
                padding: 10px;
                background-color: #f9fafb;
                border: 1px solid #ddd;
                border-radius: 3px;
            }}
            
            .legend-title {{
                font-weight: bold;
                margin-bottom: 8px;
                font-size: 11px;
            }}
            
            .legend-items {{
                display: flex;
                flex-wrap: wrap;
                gap: 12px;
            }}
            
            .legend-item {{
                display: flex;
                align-items: center;
                gap: 5px;
                font-size: 10px;
            }}
            
            .legend-color {{
                width: 16px;
                height: 16px;
                border-radius: 2px;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h2>{month_name}</h2>
        </div>
        
        <table class="calendar-table">
            <thead>
                <tr>
                    <th>Sunday</th>
                    <th>Monday</th>
                    <th>Tuesday</th>
                    <th>Wednesday</th>
                    <th>Thursday</th>
                    <th>Friday</th>
                    <th>Saturday</th>
                </tr>
            </thead>
            <tbody>
    """
    
    # Get the first day of the month and number of days
    first_day_weekday = datetime(year, month, 1).weekday()
    # Adjust for Sunday being 0
    first_day_weekday = (first_day_weekday + 1) % 7
    num_days = monthrange(year, month)[1]
    
    # Build calendar grid
    current_day = 1
    weeks_needed = ((num_days + first_day_weekday) // 7) + (1 if (num_days + first_day_weekday) % 7 > 0 else 0)
    
    for week in range(weeks_needed):
        html += "<tr>"
        for day_of_week in range(7):
            if week == 0 and day_of_week < first_day_weekday:
                html += "<td></td>"
            elif current_day > num_days:
                html += "<td></td>"
            else:
                date_obj = date(year, month, current_day)
                date_str = date_obj.isoformat()
                
                html += f'<td><div class="date-number">{current_day}</div>'
                
                if date_str in events_by_date:
                    for event in events_by_date[date_str]:
                        status_class = normalize_status_key(event["status"]).replace(" ", "")
                        html += f'<div class="event-item status-{status_class}">'
                        html += f'<div class="event-wo">WO: {event["wo"]}</div>'
                        if event["customer"]:
                            html += f'<div class="event-customer">{event["customer"]}</div>'
                        if event["model"]:
                            html += f'<div class="event-model">{event["model"]}</div>'
                        html += '</div>'
                
                html += '</td>'
                current_day += 1
        html += "</tr>"
    
    html += """
            </tbody>
        </table>
        
        <div class="legend">
            <div class="legend-title">Status Legend:</div>
            <div class="legend-items">
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #2563eb;"></div>
                    <span>Open</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #d97706;"></div>
                    <span>In Progress</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #16a34a;"></div>
                    <span>Completed</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #6b7280;"></div>
                    <span>On Hold</span>
                </div>
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #dc2626;"></div>
                    <span>Cancelled</span>
                </div>
            </div>
        </div>
        
        <script>
            // Optional: Auto-print on load
            // window.onload = function() { window.print(); }
        </script>
    </body>
    </html>
    """
    
    return html


# ---------------- Session Init ----------------
if "df" not in st.session_state:
    with st.spinner("Loading saved data..."):
        df_loaded, last_name = load_data()
    st.session_state.df = df_loaded
    st.session_state.last_uploaded_name = last_name
if "last_uploaded_name" not in st.session_state:
    st.session_state.last_uploaded_name = None
if "df_version" not in st.session_state:
    st.session_state.df_version = 0
if "last_applied_change" not in st.session_state:
    st.session_state.last_applied_change = None
if "has_unsaved_changes" not in st.session_state:
    st.session_state.has_unsaved_changes = False
if "show_print_preview" not in st.session_state:
    st.session_state.show_print_preview = False
if "last_uploaded_signature" not in st.session_state:
    st.session_state.last_uploaded_signature = None


# ---------------- Sidebar ----------------
with st.sidebar:
    st.header("Upload Order Book")
    file = st.file_uploader("Excel (.xlsx) or CSV", type=["xlsx", "csv"])

    if st.session_state.last_uploaded_name:
        st.caption(f"📂 Loaded: **{st.session_state.last_uploaded_name}**")
    else:
        st.caption("No data loaded yet.")

    st.divider()
    
    # Password-protected clear data
    with st.expander("🗑️ Clear All Data"):
        st.warning("This will delete all data from the database. This action cannot be undone.")
        clear_password = st.text_input("Enter password to confirm", type="password", key="clear_password")
        if st.button("Delete All Data", type="secondary"):
            correct_password = st.secrets.get("UPDATE_PASSWORD", "admin123")
            if clear_password == correct_password:
                st.session_state.df = pd.DataFrame(columns=REQUIRED_COLS)
                st.session_state.last_uploaded_name = None
                st.session_state.df_version += 1
                st.session_state.has_unsaved_changes = False
                st.session_state.last_uploaded_signature = None
                save_data(pd.DataFrame(columns=REQUIRED_COLS), "")
                st.success("All data cleared.")
                st.rerun()
            else:
                st.error("❌ Incorrect password. Data not cleared.")

    st.caption("Use Streamlit's left sidebar page selector to open **Table View**.")


if file is not None:
    try:
        file_signature = uploaded_file_signature(file)
        if file_signature != st.session_state.last_uploaded_signature:
            file_bytes = file.getvalue()
            buffer = io.BytesIO(file_bytes)
            df_raw = pd.read_csv(buffer) if file.name.lower().endswith(".csv") else pd.read_excel(buffer)
            st.session_state.df = normalize_df(df_raw)
            st.session_state.df_version += 1
            st.session_state.last_uploaded_name = file.name
            st.session_state.last_uploaded_signature = file_signature
            st.session_state.has_unsaved_changes = True
            st.success(f"Loaded {len(st.session_state.df)} rows. Click 'Update Changes' below to save to database.")
    except Exception as e:
        st.exception(e)


# ---------------- Calendar ----------------
st.title("📅 Production Schedule Calendar")

events = df_to_calendar_events(st.session_state.df)

cal_state = calendar(
    events=events,
    options={
        "initialView": "dayGridMonth",
        "editable": True,
        "height": 900,
        "eventDisplay": "block",
        "dayMaxEvents": False,
        "headerToolbar": {
            "left": "today prev,next",
            "center": "title",
            "right": "dayGridMonth,timeGridWeek,timeGridDay,listWeek",
        },
    },
    custom_css="""
        .fc .fc-daygrid-event, .fc .fc-timegrid-event { white-space: normal !important; }
        .fc .fc-event-title, .fc .fc-list-event-title {
            white-space: normal !important; overflow: visible !important; text-overflow: clip !important;
        }
    """,
    key=f"calendar_{st.session_state.df_version}",
)

if cal_state and cal_state.get("callback") == "eventChange":
    ev = (cal_state.get("eventChange") or {}).get("event") or {}
    wo = str(ev.get("id", "")).strip()
    new_dt = pd.to_datetime(ev.get("start"), errors="coerce")
    if wo and not pd.isna(new_dt):
        new_date = new_dt.date()
        sig = f"{wo}|{new_date.isoformat()}"
        if st.session_state.last_applied_change != sig:
            mask = st.session_state.df["WO"].astype(str).str.strip() == wo
            if mask.any():
                old_val = st.session_state.df.loc[mask, "Scheduled Date"].iloc[0]
                if pd.isna(old_val) or old_val != new_date:
                    st.session_state.df.loc[mask, "Scheduled Date"] = new_date
                    st.session_state.has_unsaved_changes = True
                    # Don't increment df_version here - it causes calendar remount and snap-back
            st.session_state.last_applied_change = sig

# ---------------- Update Changes (Password Protected) ----------------
if st.session_state.has_unsaved_changes:
    st.warning("⚠️ You have unsaved changes. Click 'Update Changes' below to save to database.")

with st.expander("🔐 Update Changes (Password Protected)", expanded=st.session_state.has_unsaved_changes):
    st.write("Enter the password to save your changes to the Supabase database.")
    
    col_a, col_b = st.columns([2, 1])
    with col_a:
        password = st.text_input("Password", type="password", key="update_password")
    with col_b:
        update_btn = st.button("✅ Update Changes", type="primary", use_container_width=True)
    
    if update_btn:
        correct_password = st.secrets.get("UPDATE_PASSWORD", "admin123")  # Default fallback
        
        if password == correct_password:
            with st.spinner("Saving changes to database..."):
                save_data(st.session_state.df, st.session_state.last_uploaded_name)
            st.session_state.has_unsaved_changes = False
            st.session_state.df_version += 1  # Force calendar remount with saved data
            st.success("✅ Changes saved to database successfully!")
            st.rerun()
        else:
            st.error("❌ Incorrect password. Changes not saved.")

st.divider()

col1, col2, col3 = st.columns([1, 1, 2])
with col1:
    st.metric("Total orders", int(len(st.session_state.df)))
with col2:
    total_value = st.session_state.df["Price"].dropna().sum() if "Price" in st.session_state.df else 0
    st.metric("Total value", f"${total_value:,.2f}")
with col3:
    st.caption("Drag & drop an event to change Scheduled Date. Edit rows in Table View.")

st.caption("Status colors: 🔵 Open  🟠 In Progress  🟢 Completed  ⚫ On Hold  🔴 Cancelled")

st.subheader("Download updated Excel")
st.download_button(
    "Download XLSX",
    data=build_excel_bytes(st.session_state.df),
    file_name="order_book_updated.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()

# ---------------- Monthly Print View ----------------
st.subheader("🖨️ Print Monthly Schedule")
st.caption("Generate a printable calendar for production distribution")

print_col1, print_col2, print_col3 = st.columns([2, 2, 3])
with print_col1:
    print_month = st.selectbox(
        "Month",
        range(1, 13),
        format_func=lambda x: datetime(2000, x, 1).strftime("%B"),
        key="print_month",
        index=datetime.now().month - 1
    )
with print_col2:
    print_year = st.number_input(
        "Year",
        min_value=2020,
        max_value=2030,
        value=datetime.now().year,
        key="print_year"
    )
with print_col3:
    st.write("")  # Spacer
    generate_btn = st.button("📄 Generate Print View", type="primary")

if generate_btn:
    st.session_state.show_print_preview = True
    st.session_state.print_html = generate_monthly_print_view(st.session_state.df, print_month, print_year)
    st.session_state.print_month_name = datetime(print_year, print_month, 1).strftime("%B_%Y")

if "show_print_preview" in st.session_state and st.session_state.show_print_preview:
    col_dl, col_hide = st.columns([1, 4])
    with col_dl:
        st.download_button(
            label="💾 Download HTML to Print",
            data=st.session_state.print_html,
            file_name=f"production_schedule_{st.session_state.print_month_name}.html",
            mime="text/html",
            help="Download and open in browser, then use Ctrl+P (Cmd+P on Mac) to print"
        )
    with col_hide:
        if st.button("Hide Preview"):
            st.session_state.show_print_preview = False
            st.rerun()
    
    st.info("👁️ Preview below - Download the HTML file and open in your browser to print with proper formatting")
    st.components.v1.html(st.session_state.print_html, height=1000, scrolling=True)
