BEZAWADADEALS v12.0.0

FILES
- app.py
- templates/index.html
- requirements.txt
- .env.example

RENDER SETTINGS
Build command:
  pip install -r requirements.txt
Start command:
  uvicorn app:app --host 0.0.0.0 --port $PORT

REQUIRED ENVIRONMENT VARIABLES
- DATABASE_URL: Render PostgreSQL Internal Database URL (or the existing DATABASE_URL)
- DATA_GOV_IN_API_KEY: free API key from data.gov.in; without it mandi feed sync reports an error
- SHOP_OWNER_TOKEN: secret token required by the shop-owner price submission form
- ADMIN_TOKEN: secret token for protected admin endpoints

IMPORTANT DATA LIMITATIONS
- AGMARKNET is a daily wholesale mandi dataset. It is not a supermarket retail-price feed and is not a Rythu Bazaar retail-price feed.
- Supermarket and Rythu Bazaar retail entries in this version come only from confirmed owner submissions. No public, free, official all-supermarket retail API was verified.
- No fabricated sample prices are seeded.
- Ward numbers 1-86 are created, but exact locality-to-ward assignment is NOT auto-filled. Import official ward boundary descriptions via POST /api/admin/wards/import; localities are added from owner submissions.
- This package has been Python syntax-checked only. It has not been tested against your live Render PostgreSQL schema or credentials.
