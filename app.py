import os, asyncio, json, time
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi import Request as FastAPIRequest

APP_VERSION = "12.0.0"
DATABASE_URL = os.getenv("DATABASE_URL", "")
DATA_GOV_IN_API_KEY = os.getenv("DATA_GOV_IN_API_KEY", "")
SHOP_OWNER_TOKEN = os.getenv("SHOP_OWNER_TOKEN", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
AGMARKNET_RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"
AGMARKNET_URL = f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE}"

CATEGORIES = [
"Vegetables", "Fruits", "Rice & Rice Products", "Wheat & Atta", "Dals & Pulses",
"Edible Oils", "Spices & Masalas", "Sugar & Jaggery", "Salt", "Dairy & Milk",
"Eggs", "Chicken & Meat", "Fish & Seafood", "Bakery", "Packaged Foods",
"Snacks", "Biscuits", "Beverages", "Tea & Coffee", "Dry Fruits & Nuts",
"Baby Food", "Baby Care", "Personal Care", "Skin Care", "Hair Care",
"Household Cleaning", "Laundry", "Home & Kitchen", "Pooja Items", "Stationery",
"School Supplies", "Electronics", "Mobile Accessories", "Clothing", "Footwear",
"Medicines (OTC)", "Pet Supplies", "Kitchen Essentials", "Water & Disposables",
"Frozen Foods", "Ready to Cook", "Organic Products", "Millets", "Cereals",
"Poultry Products", "Bakery Ingredients", "Garden Supplies", "Hardware", "Other"
]

# Ward numbers are seeded without invented locality names. Gazette boundary descriptions
# can be imported by admin using /api/admin/wards/import.


def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is missing")
    url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    return psycopg.connect(url, row_factory=dict_row, connect_timeout=15)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db():
    with db() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS wards (
            ward_number INTEGER PRIMARY KEY,
            ward_name TEXT,
            boundary_description TEXT DEFAULT '',
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """)
        # Migrate old installations safely.
        con.execute("ALTER TABLE wards ADD COLUMN IF NOT EXISTS ward_name TEXT")
        con.execute("ALTER TABLE wards ADD COLUMN IF NOT EXISTS boundary_description TEXT DEFAULT ''")
        con.execute("ALTER TABLE wards ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT NOW()")
        con.execute("""
        CREATE TABLE IF NOT EXISTS localities (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            ward_number INTEGER REFERENCES wards(ward_number),
            verified BOOLEAN NOT NULL DEFAULT FALSE,
            source TEXT DEFAULT 'shop-owner submitted',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS shops (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            shop_type TEXT NOT NULL DEFAULT 'Supermarket',
            locality_id BIGINT REFERENCES localities(id),
            ward_number INTEGER REFERENCES wards(ward_number),
            address TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """)
        con.execute("ALTER TABLE shops ADD COLUMN IF NOT EXISTS shop_type TEXT NOT NULL DEFAULT 'Supermarket'")
        con.execute("ALTER TABLE shops ADD COLUMN IF NOT EXISTS locality_id BIGINT REFERENCES localities(id)")
        con.execute("ALTER TABLE shops ADD COLUMN IF NOT EXISTS ward_number INTEGER REFERENCES wards(ward_number)")
        con.execute("ALTER TABLE shops ADD COLUMN IF NOT EXISTS address TEXT DEFAULT ''")
        con.execute("ALTER TABLE shops ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT ''")
        con.execute("ALTER TABLE shops ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE")
        con.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT '1 kg',
            UNIQUE(name, category, unit)
        )
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            price NUMERIC(12,2) NOT NULL CHECK(price > 0),
            source TEXT NOT NULL DEFAULT 'shop-owner confirmed',
            status TEXT NOT NULL DEFAULT 'Verified',
            confirmed BOOLEAN NOT NULL DEFAULT FALSE,
            observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS prices_latest_idx ON prices(product_id, observed_at DESC)")
        con.execute("""
        CREATE TABLE IF NOT EXISTS mandi_prices (
            id BIGSERIAL PRIMARY KEY,
            state TEXT, district TEXT, market TEXT, commodity TEXT, variety TEXT,
            arrival_date TEXT, min_price NUMERIC(12,2), max_price NUMERIC(12,2),
            modal_price NUMERIC(12,2), source TEXT DEFAULT 'AGMARKNET / data.gov.in',
            fetched_at TIMESTAMPTZ DEFAULT NOW(), UNIQUE(district, market, commodity, variety, arrival_date)
        )
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS source_sync (
            source_key TEXT PRIMARY KEY, last_attempt TIMESTAMPTZ, last_success TIMESTAMPTZ,
            status TEXT, message TEXT DEFAULT ''
        )
        """)
        for n in range(1, 87):
            con.execute("INSERT INTO wards(ward_number, ward_name) VALUES (%s,%s) ON CONFLICT(ward_number) DO NOTHING", (n, f"Ward {n:02d}"))
        con.commit()


def fetch_agmarknet():
    if not DATA_GOV_IN_API_KEY:
        raise RuntimeError("DATA_GOV_IN_API_KEY not configured")
    records = []
    # Vijayawada's district naming can appear as Krishna (older records) or NTR.
    for district_name in ("Krishna", "NTR"):
        params = {
            "api-key": DATA_GOV_IN_API_KEY,
            "format": "json",
            "limit": "1000",
            "filters[state]": "Andhra Pradesh",
            "filters[district]": district_name,
        }
        req = Request(AGMARKNET_URL + "?" + urlencode(params), headers={"User-Agent": "BezawadaDeals/12.0"})
        with urlopen(req, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8"))
        records.extend(payload.get("records", []))
    saved = 0
    with db() as con:
        for r in records:
            district = str(r.get("district", ""))
            if district and district.lower() not in ("krishna", "ntr"):
                continue
            con.execute("""
            INSERT INTO mandi_prices(state,district,market,commodity,variety,arrival_date,min_price,max_price,modal_price,fetched_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT(district,market,commodity,variety,arrival_date) DO UPDATE SET
              min_price=EXCLUDED.min_price,max_price=EXCLUDED.max_price,modal_price=EXCLUDED.modal_price,fetched_at=NOW()
            """, (r.get("state"), district, r.get("market"), r.get("commodity"), r.get("variety"),
                  r.get("arrival_date"), r.get("min_price") or None, r.get("max_price") or None,
                  r.get("modal_price") or None))
            saved += 1
        con.execute("""INSERT INTO source_sync(source_key,last_attempt,last_success,status,message)
          VALUES('agmarknet',NOW(),NOW(),'OK',%s) ON CONFLICT(source_key) DO UPDATE SET
          last_attempt=NOW(),last_success=NOW(),status='OK',message=EXCLUDED.message""", (f"Saved {saved} mandi rows",))
        con.commit()
    return saved


async def background_sync():
    while True:
        try:
            await asyncio.to_thread(fetch_agmarknet)
        except Exception as e:
            try:
                with db() as con:
                    con.execute("""INSERT INTO source_sync(source_key,last_attempt,status,message)
                    VALUES('agmarknet',NOW(),'ERROR',%s) ON CONFLICT(source_key) DO UPDATE SET
                    last_attempt=NOW(),status='ERROR',message=EXCLUDED.message""", (str(e)[:500],))
                    con.commit()
            except Exception:
                pass
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await asyncio.to_thread(init_db)
    except Exception as e:
        print(f"DATABASE_INIT_ERROR: {e}")
        raise
    task = asyncio.create_task(background_sync())
    yield
    task.cancel()

app = FastAPI(title="BezawadaDeals Price Tracker", version=APP_VERSION, lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
def home(request: FastAPIRequest):
    return templates.TemplateResponse(request=request, name="index.html", context={"version": APP_VERSION})

@app.get("/health")
def health():
    try:
        with db() as con:
            con.execute("SELECT 1")
        return {"ok": True, "version": APP_VERSION, "database": "connected"}
    except Exception as e:
        return {"ok": False, "version": APP_VERSION, "database": str(e)}

@app.get("/api/categories")
def categories():
    return {"count": len(CATEGORIES), "categories": CATEGORIES}

@app.get("/api/wards")
def wards():
    with db() as con:
        rows = con.execute("SELECT ward_number, ward_name, boundary_description FROM wards ORDER BY ward_number").fetchall()
    return {"count": len(rows), "wards": rows}

@app.get("/api/localities")
def localities(ward_number: Optional[int] = None):
    with db() as con:
        if ward_number:
            rows = con.execute("SELECT id,name,ward_number,verified,source FROM localities WHERE ward_number=%s ORDER BY name", (ward_number,)).fetchall()
        else:
            rows = con.execute("SELECT id,name,ward_number,verified,source FROM localities ORDER BY name").fetchall()
    return {"count": len(rows), "localities": rows}

@app.get("/api/shops")
def shops(area: Optional[str] = None, ward_number: Optional[int] = None):
    sql = """SELECT s.id,s.name,s.shop_type,s.address,s.phone,s.ward_number,l.name AS area
             FROM shops s LEFT JOIN localities l ON l.id=s.locality_id WHERE s.active=TRUE"""
    args = []
    if area:
        sql += " AND LOWER(l.name)=LOWER(%s)"; args.append(area)
    if ward_number:
        sql += " AND s.ward_number=%s"; args.append(ward_number)
    sql += " ORDER BY l.name,s.name"
    with db() as con: rows = con.execute(sql, args).fetchall()
    return {"count": len(rows), "shops": rows}

@app.get("/api/unified-price-tracker")
@app.get("/api/price-tracker")
def unified_price_tracker(category: Optional[str] = None, area: Optional[str] = None,
                          ward_number: Optional[int] = None, product: Optional[str] = None,
                          source_type: Optional[str] = None, sort: str = "low"):
    sql = """SELECT p.id AS price_id,pr.name AS product,pr.category,pr.unit,p.price,
             s.name AS shop,s.shop_type,s.address,s.ward_number,l.name AS area,
             p.source,p.status,p.observed_at,p.expires_at
             FROM prices p JOIN products pr ON pr.id=p.product_id JOIN shops s ON s.id=p.shop_id
             LEFT JOIN localities l ON l.id=s.locality_id
             WHERE p.expires_at>NOW() AND s.active=TRUE"""
    args=[]
    if category: sql += " AND LOWER(pr.category)=LOWER(%s)"; args.append(category)
    if area: sql += " AND LOWER(l.name)=LOWER(%s)"; args.append(area)
    if ward_number: sql += " AND s.ward_number=%s"; args.append(ward_number)
    if product: sql += " AND pr.name ILIKE %s"; args.append(f"%{product}%")
    if source_type: sql += " AND LOWER(s.shop_type)=LOWER(%s)"; args.append(source_type)
    sql += " ORDER BY p.price ASC" if sort == "low" else " ORDER BY p.price DESC"
    with db() as con: rows = con.execute(sql, args).fetchall()
    for r in rows:
        r["price"] = float(r["price"])
        r["observed_at"] = r["observed_at"].isoformat() if r["observed_at"] else None
        r["expires_at"] = r["expires_at"].isoformat() if r["expires_at"] else None
    return {"count": len(rows), "prices": rows, "categories": CATEGORIES, "updated_at": now_iso()}

@app.get("/api/market-prices")
def market_prices(commodity: Optional[str] = None, limit: int = Query(300, ge=1, le=1000)):
    with db() as con:
        if commodity:
            rows = con.execute("SELECT * FROM mandi_prices WHERE commodity ILIKE %s ORDER BY arrival_date DESC LIMIT %s", (f"%{commodity}%", limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM mandi_prices ORDER BY arrival_date DESC LIMIT %s", (limit,)).fetchall()
    for r in rows:
        for k in ("min_price", "max_price", "modal_price"):
            if r[k] is not None: r[k] = float(r[k])
        if r.get("fetched_at"): r["fetched_at"] = r["fetched_at"].isoformat()
    return {"count": len(rows), "prices": rows, "source": "AGMARKNET / data.gov.in (mandi wholesale; not retail)", "updated_at": now_iso()}

@app.get("/api/status")
def status():
    with db() as con:
        counts = {}
        for table in ("wards", "localities", "shops", "products", "prices", "mandi_prices"):
            counts[table] = con.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        sync = con.execute("SELECT source_key,last_attempt,last_success,status,message FROM source_sync ORDER BY source_key").fetchall()
    for s in sync:
        for k in ("last_attempt", "last_success"):
            if s[k]: s[k] = s[k].isoformat()
    return {"version": APP_VERSION, "counts": counts, "sources": sync, "automatic_refresh_minutes": 60}

@app.post("/api/shop/price-update")
def shop_price_update(payload: dict, x_shop_token: str = Header(default="")):
    if not SHOP_OWNER_TOKEN or x_shop_token != SHOP_OWNER_TOKEN:
        raise HTTPException(401, "Invalid shop owner token")
    required = ["shop_name", "shop_type", "area", "ward_number", "product", "category", "unit", "price", "confirmed"]
    missing = [k for k in required if k not in payload or payload[k] in (None, "")]
    if missing: raise HTTPException(400, f"Missing fields: {', '.join(missing)}")
    if payload["confirmed"] is not True: raise HTTPException(400, "Price must be explicitly confirmed")
    try: price = float(payload["price"])
    except Exception: raise HTTPException(400, "Invalid price")
    if price <= 0: raise HTTPException(400, "Price must be greater than zero")
    shop_type = str(payload["shop_type"]).strip()
    if shop_type not in ("Supermarket", "Rythu Bazaar", "Local Shop"):
        raise HTTPException(400, "shop_type must be Supermarket, Rythu Bazaar, or Local Shop")
    with db() as con:
        con.execute("INSERT INTO localities(name,ward_number,verified) VALUES(%s,%s,FALSE) ON CONFLICT(name) DO NOTHING", (payload["area"], int(payload["ward_number"])))
        loc = con.execute("SELECT id FROM localities WHERE LOWER(name)=LOWER(%s)", (payload["area"],)).fetchone()
        sh = con.execute("SELECT id FROM shops WHERE name=%s AND shop_type=%s AND ward_number=%s", (payload["shop_name"], shop_type, int(payload["ward_number"]))).fetchone()
        if sh:
            shop_id = sh["id"]
            con.execute("UPDATE shops SET locality_id=%s,address=%s,phone=%s,active=TRUE WHERE id=%s", (loc["id"], payload.get("address", ""), payload.get("phone", ""), shop_id))
        else:
            shop_id = con.execute("""INSERT INTO shops(name,shop_type,locality_id,ward_number,address,phone)
                 VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""", (payload["shop_name"],shop_type,loc["id"],int(payload["ward_number"]),payload.get("address", ""),payload.get("phone", ""))).fetchone()["id"]
        con.execute("INSERT INTO products(name,category,unit) VALUES(%s,%s,%s) ON CONFLICT(name,category,unit) DO NOTHING", (payload["product"],payload["category"],payload["unit"]))
        prod = con.execute("SELECT id FROM products WHERE name=%s AND category=%s AND unit=%s", (payload["product"],payload["category"],payload["unit"])).fetchone()
        con.execute("""INSERT INTO prices(shop_id,product_id,price,source,status,confirmed,observed_at,expires_at)
             VALUES(%s,%s,%s,'shop-owner confirmed','Verified',TRUE,NOW(),NOW()+INTERVAL '24 hours')""", (shop_id,prod["id"],price))
        con.commit()
    return {"ok": True, "message": "Price saved; visible for 24 hours", "price": price}

@app.post("/api/admin/wards/import")
def import_wards(payload: dict, x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN: raise HTTPException(401, "Invalid admin token")
    rows = payload.get("wards", [])
    if not isinstance(rows, list): raise HTTPException(400, "wards must be a list")
    saved=0
    with db() as con:
        for row in rows:
            n=int(row["ward_number"])
            if n<1 or n>86: continue
            con.execute("""INSERT INTO wards(ward_number,ward_name,boundary_description,updated_at)
              VALUES(%s,%s,%s,NOW()) ON CONFLICT(ward_number) DO UPDATE SET
              ward_name=EXCLUDED.ward_name,boundary_description=EXCLUDED.boundary_description,updated_at=NOW()""",
              (n,row.get("ward_name",f"Ward {n:02d}"),row.get("boundary_description","")))
            saved+=1
        con.commit()
    return {"ok":True,"saved":saved}

@app.post("/api/admin/refresh-market")
async def refresh_market(x_admin_token: str = Header(default="")):
    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN: raise HTTPException(401, "Invalid admin token")
    try:
        count = await asyncio.to_thread(fetch_agmarknet)
        return {"ok":True,"saved":count}
    except Exception as e:
        raise HTTPException(502, str(e))

@app.get("/api/official-sources")
def official_sources():
    return {"sources":[
      {"name":"AGMARKNET / data.gov.in","type":"Government mandi wholesale prices","automatic":bool(DATA_GOV_IN_API_KEY),"resource_id":AGMARKNET_RESOURCE,"note":"Daily mandi prices; not supermarket or Rythu Bazaar retail rates."},
      {"name":"Supermarket prices","type":"Retail","automatic":False,"note":"Only verified store/owner-submitted prices are displayed unless an official public price feed is available."},
      {"name":"Rythu Bazaar retail rates","type":"Retail","automatic":False,"note":"Enter through verified Rythu Bazaar operator/owner update; do not substitute mandi wholesale prices."}
    ]}
