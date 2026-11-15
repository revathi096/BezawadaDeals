import os
import json
import traceback
from pathlib import Path
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Request, Header, HTTPException, UploadFile, File, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

APP_VERSION = "5.0.0"
TOTAL_WARDS = 86

# Gazette boundary landmarks used as short display labels.
# These are display labels, not official ward names.
WARD_AREA_NAMES = {
    1: "Ramavarappadu / Inner Ring Road",
    2: "ESI Hospital / Loyola College",
    3: "Polytechnic Signal / Chandramoulipuram",
    4: "Mythri Nagar / Gunadala Hill",
    5: "Gunadala Centre / Bethlehem Nagar",
    6: "Padavalarevu / Machavaram Hill",
    7: "Karl Marx Road / Moghalrajpuram",
    8: "Moghalrajpuram / Water Tank Road",
    9: "Ashok Nagar / Sikhamani Centre",
    10: "Sikhamani Centre / Krishnalanka",
    11: "Mother Teresa Junction / PVP Road",
    12: "Chandramoulipuram / Bandar Road",
    13: "Benz Circle / Gurunanak Colony",
    14: "Gurunanak Colony / Fun Times Road",
    15: "Auto Nagar / Mahanadu Road",
    16: "Ramavarappadu Junction / Currency Nagar",
    17: "Auto Nagar / Kanuru Donka",
    18: "Chowdary Peta / Yenamalakuduru",
    19: "Chowdary Peta / JD Nagar",
    20: "JD Nagar / NSM School",
    21: "Bandar Road / NSM School",
    22: "Putta Road / Krishna River",
    23: "Geetha Nagar / Krishna River",
    24: "Bhupesh Gupta Nagar / Balaji Nagar",
    25: "Taraka Rama Nagar / Krishna River",
    26: "Taraka Rama Nagar / Kanaka Durga Varadhi",
    27: "Ranadheer Nagar / Dwaraka Nagar",
    30: "Krishna Lanka / Rythu Bazar",
    31: "Bus Stand Road / Indrakeeladri",
    32: "Durga Ghat / RTC Workshop Road",
    33: "Berm Park / Gollapudi",
    34: "Manava Mandir / Swathi Centre",
    37: "Sitara Junction / Church Centre",
    38: "Housing Board Colony / Swathi Centre",
    40: "Joji Nagar / Kabela Road",
    41: "Gollapudi Bypass / JNNURM Apartments",
    42: "Milk Factory / Rajarajeswaripeta",
    43: "Deen Dayal Nagar / Tannerpet",
    45: "KT Road / Indrakeeladri",
    46: "Nehru Bomma Centre / Kanaka Durga Temple",
    47: "Hindu High School / KT Road",
    48: "Lenin Bhavan / Park Road",
    49: "Main Bazaar / KT Road",
    50: "RR Apparao Street / Railway Track",
    52: "Wynchipeta / KT Road",
    53: "Yerrakatta Road / Nanne Saheb Street",
    54: "KL Rao Nagar / Wynchipeta",
    55: "New Rajarajeswari Peta / Lotus Landmark",
    58: "Railway Station Road / RCM School",
    59: "Bavajipeta / Tummalapalli Kalakshetram",
    60: "Besant Road / MG Road",
    61: "Eat Street / Kaleswara Rao Road",
    63: "Eluru Road / Besant Road",
    64: "Gulabi Thota / Bavajipeta",
    65: "Gunadala / Eluru Road",
    66: "Gunadala Railway Gate / Devi Nagar",
    69: "Teachers Colony / Food Junction",
    70: "Teachers Colony / GS Raju Road",
    71: "Challapalli Vari Street / GS Raju Road",
    76: "Ayodhya Nagar / Anna Canteen",
    77: "Ajith Singh Nagar / Singh Nagar Flyover",
    78: "Daba Kotla Road / Nunna Road",
    83: "Rajiv Nagar / Patel Nagar",
    84: "Kandrika / UDA Colony",
    85: "Meenakshi Nagar / Kandrika",
    86: "Kandrika Junction / Pathapadu"
}

app = FastAPI(
    title="BezawadaDeals",
    description="Vijayawada Local Shopping Marketplace",
    version=APP_VERSION
)

BASE_DIR = Path(__file__).resolve().parent

if (BASE_DIR / "Templates").is_dir():
    TEMPLATE_DIR = BASE_DIR / "Templates"
elif (BASE_DIR / "templates").is_dir():
    TEMPLATE_DIR = BASE_DIR / "templates"
else:
    TEMPLATE_DIR = None

templates = Jinja2Templates(directory=str(TEMPLATE_DIR)) if TEMPLATE_DIR else None
PUBLIC_DIR = BASE_DIR / "public"

if PUBLIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(PUBLIC_DIR)), name="static")

CATEGORIES = [
    "Vegetables", "Fruits", "Groceries", "Rice & Pulses",
    "Dairy & Bakery", "Meat & Seafood", "Supermarkets",
    "Wholesale Stores", "Shopping Malls", "Department Stores",
    "Restaurants", "Fast Food", "Cafes & Tea",
    "Juice & Ice Cream", "Sweets & Bakeries", "Electronics",
    "Mobile Stores", "Computers", "Home Appliances",
    "Fashion & Clothing", "Footwear", "Bags", "Jewellery",
    "Accessories", "Cosmetics", "Medical Stores",
    "Hospitals & Clinics", "Optical Stores", "Petrol Bunks",
    "Auto Sales & Repair", "Hardware", "Electrical Stores",
    "Furniture", "Home & Kitchen", "Books & Stationery",
    "Toys & Baby Products", "Sports & Fitness", "Flowers",
    "Gifts", "Salons & Beauty", "Laundry", "Photo & Printing",
    "Banks & ATMs", "Internet & Mobile Services",
    "Repairs & Services", "Education & Coaching",
    "Travel & Transport", "Local Services", "Pet Shops",
    "Other Shops"
]

CATEGORY_LOOKUP = {c.casefold(): c for c in CATEGORIES}


def canonical_category(value: str) -> str:
    category = CATEGORY_LOOKUP.get((value or "").strip().casefold())
    if not category:
        raise HTTPException(status_code=400, detail="Invalid category")
    return category


def get_database_url():
    url = os.getenv("DATABASE_URL")
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def get_connection():
    database_url = get_database_url()
    if not database_url:
        raise HTTPException(status_code=503, detail="Database is not configured")
    return psycopg.connect(database_url, connect_timeout=10, row_factory=dict_row)


def create_database_tables():
    sql_commands = [
        """CREATE TABLE IF NOT EXISTS wards (
            id BIGSERIAL PRIMARY KEY,
            ward_number INTEGER UNIQUE NOT NULL,
            area_name TEXT NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS localities (
            id BIGSERIAL PRIMARY KEY,
            ward_id BIGINT NOT NULL REFERENCES wards(id),
            locality_name TEXT NOT NULL,
            locality_name_te TEXT,
            is_verified BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(ward_id, locality_name)
        )""",
        """CREATE TABLE IF NOT EXISTS shops (
            id BIGSERIAL PRIMARY KEY,
            shop_name TEXT NOT NULL,
            category TEXT NOT NULL,
            ward_id BIGINT REFERENCES wards(id),
            address TEXT,
            phone TEXT,
            owner_name TEXT,
            locality_id BIGINT REFERENCES localities(id),
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            image_url TEXT,
            is_verified BOOLEAN DEFAULT FALSE,
            data_connection_status TEXT DEFAULT 'not_connected',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS products (
            id BIGSERIAL PRIMARY KEY,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS prices (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT REFERENCES shops(id),
            product_id BIGINT NOT NULL REFERENCES products(id),
            price NUMERIC(12,2) NOT NULL CHECK (price >= 0),
            price_type TEXT DEFAULT 'shop_retail',
            source_name TEXT,
            source_type TEXT DEFAULT 'manual',
            is_live BOOLEAN DEFAULT FALSE,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "ALTER TABLE wards ADD COLUMN IF NOT EXISTS area_name TEXT",
        "ALTER TABLE shops ADD COLUMN IF NOT EXISTS locality_id BIGINT REFERENCES localities(id)",
        "ALTER TABLE shops ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION",
        "ALTER TABLE shops ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION",
        "ALTER TABLE shops ADD COLUMN IF NOT EXISTS image_url TEXT",
        "ALTER TABLE shops ADD COLUMN IF NOT EXISTS data_connection_status TEXT DEFAULT 'not_connected'",
        "ALTER TABLE prices ADD COLUMN IF NOT EXISTS source_type TEXT DEFAULT 'manual'",
        "ALTER TABLE prices ADD COLUMN IF NOT EXISTS is_live BOOLEAN DEFAULT FALSE",
        "CREATE INDEX IF NOT EXISTS idx_shops_ward ON shops(ward_id)",
        "CREATE INDEX IF NOT EXISTS idx_shops_category ON shops(category)",
        "CREATE INDEX IF NOT EXISTS idx_localities_ward ON localities(ward_id)",
        "CREATE INDEX IF NOT EXISTS idx_prices_product ON prices(product_id)",
        "CREATE INDEX IF NOT EXISTS idx_prices_shop ON prices(shop_id)",
        "CREATE INDEX IF NOT EXISTS idx_prices_updated ON prices(updated_at DESC)"
    ]

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                for command in sql_commands:
                    cursor.execute(command)

                for ward_no in range(1, TOTAL_WARDS + 1):
                    area = WARD_AREA_NAMES.get(ward_no, f"Ward {ward_no}")
                    cursor.execute(
                        """
                        INSERT INTO wards (ward_number, area_name, is_active)
                        VALUES (%s, %s, TRUE)
                        ON CONFLICT (ward_number) DO UPDATE SET
                            area_name = CASE
                                WHEN wards.area_name IS NULL
                                  OR TRIM(wards.area_name) = ''
                                  OR LOWER(TRIM(wards.area_name)) = 'area details pending'
                                  OR wards.area_name = 'Ward ' || wards.ward_number::TEXT
                                THEN EXCLUDED.area_name
                                ELSE wards.area_name
                            END,
                            is_active = TRUE
                        """,
                        (ward_no, area)
                    )
            connection.commit()
        print("BezawadaDeals database setup completed.")
        return True
    except Exception as error:
        print("Database setup error:", error)
        traceback.print_exc()
        return False


@app.on_event("startup")
async def startup_event():
    create_database_tables()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not TEMPLATE_DIR:
        return HTMLResponse("<h1>Templates folder not found</h1>", status_code=500)
    if not (TEMPLATE_DIR / "index.html").is_file():
        return HTMLResponse("<h1>index.html not found</h1>", status_code=500)
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.get("/health")
async def health():
    return {"status": "online", "website": "BezawadaDeals", "version": APP_VERSION}


@app.get("/api/status")
async def api_status():
    return {"success": True, "message": "BezawadaDeals backend is running",
            "version": APP_VERSION, "categories": CATEGORIES}


@app.get("/api/db-status")
async def database_status():
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""SELECT table_name FROM information_schema.tables
                    WHERE table_schema='public' ORDER BY table_name""")
                tables = [row["table_name"] for row in cursor.fetchall()]
        return {"success": True, "database": "connected", "tables": tables}
    except Exception:
        traceback.print_exc()
        return {"success": False, "database": "connection failed"}


@app.get("/api/categories")
async def get_categories():
    return {"success": True, "total_categories": len(CATEGORIES), "categories": CATEGORIES}


@app.get("/api/wards")
async def get_wards():
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT id, ward_number, area_name, is_active
                    FROM wards
                    WHERE is_active=TRUE
                    ORDER BY ward_number
                """)
                rows = cursor.fetchall()
        return {"success": True, "total_wards": len(rows), "wards": rows}
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Unable to load wards")


def check_admin_key(api_key):
    expected = os.getenv("PRICE_FEED_API_KEY")
    if not expected or not api_key or api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


class LocalityInput(BaseModel):
    ward_number: int = Field(ge=1, le=86)
    locality_name: str = Field(min_length=2, max_length=150)
    locality_name_te: Optional[str] = None
    is_verified: bool = False


@app.post("/api/admin/localities")
async def add_locality(data: LocalityInput, x_api_key: Optional[str] = Header(default=None)):
    check_admin_key(x_api_key)
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM wards WHERE ward_number=%s", (data.ward_number,))
            ward = cursor.fetchone()
            if not ward:
                raise HTTPException(status_code=404, detail="Ward not found")
            cursor.execute("""
                INSERT INTO localities (ward_id, locality_name, locality_name_te, is_verified)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (ward_id, locality_name) DO UPDATE SET
                    locality_name_te=EXCLUDED.locality_name_te,
                    is_verified=EXCLUDED.is_verified
                RETURNING id, locality_name
            """, (ward["id"], data.locality_name.strip(), data.locality_name_te, data.is_verified))
            result = cursor.fetchone()
        connection.commit()
    return {"success": True, "locality": result}


class ShopInput(BaseModel):
    shop_name: str = Field(min_length=2, max_length=180)
    category: str
    ward_number: int = Field(ge=1, le=86)
    address: Optional[str] = None
    phone: Optional[str] = None
    owner_name: Optional[str] = None
    locality_id: Optional[int] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    image_url: Optional[str] = None
    is_verified: bool = False


@app.post("/api/admin/shops")
async def add_shop(data: ShopInput, x_api_key: Optional[str] = Header(default=None)):
    check_admin_key(x_api_key)
    category = canonical_category(data.category)
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM wards WHERE ward_number=%s AND is_active=TRUE",
                           (data.ward_number,))
            ward = cursor.fetchone()
            if not ward:
                raise HTTPException(status_code=404, detail="Ward not found")
            if data.locality_id is not None:
                cursor.execute("SELECT id FROM localities WHERE id=%s AND ward_id=%s",
                               (data.locality_id, ward["id"]))
                if not cursor.fetchone():
                    raise HTTPException(status_code=400, detail="Locality does not belong to this ward")
            cursor.execute("""
                INSERT INTO shops (
                    shop_name, category, ward_id, address, phone, owner_name,
                    locality_id, latitude, longitude, image_url, is_verified
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id, shop_name, category
            """, (data.shop_name.strip(), category, ward["id"], data.address, data.phone,
                  data.owner_name, data.locality_id, data.latitude, data.longitude,
                  data.image_url, data.is_verified))
            shop = cursor.fetchone()
        connection.commit()
    return {"success": True, "shop": shop}


@app.get("/api/shops")
async def get_shops(
    ward_number: int = Query(..., ge=1, le=86),
    category: str = Query(...)
):
    category_input = (category or "").strip()
    category = "All" if category_input.casefold() == "all" else canonical_category(category_input)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT s.id, s.shop_name, s.shop_name AS name, s.category,
                    s.address, s.phone, s.is_verified, s.data_connection_status,
                    s.latitude, s.longitude, s.image_url, s.image_url AS photo_url,
                    w.ward_number, w.area_name, l.locality_name,
                    latest.product_name, latest.unit, latest.price,
                    latest.source_name, latest.source_type, latest.updated_at,
                    COALESCE(latest.live_now, FALSE) AS is_live,
                    COALESCE(latest.live_now, FALSE) AS price_verified
                FROM shops s
                JOIN wards w ON w.id=s.ward_id
                LEFT JOIN localities l ON l.id=s.locality_id
                LEFT JOIN LATERAL (
                    SELECT p.product_name, p.unit, pr.price, pr.source_name,
                        pr.source_type, pr.updated_at,
                        (pr.source_type='connected_feed' AND pr.is_live=TRUE
                         AND pr.updated_at >= NOW()-INTERVAL '30 minutes') AS live_now
                    FROM prices pr
                    JOIN products p ON p.id=pr.product_id
                    WHERE pr.shop_id=s.id
                    ORDER BY pr.updated_at DESC
                    LIMIT 1
                ) latest ON TRUE
                WHERE w.ward_number=%s
                  AND (%s::TEXT='All' OR LOWER(s.category)=LOWER(%s::TEXT))
                ORDER BY s.shop_name
            """, (ward_number, category, category))
            rows = cursor.fetchall()

    return {"success": True, "ward_number": ward_number, "category": category,
            "total_shops": len(rows), "shops": rows}


@app.get("/api/shops/{shop_id}/products")
async def get_shop_products(shop_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT p.id AS product_id, p.product_name, p.category, p.unit,
                    pr.price, pr.source_name, pr.source_type, pr.updated_at, pr.is_live,
                    (pr.source_type='connected_feed' AND pr.is_live=TRUE
                     AND pr.updated_at >= NOW()-INTERVAL '30 minutes') AS live_now
                FROM products p
                JOIN LATERAL (
                    SELECT * FROM prices WHERE shop_id=%s AND product_id=p.id
                    ORDER BY updated_at DESC LIMIT 1
                ) pr ON TRUE
                ORDER BY p.category, p.product_name
            """, (shop_id,))
            rows = cursor.fetchall()
    return {"success": True, "shop_id": shop_id, "total_products": len(rows), "products": rows}


@app.get("/api/price-tracker")
async def price_tracker(
    ward_number: int = Query(..., ge=1, le=86),
    category: str = Query(...),
    product_name: Optional[str] = None
):
    category = canonical_category(category)
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT ON (s.id, p.id) s.id AS shop_id, s.shop_name,
                    s.address, s.latitude, s.longitude, w.ward_number,
                    l.locality_name, p.product_name, p.unit, pr.price,
                    pr.source_name, pr.source_type, pr.updated_at,
                    (pr.source_type='connected_feed' AND pr.is_live=TRUE
                     AND pr.updated_at >= NOW()-INTERVAL '30 minutes') AS live_now
                FROM prices pr
                JOIN shops s ON s.id=pr.shop_id
                JOIN wards w ON w.id=s.ward_id
                LEFT JOIN localities l ON l.id=s.locality_id
                JOIN products p ON p.id=pr.product_id
                WHERE w.ward_number=%s AND LOWER(s.category)=LOWER(%s)
                  AND (%s::TEXT IS NULL OR LOWER(p.product_name)=LOWER(%s::TEXT))
                ORDER BY s.id, p.id, pr.updated_at DESC
            """, (ward_number, category, product_name, product_name))
            rows = cursor.fetchall()
    return {"success": True, "ward_number": ward_number, "category": category,
            "total_price_records": len(rows), "prices": rows}


class PriceUpdate(BaseModel):
    shop_id: int
    product_name: str = Field(min_length=1, max_length=150)
    category: str
    unit: str = Field(min_length=1, max_length=30)
    price: float = Field(ge=0)
    source_name: str = Field(min_length=1, max_length=150)


@app.post("/api/prices/update")
async def update_shop_price(data: PriceUpdate, x_api_key: Optional[str] = Header(default=None)):
    check_admin_key(x_api_key)
    category = canonical_category(data.category)
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT id FROM shops WHERE id=%s", (data.shop_id,))
            if not cursor.fetchone():
                raise HTTPException(status_code=404, detail="Shop not found")
            cursor.execute("""
                SELECT id FROM products
                WHERE LOWER(product_name)=LOWER(%s)
                  AND LOWER(category)=LOWER(%s)
                  AND LOWER(unit)=LOWER(%s) LIMIT 1
            """, (data.product_name, category, data.unit))
            product = cursor.fetchone()
            if product:
                product_id = product["id"]
            else:
                cursor.execute("""
                    INSERT INTO products (product_name, category, unit)
                    VALUES (%s,%s,%s) RETURNING id
                """, (data.product_name, category, data.unit))
                product_id = cursor.fetchone()["id"]
            cursor.execute("""
                INSERT INTO prices (
                    shop_id, product_id, price, price_type, source_name,
                    source_type, is_live, updated_at
                ) VALUES (%s,%s,%s,'shop_retail',%s,'connected_feed',TRUE,NOW())
                RETURNING id, updated_at
            """, (data.shop_id, product_id, data.price, data.source_name))
            result = cursor.fetchone()
            cursor.execute("UPDATE shops SET data_connection_status='connected' WHERE id=%s",
                           (data.shop_id,))
        connection.commit()
    return {"success": True, "message": "Price update received",
            "price_record_id": result["id"], "updated_at": result["updated_at"]}


@app.get("/api/check")
async def check_files():
    return {
        "templates_folder": str(TEMPLATE_DIR) if TEMPLATE_DIR else None,
        "index_html_exists": (TEMPLATE_DIR / "index.html").is_file() if TEMPLATE_DIR else False,
        "public_folder_exists": PUBLIC_DIR.is_dir()
    }


class WardImportItem(BaseModel):
    ward_number: int = Field(ge=1, le=86)
    area_name: str = Field(min_length=1, max_length=200)
    localities: list[str] = Field(default_factory=list)


class WardImportRequest(BaseModel):
    wards: list[WardImportItem]


def save_wards_data(data: WardImportRequest):
    wards = data.wards
    if len(wards) != 86:
        raise HTTPException(status_code=400, detail="Ward data must contain exactly 86 wards")
    ward_numbers = [item.ward_number for item in wards]
    if sorted(ward_numbers) != list(range(1, 87)):
        raise HTTPException(status_code=400, detail="Ward numbers must be 1 to 86 exactly once")

    imported_localities = 0
    with get_connection() as connection:
        with connection.cursor() as cursor:
            for item in wards:
                area_name = item.area_name.strip()
                if not area_name or area_name.casefold() == "area details pending":
                    raise HTTPException(status_code=400,
                                        detail=f"Invalid area name for ward {item.ward_number}")
                cursor.execute("""
                    INSERT INTO wards (ward_number, area_name, is_active)
                    VALUES (%s,%s,TRUE)
                    ON CONFLICT (ward_number) DO UPDATE SET
                        area_name=EXCLUDED.area_name, is_active=TRUE
                    RETURNING id
                """, (item.ward_number, area_name))
                ward_id = cursor.fetchone()["id"]
                for locality in item.localities:
                    locality_name = locality.strip()
                    if len(locality_name) < 2:
                        continue
                    cursor.execute("""
                        INSERT INTO localities (ward_id, locality_name, is_verified)
                        VALUES (%s,%s,FALSE)
                        ON CONFLICT (ward_id, locality_name) DO NOTHING
                    """, (ward_id, locality_name))
                    if cursor.rowcount > 0:
                        imported_localities += 1
        connection.commit()
    return {"success": True, "message": "Ward data imported successfully",
            "wards_processed": 86, "new_localities_added": imported_localities,
            "verified": False}


@app.post("/api/admin/import-wards")
async def import_wards(file: UploadFile = File(...),
                       x_api_key: Optional[str] = Header(default=None)):
    check_admin_key(x_api_key)
    if not file.filename or not file.filename.lower().endswith(".json"):
        raise HTTPException(status_code=400, detail="Please upload a JSON file")
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        try:
            raw_data = json.loads(content.decode("utf-8-sig"))
            validated_data = WardImportRequest(**raw_data)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            raise HTTPException(status_code=400, detail="Invalid ward data") from error
        return save_wards_data(validated_data)
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Ward import failed")
    finally:
        await file.close()


@app.post("/api/admin/import-wards-json")
async def import_wards_json(data: WardImportRequest,
                            x_api_key: Optional[str] = Header(default=None)):
    check_admin_key(x_api_key)
    try:
        return save_wards_data(data)
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Ward import failed")
