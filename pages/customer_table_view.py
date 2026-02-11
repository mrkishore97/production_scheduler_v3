# pages/customer_table_view.py

from datetime import datetime

import pandas as pd
import streamlit as st
from supabase import create_client, Client

st.set_page_config(page_title="Customer Order Table", layout="wide")

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


# ---------------- Supabase ----------------

@st.cache_resource
def get_supabase() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


def load_data() -> pd.DataFrame:
    """Fetch all rows from Supabase."""
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

    df["Scheduled Date"] = df["Scheduled Date"].apply(parse_date)
    df["Price"] = df["Price"].apply(lambda x: float(x) if x is not None else pd.NA)
    for c in ["Quote", "PO Number", "Status", "Customer Name", "Model Description"]:
        df[c] = df[c].fillna("").astype(str)

    present = [c for c in REQUIRED_COLS if c in df.columns]
    return df[present]


# ---------------- Helpers ----------------

def parse_date(x):
    if x is None or str(x).strip() in ("", "None", "NaT"):
        return pd.NaT
    try:
        if pd.isna(x):
            return pd.NaT
    except Exception:
        pass
    return pd.to_datetime(x, errors="coerce").date()


def apply_filters(df, filters):
    """Apply all filters to the DataFrame."""
    filtered_df = df.copy()
    
    # Quote filter
    if filters["quote_text"]:
        if filters["quote_match"] == "Exact":
            filtered_df = filtered_df[filtered_df["Quote"].str.strip() == filters["quote_text"].strip()]
        else:  # Contains
            filtered_df = filtered_df[filtered_df["Quote"].str.contains(filters["quote_text"], case=False, na=False)]
    
    # PO Number filter
    if filters["po_text"]:
        if filters["po_match"] == "Exact":
            filtered_df = filtered_df[filtered_df["PO Number"].str.strip() == filters["po_text"].strip()]
        else:  # Contains
            filtered_df = filtered_df[filtered_df["PO Number"].str.contains(filters["po_text"], case=False, na=False)]
    
    # Status filter
    if filters["status"] and filters["status"] != "All":
        if filters["status_match"] == "Exact":
            filtered_df = filtered_df[filtered_df["Status"].str.strip().str.lower() == filters["status"].lower()]
        else:  # Contains
            filtered_df = filtered_df[filtered_df["Status"].str.contains(filters["status"], case=False, na=False)]
    
    # Model Description filter
    if filters["model_text"]:
        if filters["model_match"] == "Exact":
            filtered_df = filtered_df[filtered_df["Model Description"].str.strip() == filters["model_text"].strip()]
        else:  # Contains
            filtered_df = filtered_df[filtered_df["Model Description"].str.contains(filters["model_text"], case=False, na=False)]
    
    # Date filters
    if filters["date_filter_type"] == "Exact Date" and filters["exact_date"]:
        filtered_df = filtered_df[filtered_df["Scheduled Date"] == filters["exact_date"]]
    elif filters["date_filter_type"] == "Month" and filters["month"] and filters["year"]:
        filtered_df = filtered_df[
            (pd.to_datetime(filtered_df["Scheduled Date"], errors="coerce").dt.month == filters["month"]) &
            (pd.to_datetime(filtered_df["Scheduled Date"], errors="coerce").dt.year == filters["year"])
        ]
    
    return filtered_df


# ---------------- Session Init ----------------
if "df" not in st.session_state:
    with st.spinner("Loading order data..."):
        df_loaded = load_data()
    st.session_state.df = df_loaded
if "df_version" not in st.session_state:
    st.session_state.df_version = 0
if "selected_customer" not in st.session_state:
    st.session_state.selected_customer = None


# ---------------- Customer Selection (if not set) ----------------
# Get unique customer names
customer_names = sorted(st.session_state.df["Customer Name"].unique().tolist())
customer_names = [c for c in customer_names if c.strip()]  # Remove empty strings

# Sidebar for customer selection
with st.sidebar:
    st.header("🏢 Customer Portal")
    
    if not customer_names:
        st.warning("No customers found in database.")
        selected_customer = None
    else:
        # Use session state if available, otherwise default to first
        default_idx = 0
        if st.session_state.selected_customer and st.session_state.selected_customer in customer_names:
            default_idx = customer_names.index(st.session_state.selected_customer)
        
        selected_customer = st.selectbox(
            "Select Your Company",
            customer_names,
            index=default_idx,
            help="Select your company name to view your orders"
        )
    
    st.divider()
    st.caption("🔒 This is a read-only view. Contact admin for changes.")

# Update session state
if selected_customer:
    st.session_state.selected_customer = selected_customer


# ---------------- Table Page ----------------
if not selected_customer:
    st.title("🧾 Table View")
    st.info("👈 Please select your company from the sidebar to view your orders.")
    st.stop()

st.title(f"🧾 Your Orders - {selected_customer}")
st.caption("View your order details below. This is a read-only view.")

# Filter data for selected customer
customer_df = st.session_state.df[
    st.session_state.df["Customer Name"].str.lower() == selected_customer.lower()
].copy()

if customer_df.empty:
    st.warning(f"No orders found for {selected_customer}.")
    st.stop()

# ---------------- Filters ----------------
st.subheader("🔍 Filters")
st.caption("Apply filters to narrow down the view.")

with st.expander("Filter Options", expanded=False):
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**Quote Number**")
        quote_cols = st.columns([3, 1])
        quote_text = quote_cols[0].text_input("Quote", label_visibility="collapsed", key="filter_quote")
        quote_match = quote_cols[1].selectbox("Match", ["Contains", "Exact"], key="quote_match_type", label_visibility="collapsed")
        
        st.markdown("**PO Number**")
        po_cols = st.columns([3, 1])
        po_text = po_cols[0].text_input("PO Number", label_visibility="collapsed", key="filter_po")
        po_match = po_cols[1].selectbox("Match", ["Contains", "Exact"], key="po_match_type", label_visibility="collapsed")
        
        st.markdown("**Status**")
        status_cols = st.columns([3, 1])
        unique_statuses = ["All"] + sorted(customer_df["Status"].unique().tolist())
        status = status_cols[0].selectbox("Status", unique_statuses, key="filter_status", label_visibility="collapsed")
        status_match = status_cols[1].selectbox("Match", ["Contains", "Exact"], key="status_match_type", label_visibility="collapsed")
    
    with col2:
        st.markdown("**Model Description**")
        model_cols = st.columns([3, 1])
        model_text = model_cols[0].text_input("Model", label_visibility="collapsed", key="filter_model")
        model_match = model_cols[1].selectbox("Match", ["Contains", "Exact"], key="model_match_type", label_visibility="collapsed")
        
        st.markdown("**Date Filter**")
        date_filter_type = st.radio("Filter by", ["None", "Exact Date", "Month"], horizontal=True, key="date_filter_type")
        
        exact_date = None
        month = None
        year = None
        
        if date_filter_type == "Exact Date":
            exact_date = st.date_input("Select Date", key="filter_exact_date")
        elif date_filter_type == "Month":
            date_cols = st.columns(2)
            month = date_cols[0].selectbox("Month", range(1, 13), 
                                          format_func=lambda x: datetime(2000, x, 1).strftime("%B"),
                                          key="filter_month")
            year = date_cols[1].number_input("Year", min_value=2020, max_value=2030, 
                                            value=datetime.now().year, key="filter_year")
    
    # Clear filters button
    if st.button("🔄 Clear All Filters"):
        st.rerun()

# Prepare filters dictionary
filters = {
    "quote_text": quote_text,
    "quote_match": quote_match,
    "po_text": po_text,
    "po_match": po_match,
    "status": status,
    "status_match": status_match,
    "model_text": model_text,
    "model_match": model_match,
    "date_filter_type": date_filter_type,
    "exact_date": exact_date,
    "month": month,
    "year": year,
}

# Apply filters
display_df = apply_filters(customer_df, filters)

# Show filtered count
st.caption(f"Showing {len(display_df)} of {len(customer_df)} total orders")

# Display table (read-only)
st.dataframe(
    display_df,
    use_container_width=True,
    column_config={
        "Scheduled Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        "Price": st.column_config.NumberColumn(format="$%.2f"),
    },
    hide_index=True,
)

# Summary statistics
st.divider()
st.subheader("📊 Summary")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Total Orders Shown", len(display_df))
with col2:
    total_value = display_df["Price"].dropna().sum() if "Price" in display_df else 0
    st.metric("Total Value", f"${total_value:,.2f}")
with col3:
    if not display_df.empty and "Status" in display_df:
        most_common_status = display_df["Status"].mode()[0] if len(display_df["Status"].mode()) > 0 else "N/A"
        st.metric("Most Common Status", most_common_status)

# Status breakdown
if not display_df.empty and "Status" in display_df:
    st.subheader("📈 Status Breakdown")
    status_counts = display_df["Status"].value_counts()
    st.bar_chart(status_counts)

st.divider()
st.caption("💡 Need to make changes? Please contact your administrator.")
