BEZAWADADEALS DEPLOY PATCH

Replace in your repository:
- market_prices.py
- bulk_import.py
- requirements.txt

IMPORTANT: Your current app.py already contains the core API and homepage. Keep it unchanged for this patch, but change its AGMARKNET router registration so it imports this router. The live AGMARKNET path is now /api/agmarknet/market-prices; keep the existing database /api/market-prices path unchanged. This avoids duplicate routes.

Render build command: pip install -r requirements.txt
Render start command: uvicorn app:app --host 0.0.0.0 --port $PORT
Render env vars: DATABASE_URL, ADMIN_TOKEN, SHOP_OWNER_TOKEN, and optional AGMARKNET_API_KEY.

Data safety: no DROP/TRUNCATE or seeded fake records. Existing database rows are not deleted. Bulk-imported shops are marked unverified. AGMARKNET is wholesale only, not local shop retail prices.

IMPORTANT: This is a compatibility patch package, not a full redesign of app.py or templates/index.html. Keep your existing files unless you separately choose to replace the frontend.
