"""
Kiltie Cut List -- Live production planning for Guarded Goods.
Pulls unfulfilled orders from Shopify, extracts Boot Kiltie line items
with leather type and height, and aggregates into a cutting guide.
Orders disappear as they are fulfilled in Shopify.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import re

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(
    page_title="Kiltie Cut List",
    page_icon=None,
    layout="wide",
)

# ===================================================================
# Shopify API helpers
# ===================================================================

@st.cache_data(ttl=3600)
def _get_token() -> str:
    cfg = st.secrets["shopify"]
    resp = requests.post(
        f"https://{cfg['store']}/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _base_url() -> str:
    cfg = st.secrets["shopify"]
    return f"https://{cfg['store']}/admin/api/{cfg['api_version']}"


def _headers() -> dict[str, str]:
    return {"X-Shopify-Access-Token": _get_token(), "Content-Type": "application/json"}


def _paginated_get(endpoint: str, key: str) -> list[dict]:
    url = f"{_base_url()}/{endpoint}"
    items: list[dict] = []
    while url:
        resp = requests.get(url, headers=_headers(), timeout=30)
        resp.raise_for_status()
        items.extend(resp.json().get(key, []))
        link = resp.headers.get("Link", "")
        m = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = m.group(1) if m else None
    return items


@st.cache_data(ttl=300, show_spinner=False)
def get_products() -> list[dict]:
    try:
        return _paginated_get("products.json?limit=250&status=active", "products")
    except Exception as exc:
        st.warning(f"Failed to fetch products: {exc}")
        return []


@st.cache_data(ttl=120, show_spinner=False)
def get_orders(days: int = 60) -> list[dict]:
    try:
        min_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%S%z"
        )
        params = urlencode({"status": "any", "created_at_min": min_date, "limit": 250})
        return _paginated_get(f"orders.json?{params}", "orders")
    except Exception as exc:
        st.warning(f"Failed to fetch orders: {exc}")
        return []


# ===================================================================
# Page
# ===================================================================

st.title("Kiltie Cut List")
st.caption("Live from Shopify -- updates as you fulfill orders.")

# Controls row
ctrl1, ctrl2, ctrl3 = st.columns([2, 1, 1])
with ctrl1:
    order_filter = st.selectbox(
        "Show",
        ["Unfulfilled orders", "All orders (60 days)", "All orders (90 days)"],
        label_visibility="collapsed",
    )
with ctrl3:
    if st.button("Refresh", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

lookup_days = 90 if "90" in order_filter else 60

# --- Load data ---

with st.spinner("Pulling live data from Shopify..."):
    try:
        products = get_products()
        orders = get_orders(days=lookup_days)
    except Exception as exc:
        st.error(f"Could not connect to Shopify: {exc}")
        st.stop()

# Build kiltie product lookup
kiltie_products: dict[int, dict] = {}
for p in products:
    tags = (p.get("tags", "") or "").lower()
    ptype = (p.get("product_type", "") or "").lower()
    if "boot kilties" in tags or "kiltie" in ptype:
        title = p.get("title", "")
        tag_list = [t.strip() for t in (p.get("tags", "") or "").split(",")]

        tannery = ""
        leather_type = ""
        color = ""
        for t in tag_list:
            if "(" in t and ")" in t and t not in ("Boot Kilties", "Shell Cordovan (Horse)"):
                tannery = t
            elif t in (
                "Steerhide", "Horsebutt", "Shell Cordovan (Horse)", "Deer",
                "Boar", "Calfskin", "Horsehide",
            ):
                leather_type = t
            elif t in (
                "Black", "Dark Brown", "Medium Brown", "Light Brown", "Brown",
                "Natural", "Burgundy", "Green", "Navy", "Grey", "Red",
            ):
                color = t

        kiltie_products[p["id"]] = {
            "title": title,
            "tannery": tannery,
            "leather_type": leather_type,
            "color": color,
            "tags": tag_list,
            "price": p.get("variants", [{}])[0].get("price", "0"),
            "inventory": p.get("variants", [{}])[0].get("inventory_quantity", 0),
        }

# --- Filter orders ---

if "Unfulfilled" in order_filter:
    filtered_orders = [
        o for o in orders
        if o.get("fulfillment_status") in (None, "null", "partial", "")
        and o.get("financial_status") in ("paid", "authorized", "partially_paid")
    ]
    filter_label = "unfulfilled"
else:
    filtered_orders = [
        o for o in orders
        if o.get("financial_status") not in ("voided", "refunded")
    ]
    filter_label = f"all ({lookup_days}d)"

# --- Extract kiltie line items ---

kiltie_items: list[dict] = []
for o in filtered_orders:
    order_num = o.get("order_number", o["id"])
    order_date = o.get("created_at", "")[:10]
    customer_name = ""
    if o.get("customer"):
        c = o["customer"]
        customer_name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
    if not customer_name:
        customer_name = o.get("email", "N/A")

    ship_addr = o.get("shipping_address") or {}
    ship_state = ship_addr.get("province", "")
    fulfillment_status = o.get("fulfillment_status") or "unfulfilled"

    for li in o.get("line_items", []):
        product_id = li.get("product_id")
        title_lower = (li.get("title", "") or "").lower()

        is_kiltie = product_id in kiltie_products or "kiltie" in title_lower
        is_height_addon = (li.get("title", "") or "").strip() == "Kiltie Height"
        if is_kiltie and not is_height_addon:
            prod_info = kiltie_products.get(product_id, {})

            kiltie_height = ""
            for prop in li.get("properties", []):
                if (prop.get("name", "") or "").strip() == "Kiltie Height":
                    kiltie_height = (prop.get("value", "") or "").strip()
                    break

            kiltie_items.append({
                "order": order_num,
                "date": order_date,
                "customer": customer_name,
                "state": ship_state,
                "status": fulfillment_status,
                "leather": li.get("title", prod_info.get("title", "Unknown")),
                "height": kiltie_height,
                "tannery": prod_info.get("tannery", ""),
                "leather_type": prod_info.get("leather_type", ""),
                "color": prod_info.get("color", ""),
                "quantity": li.get("quantity", 1),
                "price": float(li.get("price", 0)),
            })

# ===================================================================
# Display
# ===================================================================

st.divider()

if not kiltie_items:
    st.success(
        f"No kilties to cut. Checked {len(filtered_orders)} "
        f"{filter_label} orders."
    )
else:
    total_kilties = sum(i["quantity"] for i in kiltie_items)
    unique_leathers = len(set(i["leather"] for i in kiltie_items))
    unique_orders = len(set(i["order"] for i in kiltie_items))
    total_value = sum(i["price"] * i["quantity"] for i in kiltie_items)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Kilties to Cut", total_kilties)
    m2.metric("Leathers", unique_leathers)
    m3.metric("Orders", unique_orders)
    m4.metric("Value", f"${total_value:,.2f}")

    # ---------------------------------------------------------------
    # PRINT-READY CUT LIST
    # ---------------------------------------------------------------

    st.divider()
    st.subheader("Print Cut List")
    st.caption("Use Ctrl+P / Cmd+P to print. This section is designed for clean printing.")

    # Aggregate by leather + height
    print_agg: dict[tuple[str, str], int] = Counter()
    for item in kiltie_items:
        key = (item["leather"], item["height"] or "Not specified")
        print_agg[key] += item["quantity"]

    # Build a clean printable table
    print_rows = []
    for (leather, height), qty in sorted(print_agg.items(), key=lambda x: (-x[1], x[0])):
        print_rows.append({"Qty": qty, "Leather": leather, "Height": height})

    print_df = pd.DataFrame(print_rows)

    # Inject CSS that hides everything except this table when printing
    st.markdown(
        """
        <style>
        @media print {
            /* Hide Streamlit chrome */
            header, footer, [data-testid="stSidebar"],
            [data-testid="stToolbar"], [data-testid="stDecoration"],
            [data-testid="stStatusWidget"], .stDeployButton,
            [data-testid="manage-app-button"] {
                display: none !important;
            }
            /* Hide everything by default */
            section.main > div > div > div > div {
                display: none !important;
            }
            /* Show only the print section */
            #print-section, #print-section * {
                display: block !important;
                visibility: visible !important;
            }
            #print-section {
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
            }
            #print-section table {
                width: 100%;
                border-collapse: collapse;
                font-size: 14pt;
            }
            #print-section th, #print-section td {
                border: 1px solid #333;
                padding: 8px 12px;
                text-align: left;
            }
            #print-section th {
                background: #f0f0f0 !important;
                font-weight: bold;
            }
            #print-section h2 {
                margin-bottom: 10px;
                font-size: 18pt;
            }
            #print-section .print-date {
                font-size: 10pt;
                color: #666;
                margin-bottom: 16px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # Build the printable HTML table
    table_html = "<table><thead><tr><th>Qty</th><th>Leather</th><th>Height</th></tr></thead><tbody>"
    for row in print_rows:
        table_html += f"<tr><td><strong>{row['Qty']}</strong></td><td>{row['Leather']}</td><td>{row['Height']}</td></tr>"
    table_html += "</tbody></table>"

    st.markdown(
        f"""
        <div id="print-section">
            <h2>Kiltie Cut List</h2>
            <div class="print-date">{datetime.now().strftime('%B %d, %Y')} | {total_kilties} kilties across {unique_orders} orders</div>
            {table_html}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Also show as a Streamlit dataframe for on-screen use
    st.dataframe(print_df, use_container_width=True, hide_index=True)

    # ---------------------------------------------------------------
    # DETAILED CUT LIST (cards)
    # ---------------------------------------------------------------

    st.divider()
    st.subheader("Detailed Cut List")

    leather_agg: dict[str, dict] = defaultdict(lambda: {
        "quantity": 0,
        "tannery": "",
        "leather_type": "",
        "color": "",
        "heights": Counter(),
        "orders": [],
        "customers": [],
    })
    for item in kiltie_items:
        key = item["leather"]
        leather_agg[key]["quantity"] += item["quantity"]
        leather_agg[key]["tannery"] = item["tannery"]
        leather_agg[key]["leather_type"] = item["leather_type"]
        leather_agg[key]["color"] = item["color"]
        leather_agg[key]["orders"].append(f"#{item['order']}")
        leather_agg[key]["customers"].append(item["customer"])
        if item["height"]:
            leather_agg[key]["heights"][item["height"]] += item["quantity"]

    sorted_leathers = sorted(leather_agg.items(), key=lambda x: -x[1]["quantity"])

    for leather_name, info in sorted_leathers:
        qty = info["quantity"]
        order_list = ", ".join(sorted(set(info["orders"])))
        customer_list = ", ".join(sorted(set(info["customers"])))
        tannery = info["tannery"]
        ltype = info["leather_type"]
        color_name = info["color"]
        heights = info["heights"]

        # Height display
        if heights:
            height_str = ", ".join(f"{h} x{c}" for h, c in sorted(heights.items()))
        else:
            height_str = "No height specified"

        # Detail line
        detail_parts = []
        if tannery:
            detail_parts.append(tannery)
        if ltype:
            detail_parts.append(ltype)
        if color_name:
            detail_parts.append(color_name)
        detail_line = " / ".join(detail_parts) if detail_parts else ""

        # Border color by quantity
        if qty >= 3:
            border_color = "#c0392b"
            bg = "rgba(192, 57, 43, 0.04)"
        elif qty >= 2:
            border_color = "#d4a017"
            bg = "rgba(212, 160, 23, 0.04)"
        else:
            border_color = "#27ae60"
            bg = "rgba(39, 174, 96, 0.04)"

        st.markdown(
            f"<div style='border-left: 5px solid {border_color}; "
            f"padding: 12px 16px; margin-bottom: 8px; "
            f"background: {bg}; border-radius: 4px;'>"
            f"<div style='display:flex; justify-content:space-between; align-items:baseline;'>"
            f"<div>"
            f"<span style='font-size: 1.4em; font-weight: bold; color:{border_color};'>{qty}x</span> "
            f"<span style='font-size: 1.1em; font-weight: 600;'>{leather_name}</span>"
            f"</div>"
            f"<div style='font-size: 0.95em;'>{height_str}</div>"
            f"</div>"
            f"<div style='margin-top: 4px; font-size: 0.85em; color: #666;'>"
            f"{detail_line}</div>"
            f"<div style='margin-top: 3px; font-size: 0.8em; color: #888;'>"
            f"Orders: {order_list} -- {customer_list}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # ---------------------------------------------------------------
    # Chart
    # ---------------------------------------------------------------

    st.divider()
    cut_df = pd.DataFrame([
        {"Leather": name, "Quantity": info["quantity"], "Tannery": info["tannery"] or "Unknown"}
        for name, info in sorted_leathers
    ])
    fig = px.bar(
        cut_df,
        x="Quantity",
        y="Leather",
        orientation="h",
        color="Tannery",
        title="Kilties to Cut",
    )
    fig.update_layout(
        yaxis=dict(autorange="reversed"),
        height=max(300, len(cut_df) * 40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # ---------------------------------------------------------------
    # Full order detail table
    # ---------------------------------------------------------------

    st.divider()
    st.subheader("Order Details")

    detail_df = pd.DataFrame(kiltie_items)
    detail_df = detail_df.rename(columns={
        "order": "Order #",
        "date": "Date",
        "customer": "Customer",
        "state": "State",
        "status": "Status",
        "leather": "Leather",
        "height": "Height",
        "tannery": "Tannery",
        "quantity": "Qty",
        "price": "Price",
    })
    display_cols = [
        "Order #", "Date", "Customer", "State", "Status",
        "Leather", "Height", "Tannery", "Qty", "Price",
    ]
    available_cols = [c for c in display_cols if c in detail_df.columns]
    st.dataframe(
        detail_df[available_cols].sort_values("Order #", ascending=False),
        use_container_width=True,
        hide_index=True,
    )

# ===================================================================
# Kiltie Inventory
# ===================================================================

st.divider()
with st.expander("Kiltie Inventory Snapshot"):
    if kiltie_products:
        inv_rows = []
        for pid, info in kiltie_products.items():
            inv_rows.append({
                "Leather": info["title"],
                "Tannery": info["tannery"],
                "Type": info["leather_type"],
                "Price": f"${float(info['price']):,.2f}",
                "In Stock": info["inventory"],
            })
        inv_df = pd.DataFrame(inv_rows).sort_values("In Stock", ascending=True)

        oos = inv_df[inv_df["In Stock"] <= 0]
        low = inv_df[(inv_df["In Stock"] > 0) & (inv_df["In Stock"] <= 3)]

        ic1, ic2, ic3 = st.columns(3)
        ic1.metric("Total Kiltie SKUs", len(inv_df))
        ic2.metric("Out of Stock", len(oos))
        ic3.metric("Low Stock (3 or less)", len(low))

        st.dataframe(inv_df, use_container_width=True, hide_index=True)
    else:
        st.info("No kiltie products found.")

st.divider()
st.caption(
    f"Last refreshed: {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
    f"Checked {len(filtered_orders)} {filter_label} orders | "
    f"Data from Shopify Admin API"
)
