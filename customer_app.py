# customer_app.py

import hashlib
import io
from datetime import datetime, date
from calendar import monthrange

import pandas as pd
import streamlit as st
from streamlit_calendar import calendar
from supabase import create_client, Client

# ---------------- Config ----------------
st.set_page_config(page_title="Customer Order Calendar", layout="wide")

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

STATUS_COLORS = {
    "open":       {"backgroundColor": "#2563eb", "borderColor": "#1d4ed8", "textColor": "#ffffff"},
    "in progress":{"backgroundColor": "#d97706", "borderColor": "#b45309", "textColor": "#ffffff"},
    "completed":  {"backgroundColor": "#16a34a", "borderColor": "#15803d", "textColor": "#ffffff"},
    "on hold":    {"backgroundColor": "#6b7280", "borderColor": "#4b5563", "textColor": "#ffffff"},
    "cancelled":  {"backgroundColor": "#dc2626", "borderColor": "#b91c1c", "textColor": "#ffffff"},
    "default":    {"backgroundColor": "#0f766e", "borderColor": "#115e59", "textColor": "#ffffff"},
}

# Color for "sold" dates (other customers' orders)
SOLD_COLORS = {"backgroundColor": "#94a3b8", "borderColor": "#64748b", "textColor": "#ffffff"}

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


# ---------------- Supabase ----------------

@st.cache_resource
def get_supabase() -> Client:
    """Cached Supabase client — created once per app lifetime."""
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


def load_data() -> pd.DataFrame:
    """Fetch all rows from Supabase and return as a normalized DataFrame."""
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

    df["Scheduled Date"] = df["Scheduled Date"].apply(parse_date_to_date)
    df["Price"] = df["Price"].apply(lambda x: float(x) if x is not None else pd.NA)
    for c in ["Quote", "PO Number", "Status", "Customer Name", "Model Description"]:
        df[c] = df[c].fillna("").astype(str)

    present = [c for c in REQUIRED_COLS if c in df.columns]
    return df[present]


# ---------------- Helpers ----------------

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


def df_to_calendar_events(df: pd.DataFrame, selected_customer: str):
    """
    Convert DataFrame to calendar events.
    - Customer's own orders show with status colors
    - Other customers' orders show as "SOLD" with gray color
    """
    events = []
    for _, r in df.iterrows():
        wo = str(r.get("WO", "")).strip()
        d = r.get("Scheduled Date", pd.NaT)
        if not wo or pd.isna(d):
            continue
        
        cust = str(r.get("Customer Name", "")).strip()
        model = str(r.get("Model Description", "")).strip()
        status = str(r.get("Status", "")).strip()
        
        # Check if this order belongs to the selected customer
        is_customer_order = (cust.lower() == selected_customer.lower())
        
        if is_customer_order:
            # Show customer's own orders with full details
            title = " | ".join([p for p in [wo, cust] if p])
            if model:
                title += f" — {model}"
            
            events.append({
                "id": wo,
                "title": title,
                "start": d.isoformat(),
                "allDay": True,
                **status_to_colors(status),
                "extendedProps": {
                    "wo": wo,
                    "customer_name": cust,
                    "model_description": model,
                    "status": status
                },
            })
        else:
            # Show other customers' orders as "SOLD"
            events.append({
                "id": f"sold_{wo}",
                "title": "SOLD",
                "start": d.isoformat(),
                "allDay": True,
                **SOLD_COLORS,
                "extendedProps": {
                    "sold": True
                },
            })
    
    return events


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


def generate_monthly_print_view(df: pd.DataFrame, month: int, year: int, selected_customer: str) -> str:
    """Generate HTML for a printable monthly calendar view (customer's orders only)."""
    month_name = datetime(year, month, 1).strftime("%B %Y")
    
    # Filter data for the selected month and customer
    df_month = df[
        (pd.to_datetime(df["Scheduled Date"], errors="coerce").dt.month == month) &
        (pd.to_datetime(df["Scheduled Date"], errors="coerce").dt.year == year) &
        (df["Customer Name"].str.lower() == selected_customer.lower())
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
    
    # Get sold dates (other customers in this month)
    df_sold = df[
        (pd.to_datetime(df["Scheduled Date"], errors="coerce").dt.month == month) &
        (pd.to_datetime(df["Scheduled Date"], errors="coerce").dt.year == year) &
        (df["Customer Name"].str.lower() != selected_customer.lower())
    ].copy()
    
    sold_dates = set()
    for _, row in df_sold.iterrows():
        d = row["Scheduled Date"]
        if not pd.isna(d):
            sold_dates.add(d.isoformat())
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>{month_name} - {selected_customer}</title>
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
            
            .header .customer {{
                margin: 5px 0 0 0;
                font-size: 14px;
                color: #666;
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
            
            .calendar-table td.sold {{
                background-color: #f1f5f9;
            }}
            
            .date-number {{
                font-weight: bold;
                font-size: 12px;
                margin-bottom: 4px;
                color: #333;
            }}
            
            .sold-marker {{
                text-align: center;
                padding: 8px;
                background-color: #94a3b8;
                color: white;
                border-radius: 3px;
                font-weight: bold;
                font-size: 10px;
                margin-top: 10px;
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
            <div class="customer">Customer: {selected_customer}</div>
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
                
                # Check if sold
                is_sold = date_str in sold_dates
                td_class = ' class="sold"' if is_sold else ''
                
                html += f'<td{td_class}><div class="date-number">{current_day}</div>'
                
                if date_str in events_by_date:
                    # Customer's orders
                    for event in events_by_date[date_str]:
                        status_class = normalize_status_key(event["status"]).replace(" ", "")
                        html += f'<div class="event-item status-{status_class}">'
                        html += f'<div class="event-wo">WO: {event["wo"]}</div>'
                        if event["customer"]:
                            html += f'<div class="event-customer">{event["customer"]}</div>'
                        if event["model"]:
                            html += f'<div class="event-model">{event["model"]}</div>'
                        html += '</div>'
                elif is_sold:
                    # Show SOLD marker
                    html += '<div class="sold-marker">SOLD</div>'
                
                html += '</td>'
                current_day += 1
        html += "</tr>"
    
    html += """
            </tbody>
        </table>
        
        <div class="legend">
            <div class="legend-title">Legend:</div>
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
                <div class="legend-item">
                    <div class="legend-color" style="background-color: #94a3b8;"></div>
                    <span>SOLD (Unavailable)</span>
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
    with st.spinner("Loading order data..."):
        df_loaded = load_data()
    st.session_state.df = df_loaded
if "df_version" not in st.session_state:
    st.session_state.df_version = 0
if "show_print_preview" not in st.session_state:
    st.session_state.show_print_preview = False


# ---------------- Sidebar - Customer Selection ----------------
with st.sidebar:
    st.header("🏢 Customer Portal")
    
    # Get unique customer names
    customer_names = sorted(st.session_state.df["Customer Name"].unique().tolist())
    customer_names = [c for c in customer_names if c.strip()]  # Remove empty strings
    
    if not customer_names:
        st.warning("No customers found in database.")
        selected_customer = None
    else:
        selected_customer = st.selectbox(
            "Select Your Company",
            customer_names,
            help="Select your company name to view your orders"
        )
    
    st.divider()
    st.caption("📖 Use the sidebar page selector to open **Table View**")
    st.caption("🔒 This is a read-only view. Contact admin for changes.")

# Store selected customer in session state
if selected_customer:
    st.session_state.selected_customer = selected_customer
else:
    st.session_state.selected_customer = None

# ---------------- Main Content ----------------
if not selected_customer:
    st.title("📅 Production Schedule Calendar")
    st.info("👈 Please select your company from the sidebar to view your orders.")
    st.stop()

# Filter data for selected customer
customer_df = st.session_state.df[
    st.session_state.df["Customer Name"].str.lower() == selected_customer.lower()
].copy()

st.title(f"📅 Production Schedule - {selected_customer}")

# ---------------- Calendar ----------------
events = df_to_calendar_events(st.session_state.df, selected_customer)

cal_state = calendar(
    events=events,
    options={
        "initialView": "dayGridMonth",
        "editable": False,  # Read-only
        "selectable": False,  # Can't select dates
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
        .fc .fc-daygrid-event, .fc .fc-timegrid-event { 
            white-space: normal !important;
            cursor: default !important;
        }
        .fc .fc-event-title, .fc .fc-list-event-title {
            white-space: normal !important; 
            overflow: visible !important; 
            text-overflow: clip !important;
        }
    """,
    key=f"calendar_{st.session_state.df_version}",
)

st.divider()

# Statistics
col1, col2, col3 = st.columns([1, 1, 2])
with col1:
    st.metric("Your Orders", int(len(customer_df)))
with col2:
    total_value = customer_df["Price"].dropna().sum() if "Price" in customer_df else 0
    st.metric("Total Value", f"${total_value:,.2f}")
with col3:
    st.caption("🟦 Your orders | ⬜ SOLD (unavailable dates)")

st.caption("Status colors: 🔵 Open  🟠 In Progress  🟢 Completed  ⚫ On Hold  🔴 Cancelled")

# Download Excel (customer's data only)
st.subheader("📥 Download Your Orders")
st.download_button(
    "Download Excel",
    data=build_excel_bytes(customer_df),
    file_name=f"orders_{selected_customer.replace(' ', '_')}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

st.divider()

# ---------------- Monthly Print View ----------------
st.subheader("🖨️ Print Monthly Schedule")
st.caption("Generate a printable calendar showing your orders and sold dates")

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
    st.session_state.print_html = generate_monthly_print_view(
        st.session_state.df, 
        print_month, 
        print_year,
        selected_customer
    )
    st.session_state.print_month_name = datetime(print_year, print_month, 1).strftime("%B_%Y")

if st.session_state.show_print_preview:
    col_dl, col_hide = st.columns([1, 4])
    with col_dl:
        st.download_button(
            label="💾 Download HTML to Print",
            data=st.session_state.print_html,
            file_name=f"schedule_{selected_customer.replace(' ', '_')}_{st.session_state.print_month_name}.html",
            mime="text/html",
            help="Download and open in browser, then use Ctrl+P (Cmd+P on Mac) to print"
        )
    with col_hide:
        if st.button("Hide Preview"):
            st.session_state.show_print_preview = False
            st.rerun()
    
    st.info("👁️ Preview below - Download the HTML file and open in your browser to print with proper formatting")
    st.components.v1.html(st.session_state.print_html, height=1000, scrolling=True)
