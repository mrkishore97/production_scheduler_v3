# pages/table_view.py

import re
from datetime import datetime

import pandas as pd
import streamlit as st
from supabase import create_client, Client

st.set_page_config(page_title="Order Book Table", layout="wide")

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


def save_data(df: pd.DataFrame, last_uploaded_name: str):
    """Replace all rows in Supabase with the current DataFrame."""
    supabase = get_supabase()
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

    for i in range(0, len(rows), 500):
        supabase.table(SUPABASE_TABLE).insert(rows[i : i + 500]).execute()


def load_data() -> tuple[pd.DataFrame, str | None]:
    """Fetch all rows from Supabase."""
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

    df["Scheduled Date"] = df["Scheduled Date"].apply(parse_date)
    df["Price"] = df["Price"].apply(lambda x: float(x) if x is not None else pd.NA)
    for c in ["Quote", "PO Number", "Status", "Customer Name", "Model Description"]:
        df[c] = df[c].fillna("").astype(str)

    present = [c for c in REQUIRED_COLS if c in df.columns]
    return df[present], last_name


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


def parse_price(x):
    if x is None or str(x).strip() == "":
        return pd.NA
    s = str(x).replace("$", "").replace(",", "")
    try:
        return float(s)
    except Exception:
        return pd.NA


def normalize_df(df):
    df = df.copy()
    df = df[REQUIRED_COLS]

    text_cols = ["WO", "Quote", "PO Number", "Status", "Customer Name", "Model Description"]
    for c in text_cols:
        df[c] = df[c].where(df[c].notna(), "").astype(str).str.strip()
        df[c] = df[c].replace({"nan": "", "NaN": "", "None": "", "<NA>": ""})

    df["Scheduled Date"] = df["Scheduled Date"].apply(parse_date)
    df["Price"] = df["Price"].apply(parse_price)

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
    blank_text = df[["WO", "Quote", "PO Number", "Status", "Customer Name", "Model Description"]].eq("").all(axis=1)
    df = df[~(summary_like | (blank_text & df["Scheduled Date"].isna() & df["Price"].isna()))]

    return df


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
    
    # Customer Name filter
    if filters["customer_text"]:
        if filters["customer_match"] == "Exact":
            filtered_df = filtered_df[filtered_df["Customer Name"].str.strip() == filters["customer_text"].strip()]
        else:  # Contains
            filtered_df = filtered_df[filtered_df["Customer Name"].str.contains(filters["customer_text"], case=False, na=False)]
    
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
    with st.spinner("Loading saved data..."):
        df_loaded, last_name = load_data()
    st.session_state.df = df_loaded
    st.session_state.last_uploaded_name = last_name
if "last_uploaded_name" not in st.session_state:
    st.session_state.last_uploaded_name = None
if "df_version" not in st.session_state:
    st.session_state.df_version = 0
if "has_unsaved_changes" not in st.session_state:
    st.session_state.has_unsaved_changes = False


# ---------------- Table Page ----------------
st.title("🧾 Table View")
st.caption("Edit rows here, then click **Apply Changes**. Calendar updates automatically.")

if st.session_state.last_uploaded_name:
    st.info(f"📂 Currently loaded: **{st.session_state.last_uploaded_name}**")
else:
    st.warning("No data loaded. Upload a file from the Calendar page.")

if st.session_state.has_unsaved_changes:
    st.warning("⚠️ You have unsaved changes. Save them below before they're lost.")

# ---------------- Filters ----------------
st.subheader("🔍 Filters (View Only)")
st.caption("Apply filters to view specific data. Filters do not affect editing or saving.")

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
        # Get unique statuses from the dataframe
        unique_statuses = ["All"] + sorted(st.session_state.df["Status"].unique().tolist())
        status = status_cols[0].selectbox("Status", unique_statuses, key="filter_status", label_visibility="collapsed")
        status_match = status_cols[1].selectbox("Match", ["Contains", "Exact"], key="status_match_type", label_visibility="collapsed")
    
    with col2:
        st.markdown("**Customer Name**")
        customer_cols = st.columns([3, 1])
        customer_text = customer_cols[0].text_input("Customer", label_visibility="collapsed", key="filter_customer")
        customer_match = customer_cols[1].selectbox("Match", ["Contains", "Exact"], key="customer_match_type", label_visibility="collapsed")
        
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
    "customer_text": customer_text,
    "customer_match": customer_match,
    "model_text": model_text,
    "model_match": model_match,
    "date_filter_type": date_filter_type,
    "exact_date": exact_date,
    "month": month,
    "year": year,
}

# Apply filters for display only
display_df = apply_filters(st.session_state.df, filters)

# Show filtered count
st.caption(f"Showing {len(display_df)} of {len(st.session_state.df)} total rows")

with st.form("table_form"):
    edited = st.data_editor(
        display_df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Scheduled Date": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "Price": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    apply = st.form_submit_button("✅ Apply Changes")

if apply:
    # When saving, we need to merge the edited filtered data back into the full dataset
    df_new = normalize_df(edited)
    mask = (
        df_new["WO"].str.strip().ne("")
        | df_new["Customer Name"].str.strip().ne("")
        | df_new["Model Description"].str.strip().ne("")
    )
    df_new = df_new.loc[mask]
    
    # Update the full dataframe with changes from the filtered view
    if not df_new.empty:
        # Remove rows that were in the filtered view from the main df
        wo_in_filtered = display_df["WO"].tolist()
        st.session_state.df = st.session_state.df[~st.session_state.df["WO"].isin(wo_in_filtered)]
        
        # Add the edited rows back
        st.session_state.df = pd.concat([st.session_state.df, df_new], ignore_index=True)
    
    st.session_state.df_version += 1
    st.session_state.has_unsaved_changes = True
    st.success("Changes applied. Click 'Update Changes' below to save to database.")
    st.rerun()

# ---------------- Update Changes (Password Protected) ----------------
st.divider()
st.subheader("🔐 Save to Database")

with st.expander("Password Protected Update", expanded=st.session_state.has_unsaved_changes):
    st.write("Enter the password to save your changes to the Supabase database.")
    
    col_a, col_b = st.columns([2, 1])
    with col_a:
        password = st.text_input("Password", type="password", key="table_update_password")
    with col_b:
        update_btn = st.button("✅ Update Changes", type="primary", use_container_width=True)
    
    if update_btn:
        correct_password = st.secrets.get("UPDATE_PASSWORD", "admin123")
        
        if password == correct_password:
            with st.spinner("Saving to database..."):
                save_data(st.session_state.df, st.session_state.last_uploaded_name)
            st.session_state.has_unsaved_changes = False
            st.success("✅ Changes saved to database successfully!")
            st.rerun()
        else:
            st.error("❌ Incorrect password. Changes not saved.")
