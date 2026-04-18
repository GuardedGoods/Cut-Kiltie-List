"""
Kiltie Cut List -- Live production planning for Guarded Goods.
Pulls unfulfilled orders from Shopify, extracts Boot Kiltie line items
with leather type and height, and aggregates into a cutting guide.
Mobile-first. No customer data surfaced in the UI.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
import html
import re

import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Cut List -- Guarded Goods",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="collapsed",
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
# Branded CSS (Guarded Goods palette, mobile-first)
# ===================================================================

st.markdown(
    """
    <style>
    :root {
        --gg-bg:       #faf7f2;
        --gg-surface:  #ffffff;
        --gg-ink:      #1a1a1a;
        --gg-muted:    #6b6b6b;
        --gg-line:     #e5dfd4;
        --gg-accent:   #6b4423;
        --gg-urgent:   #8b2e2e;
        --gg-warn:     #a67c2a;
        --gg-ok:       #4a6b3a;
        --gg-font:     "Inter", "Helvetica Neue", system-ui, -apple-system, sans-serif;
    }

    html, body, [data-testid="stAppViewContainer"],
    [data-testid="stApp"], .main, .block-container {
        background: var(--gg-bg) !important;
        color: var(--gg-ink);
        font-family: var(--gg-font);
    }

    .block-container {
        padding-top: 1.25rem !important;
        padding-bottom: 3rem !important;
        max-width: 820px !important;
    }

    /* Branded header */
    .gg-eyebrow {
        font-size: 11px;
        letter-spacing: 0.22em;
        text-transform: uppercase;
        color: var(--gg-muted);
        font-weight: 600;
        margin-bottom: 2px;
    }
    .gg-title {
        font-size: 28px;
        font-weight: 700;
        letter-spacing: -0.01em;
        color: var(--gg-ink);
        margin: 0 0 4px 0;
    }
    .gg-subtitle {
        font-size: 13px;
        color: var(--gg-muted);
        margin-bottom: 18px;
    }

    /* Metric tiles */
    [data-testid="stMetric"] {
        background: var(--gg-surface);
        border: 1px solid var(--gg-line);
        border-radius: 2px;
        padding: 12px 14px;
    }
    [data-testid="stMetricLabel"] {
        font-size: 10px !important;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--gg-muted) !important;
        font-weight: 600;
    }
    [data-testid="stMetricValue"] {
        font-size: 26px !important;
        font-weight: 700 !important;
        color: var(--gg-ink) !important;
    }

    /* Leather card */
    .gg-card {
        background: var(--gg-surface);
        border: 1px solid var(--gg-line);
        border-left: 4px solid var(--gg-ok);
        border-radius: 2px;
        padding: 14px;
        margin-bottom: 10px;
        display: flex;
        align-items: center;
        gap: 14px;
        cursor: pointer;
        transition: opacity .18s ease, padding .18s ease;
        user-select: none;
        -webkit-tap-highlight-color: transparent;
    }
    .gg-card.qty-warn  { border-left-color: var(--gg-warn); }
    .gg-card.qty-urgent { border-left-color: var(--gg-urgent); }

    .gg-card.cut {
        opacity: 0.45;
        padding-top: 8px;
        padding-bottom: 8px;
    }
    .gg-card.cut .gg-leather { text-decoration: line-through; }
    .gg-card.cut .gg-heights,
    .gg-card.cut .gg-age      { display: none; }
    .gg-card.cut .gg-thumb,
    .gg-card.cut .gg-thumb-empty { width: 48px; height: 48px; }

    .gg-thumb {
        width: 88px;
        height: 88px;
        object-fit: cover;
        border-radius: 2px;
        flex-shrink: 0;
        background: var(--gg-bg);
        border: 1px solid var(--gg-line);
        cursor: zoom-in;
    }
    .gg-thumb-empty {
        width: 88px;
        height: 88px;
        border-radius: 2px;
        flex-shrink: 0;
        background: var(--gg-bg);
        border: 1px solid var(--gg-line);
    }

    .gg-body { flex: 1; min-width: 0; }

    .gg-row-top {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: 10px;
    }
    .gg-leather {
        font-size: 17px;
        font-weight: 600;
        color: var(--gg-ink);
        line-height: 1.25;
        word-break: break-word;
    }
    .gg-qty {
        font-size: 28px;
        font-weight: 700;
        color: var(--gg-accent);
        line-height: 1;
        white-space: nowrap;
    }
    .gg-qty .x {
        font-size: 14px;
        font-weight: 500;
        color: var(--gg-muted);
        margin-left: 2px;
    }

    .gg-heights {
        font-size: 14px;
        font-weight: 500;
        color: var(--gg-ink);
        margin-top: 6px;
    }
    .gg-heights .h-pill {
        display: inline-block;
        background: var(--gg-bg);
        border: 1px solid var(--gg-line);
        border-radius: 2px;
        padding: 2px 8px;
        margin: 2px 4px 0 0;
        font-size: 13px;
    }

    .gg-age {
        font-size: 10px;
        letter-spacing: 0.18em;
        text-transform: uppercase;
        color: var(--gg-muted);
        margin-top: 6px;
    }

    /* Photo modal (full-screen image on thumbnail tap) */
    .gg-modal {
        position: fixed; inset: 0;
        background: rgba(0,0,0,0.88);
        z-index: 9999;
        display: flex; align-items: center; justify-content: center;
        cursor: zoom-out;
    }
    .gg-modal img {
        max-width: 95vw; max-height: 95vh;
        object-fit: contain;
        border-radius: 2px;
        box-shadow: 0 0 40px rgba(0,0,0,0.5);
    }

    /* Print block: hidden on screen, shown by @media print rules below */
    #gg-print { display: none; }

    /* Streamlit expander -> match GG minimal style */
    [data-testid="stExpander"] {
        border: 1px solid var(--gg-line) !important;
        border-radius: 2px !important;
        background: var(--gg-surface) !important;
        margin-top: -8px;
        margin-bottom: 14px;
    }
    [data-testid="stExpander"] summary {
        font-size: 12px;
        letter-spacing: 0.14em;
        text-transform: uppercase;
        color: var(--gg-muted) !important;
    }

    /* Controls */
    [data-testid="stSelectbox"] label,
    [data-testid="stButton"] button {
        font-family: var(--gg-font);
    }
    [data-testid="stButton"] button[kind="primary"] {
        background: var(--gg-ink);
        border: 1px solid var(--gg-ink);
        color: #fff;
        border-radius: 2px;
        font-weight: 600;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        font-size: 12px;
    }
    [data-testid="stButton"] button[kind="primary"]:hover {
        background: var(--gg-accent);
        border-color: var(--gg-accent);
    }

    hr { border-color: var(--gg-line) !important; }

    /* Mobile */
    @media (max-width: 640px) {
        .block-container {
            padding-left: 0.75rem !important;
            padding-right: 0.75rem !important;
        }
        .gg-title { font-size: 24px; }
        .gg-thumb, .gg-thumb-empty { width: 72px; height: 72px; }
        .gg-leather { font-size: 16px; }
        .gg-qty { font-size: 26px; }
        [data-testid="stMetricValue"] { font-size: 22px !important; }
    }

    /* Print: only the aggregated table */
    @media print {
        header, footer, [data-testid="stSidebar"],
        [data-testid="stToolbar"], [data-testid="stDecoration"],
        [data-testid="stStatusWidget"], .stDeployButton,
        [data-testid="manage-app-button"] { display: none !important; }

        .block-container > div > div > div > div { display: none !important; }

        #gg-print, #gg-print * {
            display: block !important;
            visibility: visible !important;
        }
        #gg-print { position: absolute; top: 0; left: 0; width: 100%; }
        #gg-print table {
            width: 100%;
            border-collapse: collapse;
            font-size: 12pt;
        }
        #gg-print th, #gg-print td {
            border: 1px solid #333;
            padding: 8px 10px;
            text-align: left;
        }
        #gg-print th { background: #f0f0f0 !important; font-weight: 700; }
        #gg-print h2 { font-size: 18pt; margin-bottom: 6px; }
        #gg-print .print-date { font-size: 10pt; color: #666; margin-bottom: 14px; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ===================================================================
# Header
# ===================================================================

st.markdown(
    """
    <div class="gg-eyebrow">Guarded Goods</div>
    <div class="gg-title">Cut List</div>
    <div class="gg-subtitle">Live from Shopify &middot; updates as orders fulfill.</div>
    """,
    unsafe_allow_html=True,
)

# Controls
c1, c2 = st.columns([3, 1])
with c1:
    order_filter = st.selectbox(
        "Show",
        [
            "Unfulfilled orders",
            "Today",
            "Yesterday",
            "Last 3 days",
            "Last 7 days",
            "All orders (60 days)",
            "All orders (90 days)",
        ],
        label_visibility="collapsed",
    )
with c2:
    if st.button("Refresh", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# Map filter -> API lookback + optional client-side date window.
# API lookback is coarse (7/60/90) so cache keys stay small.
today = date.today()
if order_filter in ("Today", "Yesterday", "Last 3 days", "Last 7 days"):
    lookup_days = 8  # buffer so tz-skewed orders on the boundary still pull
elif "90" in order_filter:
    lookup_days = 90
else:
    lookup_days = 60

if order_filter == "Today":
    date_min = date_max = today
elif order_filter == "Yesterday":
    date_min = date_max = today - timedelta(days=1)
elif order_filter == "Last 3 days":
    date_min, date_max = today - timedelta(days=2), today
elif order_filter == "Last 7 days":
    date_min, date_max = today - timedelta(days=6), today
elif "90" in order_filter:
    date_min, date_max = today - timedelta(days=90), today
elif "60" in order_filter:
    date_min, date_max = today - timedelta(days=60), today
else:
    date_min = date_max = None  # Unfulfilled filter ignores the date window

# ===================================================================
# Load + shape data
# ===================================================================

with st.spinner("Pulling live data from Shopify..."):
    try:
        products = get_products()
        orders = get_orders(days=lookup_days)
    except Exception as exc:
        st.error(f"Could not connect to Shopify: {exc}")
        st.stop()

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

        images = p.get("images", [])
        image_url = images[0].get("src", "") if images else ""

        kiltie_products[p["id"]] = {
            "title": title,
            "tannery": tannery,
            "leather_type": leather_type,
            "color": color,
            "price": p.get("variants", [{}])[0].get("price", "0"),
            "inventory": p.get("variants", [{}])[0].get("inventory_quantity", 0),
            "image_url": image_url,
        }

if "Unfulfilled" in order_filter:
    filtered_orders = [
        o for o in orders
        if o.get("fulfillment_status") in (None, "null", "partial", "")
        and o.get("financial_status") in ("paid", "authorized", "partially_paid")
    ]
    filter_label = "unfulfilled"
else:
    dmin = date_min.isoformat() if date_min else ""
    dmax = date_max.isoformat() if date_max else ""
    filtered_orders = [
        o for o in orders
        if o.get("financial_status") not in ("voided", "refunded")
        and dmin <= (o.get("created_at", "") or "")[:10] <= dmax
    ]
    filter_label = order_filter.lower()

kiltie_items: list[dict] = []
for o in filtered_orders:
    order_num = o.get("order_number", o["id"])
    order_date = o.get("created_at", "")[:10]

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
            if not kiltie_height:
                kiltie_height = '6"'  # default when the customer didn't pick one

            kiltie_items.append({
                "order": order_num,
                "date": order_date,
                "leather": li.get("title", prod_info.get("title", "Unknown")),
                "height": kiltie_height,
                "quantity": li.get("quantity", 1),
                "image_url": prod_info.get("image_url", ""),
            })

# ===================================================================
# Display
# ===================================================================

if not kiltie_items:
    st.success(
        f"No kilties to cut. Checked {len(filtered_orders)} "
        f"{filter_label} orders."
    )
else:
    total_kilties = sum(i["quantity"] for i in kiltie_items)
    unique_leathers = len(set(i["leather"] for i in kiltie_items))
    unique_orders = len(set(i["order"] for i in kiltie_items))

    with st.expander("Summary", expanded=True):
        m1, m2, m3 = st.columns(3)
        m1.metric("Kilties", total_kilties)
        m2.metric("Leathers", unique_leathers)
        m3.metric("Orders", unique_orders)

    # Aggregate by leather
    leather_agg: dict[str, dict] = defaultdict(lambda: {
        "quantity": 0,
        "image_url": "",
        "heights": Counter(),
        "oldest_date": "",
    })
    for item in kiltie_items:
        key = item["leather"]
        leather_agg[key]["quantity"] += item["quantity"]
        if item.get("image_url") and not leather_agg[key]["image_url"]:
            leather_agg[key]["image_url"] = item["image_url"]
        if item["height"]:
            leather_agg[key]["heights"][item["height"]] += item["quantity"]
        d = item.get("date") or ""
        if d and (not leather_agg[key]["oldest_date"] or d < leather_agg[key]["oldest_date"]):
            leather_agg[key]["oldest_date"] = d

    sorted_leathers = sorted(leather_agg.items(), key=lambda x: -x[1]["quantity"])

    today = date.today()

    for leather_name, info in sorted_leathers:
        qty = info["quantity"]
        heights = info["heights"]

        if qty >= 3:
            card_class = "gg-card qty-urgent"
        elif qty >= 2:
            card_class = "gg-card qty-warn"
        else:
            card_class = "gg-card"

        if heights:
            height_pills = "".join(
                f"<span class='h-pill'>{html.escape(h)} &middot; {c}x</span>"
                for h, c in sorted(heights.items(), key=lambda x: -x[1])
            )
        else:
            height_pills = "<span class='h-pill'>No height specified</span>"

        age_html = ""
        od = info.get("oldest_date")
        if od:
            try:
                days = (today - date.fromisoformat(od)).days
                if days >= 1:
                    age_html = (
                        f'<div class="gg-age">{days} '
                        f'day{"s" if days != 1 else ""} old</div>'
                    )
            except ValueError:
                pass

        img_url = info.get("image_url", "")
        img_url_attr = html.escape(img_url, quote=True)
        thumb = (
            f"<img class='gg-thumb' src='{img_url_attr}' alt=''>"
            if img_url else "<div class='gg-thumb-empty'></div>"
        )

        name_attr = html.escape(leather_name, quote=True)
        name_html = html.escape(leather_name)

        st.markdown(
            f"""
            <div class="{card_class}" data-leather="{name_attr}">
                {thumb}
                <div class="gg-body">
                    <div class="gg-row-top">
                        <div class="gg-leather">{name_html}</div>
                        <div class="gg-qty">{qty}<span class="x">x</span></div>
                    </div>
                    <div class="gg-heights">{height_pills}</div>
                    {age_html}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ---------------------------------------------------------------
    # Print view: always rendered, hidden on screen via CSS,
    # revealed by @media print. Cmd/Ctrl+P just works.
    # ---------------------------------------------------------------
    table_html = (
        "<table><thead><tr>"
        "<th style='width:60px;'></th><th>Leather</th><th>Qty</th><th>Heights</th>"
        "</tr></thead><tbody>"
    )
    for leather_name, info in sorted(leather_agg.items(), key=lambda x: x[0]):
        height_parts = [
            f"{html.escape(h)} x{c}" for h, c in sorted(info["heights"].items())
        ]
        height_str = ", ".join(height_parts) if height_parts else "--"
        img = info.get("image_url", "")
        img_td = (
            f"<img src='{html.escape(img, quote=True)}' style='width:50px;"
            f"height:50px;object-fit:cover;border-radius:2px;'>" if img else ""
        )
        table_html += (
            f"<tr><td>{img_td}</td>"
            f"<td><strong>{html.escape(leather_name)}</strong></td>"
            f"<td>{info['quantity']}</td>"
            f"<td>{height_str}</td></tr>"
        )
    table_html += "</tbody></table>"

    st.markdown(
        f"""
        <div id="gg-print">
            <h2>Kiltie Cut List</h2>
            <div class="print-date">{datetime.now().strftime('%B %d, %Y')}
                | {total_kilties} kilties across {unique_orders} orders</div>
            {table_html}
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown(
    f"""
    <div style='margin-top:24px;font-size:11px;color:var(--gg-muted);
                letter-spacing:0.12em;text-transform:uppercase;'>
        Last refreshed {datetime.now().strftime('%Y-%m-%d %H:%M')}
        &middot; {len(filtered_orders)} {filter_label} orders
    </div>
    """,
    unsafe_allow_html=True,
)

# ===================================================================
# Client-side enhancements:
#   - tap a card to mark/unmark as cut (persisted in localStorage)
#   - tap thumbnail to view full-size photo
#   - iOS "Add to Home Screen" polish via injected meta tags
#   - auto-refresh every 3 minutes
# Rendered with st.html so the script executes in the parent DOM.
# ===================================================================

st.html(
    """
    <script>
    (function () {
      const KEY = 'gg-cut-leathers';
      const cut = new Set(JSON.parse(localStorage.getItem(KEY) || '[]'));
      const persist = () => localStorage.setItem(KEY, JSON.stringify([...cut]));

      function showModal(src) {
        const m = document.createElement('div');
        m.className = 'gg-modal';
        const img = document.createElement('img');
        img.src = src;
        m.appendChild(img);
        m.addEventListener('click', () => m.remove());
        document.body.appendChild(m);
      }

      function wire() {
        document.querySelectorAll('.gg-card[data-leather]').forEach(card => {
          const name = card.dataset.leather;
          if (cut.has(name)) card.classList.add('cut');
          card.querySelectorAll('.gg-thumb').forEach(t => {
            t.onclick = (e) => { e.stopPropagation(); showModal(t.src); };
          });
          card.onclick = () => {
            if (cut.has(name)) { cut.delete(name); card.classList.remove('cut'); }
            else                { cut.add(name);    card.classList.add('cut'); }
            persist();
          };
        });
      }

      // iOS home-screen polish
      [
        ['apple-mobile-web-app-capable', 'yes'],
        ['apple-mobile-web-app-status-bar-style', 'black-translucent'],
        ['apple-mobile-web-app-title', 'Cut List'],
        ['theme-color', '#faf7f2'],
      ].forEach(([n, c]) => {
        if (!document.querySelector('meta[name="' + n + '"]')) {
          const m = document.createElement('meta');
          m.name = n; m.content = c;
          document.head.appendChild(m);
        }
      });

      // Auto-refresh once every 3 minutes (guard so reruns don't stack timers)
      if (!window.__ggReload) {
        window.__ggReload = setTimeout(() => location.reload(), 180000);
      }

      wire();
      // Streamlit reruns may rebuild cards -- re-wire when DOM changes.
      new MutationObserver(wire).observe(
        document.body, { childList: true, subtree: true }
      );
    })();
    </script>
    """
)
