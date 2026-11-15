import os, json, secrets, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request

APP_VERSION = "10.0.1"
TOTAL_WARDS = 86
AGMARKNET_RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"
CATEGORIES = [
"Vegetables","Fruits","Groceries","Rice & Pulses","Dairy & Bakery","Meat & Seafood","Supermarkets","Wholesale Stores","Shopping Malls","Department Stores","Restaurants","Fast Food","Cafes & Tea","Juice & Ice Cream","Sweets & Bakeries","Electronics","Mobile Stores","Computers","Home Appliances","Fashion & Clothing","Footwear","Bags","Jewellery","Accessories","Cosmetics","Medical Stores","Hospitals & Clinics","Optical Stores","Petrol Bunks","Auto Sales & Repair","Hardware","Electrical Stores","Furniture","Home & Kitchen","Books & Stationery","Toys & Baby Products","Sports & Fitness","Flowers","Gifts","Salons & Beauty","Laundry","Photo & Printing","Banks & ATMs","Internet & Mobile Services","Repairs & Services","Education & Coaching","Travel & Transport","Local Services","Pet Shops","Other Shops"]
CATEGORY_SET = set(CATEGORIES)
DATABASE_URL = os.getenv("DATABASE_URL", "")
API_KEY = os.getenv("DATA_GOV_IN_API_KEY", "")
app = FastAPI(title="BezawadaDeals", version=APP_VERSION)
templates = Jinja2Templates(directory="templates")


def db():
    if not DATABASE_URL:
        raise HTTPException(503, "DATABASE_URL environment variable is not configured")
    return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)


def init_db():
    if not DATABASE_URL:
        return
    sql = """
    CREATE TABLE IF NOT EXISTS wards (id SERIAL PRIMARY KEY, ward_number INTEGER UNIQUE NOT NULL, ward_name TEXT);
    CREATE TABLE IF NOT EXISTS shops (
      id BIGSERIAL PRIMARY KEY, shop_name TEXT NOT NULL, category TEXT NOT NULL, ward_id INTEGER REFERENCES wards(id),
      address TEXT, phone TEXT, owner_name TEXT, locality TEXT, image_url TEXT, is_verified BOOLEAN DEFAULT FALSE,
      shop_update_token TEXT UNIQUE, created_at TIMESTAMPTZ DEFAULT NOW()
    );
    ALTER TABLE shops ADD COLUMN IF NOT EXISTS locality TEXT;
    ALTER TABLE shops ADD COLUMN IF NOT EXISTS shop_update_token TEXT;
    ALTER TABLE shops ADD COLUMN IF NOT EXISTS owner_name TEXT;
    ALTER TABLE shops ADD COLUMN IF NOT EXISTS image_url TEXT;
    ALTER TABLE shops ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;
    CREATE TABLE IF NOT EXISTS products (
      id BIGSERIAL PRIMARY KEY, product_name TEXT NOT NULL, category TEXT NOT NULL, unit TEXT NOT NULL DEFAULT 'piece',
      description TEXT
    );
    CREATE TABLE IF NOT EXISTS prices (
      id BIGSERIAL PRIMARY KEY, shop_id BIGINT REFERENCES shops(id) ON DELETE CASCADE,
      product_id BIGINT REFERENCES products(id), price NUMERIC(12,2) NOT NULL CHECK(price >= 0),
      price_type TEXT DEFAULT 'retail', source_name TEXT, source_type TEXT NOT NULL,
      is_live BOOLEAN DEFAULT TRUE, in_store_confirmed BOOLEAN DEFAULT FALSE,
      valid_until TIMESTAMPTZ, updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS prices_product_updated_idx ON prices(product_id, updated_at DESC);
    CREATE INDEX IF NOT EXISTS prices_shop_updated_idx ON prices(shop_id, updated_at DESC);
    CREATE TABLE IF NOT EXISTS offers (
      id BIGSERIAL PRIMARY KEY, shop_id BIGINT REFERENCES shops(id) ON DELETE CASCADE,
      title TEXT NOT NULL, description TEXT, discount_text TEXT,
      valid_from TIMESTAMPTZ DEFAULT NOW(), valid_until TIMESTAMPTZ,
      is_active BOOLEAN DEFAULT TRUE, updated_at TIMESTAMPTZ DEFAULT NOW()
    );
    """
    with db() as conn:
        conn.execute(sql)
        # Safe migration: CREATE TABLE IF NOT EXISTS does not add columns
        # to an existing Render PostgreSQL table created by an older version.
        conn.execute("ALTER TABLE wards ADD COLUMN IF NOT EXISTS ward_name TEXT")
        for n in range(1, TOTAL_WARDS + 1):
            conn.execute("INSERT INTO wards(ward_number,ward_name) VALUES(%s,%s) ON CONFLICT(ward_number) DO NOTHING", (n, f"Ward {n}"))


@app.on_event("startup")
def startup():
    init_db()


def status_for(row):
    if row.get("valid_until") and row["valid_until"] < datetime.now(timezone.utc):
        return "Expired"
    if row.get("source_type") == "shop_owner_confirmed" and row.get("in_store_confirmed"):
        return "Verified"
    if row.get("source_type") == "agmarknet_wholesale":
        return "Government wholesale"
    return "Estimated"


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "categories": CATEGORIES})

@app.get("/health")
def health():
    return {"ok": True, "version": APP_VERSION}

@app.get("/api/status")
def api_status():
    connected = False
    try:
        with db() as conn:
            conn.execute("SELECT 1")
            connected = True
    except Exception:
        pass
    return {"ok": True, "version": APP_VERSION, "database_connected": connected, "wards": TOTAL_WARDS, "categories": len(CATEGORIES)}

@app.get("/api/categories")
def categories():
    return {"categories": CATEGORIES}

@app.get("/api/wards")
def wards():
    return {"wards": [{"ward_number": i, "ward_name": f"Ward {i}"} for i in range(1, TOTAL_WARDS+1)]}

@app.post("/api/shops")
def create_shop(payload: dict):
    name = str(payload.get("shop_name", "")).strip()
    category = str(payload.get("category", "")).strip()
    ward = payload.get("ward_number")
    if not name or category not in CATEGORY_SET:
        raise HTTPException(400, "Valid shop_name and category are required")
    try: ward = int(ward)
    except Exception: raise HTTPException(400, "ward_number must be 1-86")
    if not 1 <= ward <= TOTAL_WARDS: raise HTTPException(400, "ward_number must be 1-86")
    token = secrets.token_urlsafe(24)
    with db() as conn:
        w = conn.execute("SELECT id FROM wards WHERE ward_number=%s", (ward,)).fetchone()
        row = conn.execute("""INSERT INTO shops(shop_name,category,ward_id,address,phone,owner_name,locality,image_url,shop_update_token)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,shop_name,category,shop_update_token""",
          (name, category, w["id"], payload.get("address"), payload.get("phone"), payload.get("owner_name"), payload.get("locality"), payload.get("image_url"), token)).fetchone()
    return row

@app.get("/api/shops")
def list_shops(ward_number: int = Query(..., ge=1, le=86), category: str = "All"):
    if category != "All" and category not in CATEGORY_SET: raise HTTPException(400, "Unknown category")
    with db() as conn:
        rows = conn.execute("""SELECT s.id,s.shop_name,s.category,s.address,s.phone,s.owner_name,s.locality,s.image_url,s.is_verified,w.ward_number,
          (SELECT COUNT(*) FROM prices p WHERE p.shop_id=s.id AND p.is_live=TRUE) AS price_count
          FROM shops s JOIN wards w ON w.id=s.ward_id
          WHERE w.ward_number=%s AND (%s::text='All' OR s.category=%s::text)
          ORDER BY s.shop_name""", (ward_number, category, category)).fetchall()
    return {"shops": rows}

@app.get("/api/shops/{shop_id}/products")
def shop_products(shop_id: int):
    with db() as conn:
        rows = conn.execute("""SELECT p.product_name,p.category,p.unit,pr.price,pr.source_type,pr.updated_at,pr.valid_until,pr.in_store_confirmed
          FROM prices pr JOIN products p ON p.id=pr.product_id WHERE pr.shop_id=%s AND pr.is_live=TRUE
          ORDER BY pr.updated_at DESC""", (shop_id,)).fetchall()
    for r in rows: r["status"] = status_for(r)
    return {"products": rows}

@app.post("/api/shop/price-update")
def shop_price_update(payload: dict, x_shop_token: Optional[str] = Header(None)):
    if not x_shop_token: raise HTTPException(401, "X-Shop-Token header required")
    name = str(payload.get("product_name", "")).strip()
    category = str(payload.get("category", "")).strip()
    unit = str(payload.get("unit", "piece")).strip() or "piece"
    try: price = float(payload.get("price"))
    except Exception: raise HTTPException(400, "Valid price required")
    if price < 0 or not name or category not in CATEGORY_SET: raise HTTPException(400, "Valid product, category and non-negative price required")
    if payload.get("confirm_same_as_shop_price") is not True:
        raise HTTPException(400, "Owner must confirm that this is the current in-store price")
    with db() as conn:
        shop = conn.execute("SELECT id,category FROM shops WHERE shop_update_token=%s", (x_shop_token,)).fetchone()
        if not shop: raise HTTPException(403, "Invalid shop token")
        if shop["category"] != category: raise HTTPException(400, "Product category must match shop category")
        product = conn.execute("SELECT id FROM products WHERE LOWER(product_name)=LOWER(%s) AND LOWER(category)=LOWER(%s) AND LOWER(unit)=LOWER(%s) ORDER BY id LIMIT 1", (name,category,unit)).fetchone()
        if not product:
            product = conn.execute("INSERT INTO products(product_name,category,unit) VALUES(%s,%s,%s) RETURNING id", (name,category,unit)).fetchone()
        conn.execute("""INSERT INTO prices(shop_id,product_id,price,price_type,source_name,source_type,is_live,in_store_confirmed,valid_until,updated_at)
          VALUES(%s,%s,%s,'retail','Shop owner','shop_owner_confirmed',TRUE,TRUE,NOW()+INTERVAL '24 hours',NOW())""", (shop["id"],product["id"],price))
    return {"ok": True, "message": "Price update saved", "status": "Verified", "valid_for_hours": 24}

@app.get("/api/price-comparison")
def price_comparison(product: str = Query(..., min_length=1), unit: Optional[str] = None, ward_number: Optional[int] = Query(None, ge=1, le=86), sort: str = "low"):
    order = "ASC" if sort.lower() != "high" else "DESC"
    sql = f"""SELECT p.product_name,p.category,p.unit,pr.price,pr.source_type,pr.in_store_confirmed,pr.valid_until,pr.updated_at,
      s.id shop_id,s.shop_name,s.address,s.phone,w.ward_number
      FROM prices pr JOIN products p ON p.id=pr.product_id JOIN shops s ON s.id=pr.shop_id JOIN wards w ON w.id=s.ward_id
      WHERE pr.is_live=TRUE AND LOWER(p.product_name)=LOWER(%s)
      AND (%s::text IS NULL OR LOWER(p.unit)=LOWER(%s::text))
      AND (%s::integer IS NULL OR w.ward_number=%s::integer)
      ORDER BY pr.price {order},pr.updated_at DESC"""
    with db() as conn:
        rows = conn.execute(sql, (product,unit,unit,ward_number,ward_number)).fetchall()
    for r in rows: r["status"] = status_for(r)
    return {"product": product, "count": len(rows), "prices": rows}

@app.get("/api/unified-price-tracker")
def unified_tracker(category: str = "All", ward_number: Optional[int] = Query(None, ge=1, le=86), product: Optional[str] = None, sort: str = "low"):
    if category != "All" and category not in CATEGORY_SET: raise HTTPException(400, "Unknown category")
    order = "ASC" if sort.lower() != "high" else "DESC"
    sql = f"""SELECT p.product_name,p.category,p.unit,pr.price,pr.source_type,pr.source_name,pr.in_store_confirmed,pr.valid_until,pr.updated_at,
      s.id shop_id,s.shop_name,s.address,s.phone,s.locality,w.ward_number
      FROM prices pr JOIN products p ON p.id=pr.product_id JOIN shops s ON s.id=pr.shop_id JOIN wards w ON w.id=s.ward_id
      WHERE pr.is_live=TRUE AND (%s::text='All' OR LOWER(p.category)=LOWER(%s::text))
      AND (%s::integer IS NULL OR w.ward_number=%s::integer)
      AND (%s::text IS NULL OR LOWER(p.product_name)=LOWER(%s::text))
      ORDER BY p.product_name,pr.price {order},pr.updated_at DESC LIMIT 1000"""
    with db() as conn:
        rows = conn.execute(sql,(category,category,ward_number,ward_number,product,product)).fetchall()
    for r in rows: r["status"] = status_for(r)
    return {"count":len(rows),"prices":rows,"categories":CATEGORIES}

@app.get("/api/price-tracker")
def price_tracker(category: str = "All", product: Optional[str] = None, ward_number: Optional[int] = Query(None, ge=1, le=86)):
    return unified_tracker(category, ward_number, product, "low")

@app.get("/api/offers")
def offers(ward_number: Optional[int] = Query(None, ge=1, le=86)):
    with db() as conn:
        rows = conn.execute("""SELECT o.id,o.title,o.description,o.discount_text,o.valid_from,o.valid_until,s.shop_name,s.category,w.ward_number
          FROM offers o JOIN shops s ON s.id=o.shop_id JOIN wards w ON w.id=s.ward_id
          WHERE o.is_active=TRUE AND (o.valid_until IS NULL OR o.valid_until>=NOW())
          AND (%s::integer IS NULL OR w.ward_number=%s::integer) ORDER BY o.updated_at DESC LIMIT 300""",(ward_number,ward_number)).fetchall()
    return {"offers":rows}

@app.get("/api/market-prices")
def market_prices(district: str = "Krishna", limit: int = Query(100, ge=1, le=500)):
    if not API_KEY:
        return {"available":False,"source":"AGMARKNET via data.gov.in","message":"DATA_GOV_IN_API_KEY not configured","prices":[]}
    params = urllib.parse.urlencode({"api-key":API_KEY,"format":"json","limit":limit,"filters[state]":"Andhra Pradesh","filters[district]":district})
    url = f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE}?{params}"
    try:
        req=urllib.request.Request(url,headers={"User-Agent":"BezawadaDeals/1.0"})
        with urllib.request.urlopen(req,timeout=18) as response: data=json.loads(response.read().decode("utf-8"))
        out=[]
        for r in data.get("records",[]):
            out.append({"commodity":r.get("commodity"),"market":r.get("market"),"district":r.get("district"),"variety":r.get("variety"),"arrival_date":r.get("arrival_date"),"min_price":r.get("min_price"),"max_price":r.get("max_price"),"modal_price":r.get("modal_price"),"unit":"quintal","status":"Government wholesale","source":"AGMARKNET / data.gov.in"})
        return {"available":True,"source":"AGMARKNET via data.gov.in","note":"Wholesale mandi prices per quintal; these are not shop retail prices.","prices":out}
    except Exception as e:
        return {"available":False,"source":"AGMARKNET via data.gov.in","message":f"Government feed unavailable: {type(e).__name__}","prices":[]}

@app.get("/api/official-sources")
def official_sources():
    return {"categories":[{"category":c,"official_retail_feed":"Data Not Available","retail_estimate_enabled":False,"retail_source":"Shop-owner confirmed updates only"} for c in CATEGORIES],"government_wholesale":{"source":"AGMARKNET / data.gov.in","category":"Agricultural commodities only","unit":"quintal","retail_equivalent":False}}

@app.get("/api/market-feed-status")
def market_feed_status():
    return {"source":"AGMARKNET / data.gov.in","api_key_configured":bool(API_KEY),"scope":"Agricultural mandi wholesale data; not retail prices"}

@app.get("/api/owner-guide")
def owner_guide():
    return {"steps":["Admin creates a shop and securely shares its update token with the owner.","Owner submits POST /api/shop/price-update with X-Shop-Token.","Owner must confirm the submitted price matches the current in-store price.","Confirmed prices are labelled Verified for 24 hours, then Expired."],"never_publish_fake_prices":True}
