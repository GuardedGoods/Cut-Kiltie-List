# Cut-Kiltie-List — agent notes

## Git workflow

**Push directly to `main`.** The repo owner has authorized this as the
default. No feature branches, no PRs unless explicitly requested. Commit on
`main`, push to `origin/main`.

## App

Single-file Streamlit app (`app.py`) that pulls unfulfilled Shopify orders
and renders a mobile-first, Guarded Goods-branded leather cut list. No
customer PII is surfaced in the UI — only leather name, photo, qty, heights,
and order numbers in the per-order expander.
