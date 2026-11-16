BEZAWADADEALS v13.0.0

FILES
- app.py
- requirements.txt
- templates/index.html

RENDER BUILD COMMAND
pip install -r requirements.txt

RENDER START COMMAND
uvicorn app:app --host 0.0.0.0 --port $PORT

RENDER ENVIRONMENT VARIABLES
DATABASE_URL = your existing Render PostgreSQL Internal Database URL
DATA_GOV_IN_API_KEY = your data.gov.in API key (required for AGMARKNET)
SHOP_OWNER_TOKEN = a long private secret shared only with approved shop / Rythu Bazaar operators
ADMIN_TOKEN = a different long private secret for admin price verification and manual refresh

WHAT THIS VERSION DOES
- One unified retail price tracker for all 50 categories.
- Area, VMC ward, product, category, and source filters.
- Owner-submitted Supermarket / Rythu Bazaar / Local Shop prices.
- Owner-submitted prices expire after 24 hours and are labelled Owner-confirmed; admin can mark a record Verified.
- Expired prices remain visible with an Expired label unless Current only is selected.
- AGMARKNET daily wholesale mandi prices for Andhra Pradesh NTR / legacy Krishna records, when the API key works.
- Department of Consumer Affairs daily All-India retail benchmark, shown separately and never presented as a Vijayawada shop price.
- VMC 86-ward boundary text import attempt from the 14-Sep-2026 Gazette archive transcript. Boundary text is shown as boundary reference, not as a guaranteed street-to-ward geocoder.
- Page data refresh every 5 minutes; backend source sync runs hourly.

IMPORTANT HONEST LIMITATIONS
1. There is no verified free government API discovered that supplies every supermarket's live selling price for all 50 categories in Vijayawada.
2. Department of Consumer Affairs benchmark covers selected essential commodities and is All-India average; it is not a Vijayawada area-specific shop price.
3. AGMARKNET is mandi wholesale data (usually INR per quintal), not Rythu Bazaar retail data.
4. Exact locality-to-ward assignment requires checking the Gazette boundary or authoritative VMC ward GIS data. The app does not invent area mappings. Shop/operator entries record the area and ward they submit.
5. Rythu Bazaar retail prices and supermarket prices appear only after authorized operators submit them or a real store feed is connected.
6. Source websites may change their HTML/API, so check /api/status and Render logs. A failed sync is shown as an error, not hidden.

ENDPOINTS
/health
/api/categories
/api/wards
/api/localities
/api/shops
/api/unified-price-tracker
/api/market-prices
/api/official-retail-prices
/api/offers
/api/status
/api/official-sources

ADMIN
POST /api/admin/refresh with header X-Admin-Token: ADMIN_TOKEN
POST /api/admin/prices/{price_id}/verify with header X-Admin-Token: ADMIN_TOKEN

SHOP OWNER
POST /api/shop/price-update with header X-Shop-Token: SHOP_OWNER_TOKEN

This code has been syntax checked. It has not been connected to your live Render database or credentials, so do not assume deployment success until /health and /api/status are checked.
