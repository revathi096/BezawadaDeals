import os
import json
import secrets
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Header, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

APP_VERSION = "11.0.0"
TOTAL_WARDS = 86

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "").strip()
DATA_GOV_IN_API_KEY = os.getenv("DATA_GOV_IN_API_KEY", "").strip()

AGMARKNET_RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"

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

CATEGORY_SET = set(CATEGORIES)

app = FastAPI(
    title="BezawadaDeals",
    version=APP_VERSION
)


def get_db():
    if not DATABASE_URL:
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL is not configured"
        )

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=15
    )


def check_admin(token):
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_TOKEN is not configured in Render"
        )

    if not token or not secrets.compare_digest(
        str(token), ADMIN_TOKEN
    ):
        raise HTTPException(
            status_code=403,
            detail="Invalid admin token"
        )


def check_category(category):
    category = str(category or "").strip()

    if category not in CATEGORY_SET:
        raise HTTPException(
            status_code=400,
            detail="Invalid category"
        )

    return category


def init_db():
    if not DATABASE_URL:
        return

    sql = """
    CREATE TABLE IF NOT EXISTS wards (
        id SERIAL PRIMARY KEY,
        ward_number INTEGER UNIQUE NOT NULL,
        ward_name TEXT,
        area_name TEXT,
        boundary_description TEXT,
        is_active BOOLEAN DEFAULT TRUE
    );

    ALTER TABLE wards
        ADD COLUMN IF NOT EXISTS ward_name TEXT;

    ALTER TABLE wards
        ADD COLUMN IF NOT EXISTS area_name TEXT;

    ALTER TABLE wards
        ADD COLUMN IF NOT EXISTS boundary_description TEXT;

    ALTER TABLE wards
        ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;

    CREATE TABLE IF NOT EXISTS shops (
        id BIGSERIAL PRIMARY KEY,
        shop_name TEXT NOT NULL,
        category TEXT NOT NULL,
        ward_id INTEGER REFERENCES wards(id),
        address TEXT,
        phone TEXT,
        owner_name TEXT,
        locality TEXT,
        image_url TEXT,
        is_verified BOOLEAN DEFAULT FALSE,
        shop_update_token TEXT UNIQUE,
        created_at TIMESTAMPTZ DEFAULT NOW()
    );

    ALTER TABLE shops ADD COLUMN IF NOT EXISTS locality TEXT;
    ALTER TABLE shops ADD COLUMN IF NOT EXISTS shop_update_token TEXT;
    ALTER TABLE shops ADD COLUMN IF NOT EXISTS owner_name TEXT;
    ALTER TABLE shops ADD COLUMN IF NOT EXISTS image_url TEXT;
    ALTER TABLE shops ADD COLUMN IF NOT EXISTS is_verified BOOLEAN DEFAULT FALSE;

    CREATE TABLE IF NOT EXISTS products (
        id BIGSERIAL PRIMARY KEY,
        product_name TEXT NOT NULL,
        category TEXT NOT NULL,
        unit TEXT NOT NULL DEFAULT 'kg',
        description TEXT
    );

    CREATE TABLE IF NOT EXISTS prices (
        id BIGSERIAL PRIMARY KEY,
        shop_id BIGINT REFERENCES shops(id) ON DELETE CASCADE,
        product_id BIGINT REFERENCES products(id),
        price NUMERIC(12,2) NOT NULL CHECK(price >= 0),
        price_type TEXT DEFAULT 'retail',
        source_name TEXT,
        source_type TEXT NOT NULL,
        is_live BOOLEAN DEFAULT TRUE,
        in_store_confirmed BOOLEAN DEFAULT FALSE,
        valid_until TIMESTAMPTZ,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS prices_product_updated_idx
        ON prices(product_id, updated_at DESC);

    CREATE INDEX IF NOT EXISTS prices_shop_updated_idx
        ON prices(shop_id, updated_at DESC);

    CREATE TABLE IF NOT EXISTS offers (
        id BIGSERIAL PRIMARY KEY,
        shop_id BIGINT REFERENCES shops(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        description TEXT,
        discount_text TEXT,
        valid_from TIMESTAMPTZ DEFAULT NOW(),
        valid_until TIMESTAMPTZ,
        is_active BOOLEAN DEFAULT TRUE,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS localities (
        id BIGSERIAL PRIMARY KEY,
        ward_id INTEGER REFERENCES wards(id) ON DELETE CASCADE,
        locality_name TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        UNIQUE(ward_id, locality_name)
    );
    """

    with get_db() as conn:
        conn.execute(sql)

        for number in range(1, TOTAL_WARDS + 1):
            conn.execute(
                """
                INSERT INTO wards(ward_number, ward_name)
                VALUES(%s, %s)
                ON CONFLICT(ward_number) DO NOTHING
                """,
                (number, f"Ward {number}")
            )

        seed_products = {
            "Vegetables": [
                ("Tomato", "kg"),
                ("Onion", "kg"),
                ("Potato", "kg"),
                ("Brinjal", "kg"),
                ("Carrot", "kg"),
                ("Cabbage", "kg"),
                ("Green Chilli", "kg"),
                ("Drumstick", "kg"),
                ("Ladies Finger", "kg")
            ],
            "Fruits": [
                ("Banana", "dozen"),
                ("Apple", "kg"),
                ("Mango", "kg"),
                ("Orange", "kg"),
                ("Papaya", "kg"),
                ("Grapes", "kg")
            ],
            "Rice & Pulses": [
                ("Rice", "kg"),
                ("Toor Dal", "kg"),
                ("Moong Dal", "kg"),
                ("Urad Dal", "kg"),
                ("Chana Dal", "kg")
            ],
            "Groceries": [
                ("Sugar", "kg"),
                ("Wheat Flour", "kg"),
                ("Cooking Oil", "litre"),
                ("Salt", "kg"),
                ("Turmeric", "kg")
            ],
            "Dairy & Bakery": [
                ("Milk", "litre"),
                ("Curd", "kg"),
                ("Bread", "piece"),
                ("Eggs", "dozen")
            ]
        }

        for category, products in seed_products.items():
            for name, unit in products:
                conn.execute(
                    """
                    INSERT INTO products(product_name, category, unit)
                    SELECT %s, %s, %s
                    WHERE NOT EXISTS (
                        SELECT 1 FROM products
                        WHERE LOWER(product_name)=LOWER(%s)
                        AND category=%s
                    )
                    """,
                    (name, category, unit, name, category)
                )


@app.on_event("startup")
def startup():
    init_db()


def get_price_status(row):
    valid_until = row.get("valid_until")

    if valid_until:
        if valid_until < datetime.now(timezone.utc):
            return "Expired"

    if (
        row.get("source_type") == "shop_owner_confirmed"
        and row.get("in_store_confirmed")
    ):
        return "Verified"

    return "Estimated"


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"categories": CATEGORIES}
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "version": APP_VERSION
    }


@app.get("/api/status")
def api_status():
    connected = False
    error = None

    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
            connected = True
    except Exception as exc:
        error = type(exc).__name__

    return {
        "ok": True,
        "version": APP_VERSION,
        "database_connected": connected,
        "database_error": error,
        "wards": TOTAL_WARDS,
        "categories": len(CATEGORIES)
    }


@app.get("/api/categories")
def get_categories():
    return {"categories": CATEGORIES}


@app.get("/api/wards")
def get_wards():
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                w.ward_number,
                w.ward_name,
                w.area_name,
                w.boundary_description,
                COUNT(DISTINCT s.id) AS shop_count,
                COUNT(DISTINCT l.id) AS locality_count
            FROM wards w
            LEFT JOIN shops s ON s.ward_id=w.id
            LEFT JOIN localities l ON l.ward_id=w.id
            WHERE w.is_active=TRUE
            GROUP BY w.id
            ORDER BY w.ward_number
            """
        ).fetchall()

    return {"wards": rows}


@app.get("/api/localities")
def get_localities(
    ward_number: Optional[int] = Query(None, ge=1, le=86)
):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT
                l.locality_name,
                w.ward_number
            FROM localities l
            JOIN wards w ON w.id=l.ward_id
            WHERE (%s::integer IS NULL OR w.ward_number=%s)
            ORDER BY l.locality_name
            """,
            (ward_number, ward_number)
        ).fetchall()

    return {"localities": rows}


@app.get("/api/shops")
def get_shops(
    ward_number: Optional[int] = Query(None, ge=1, le=86),
    category: str = "All",
    locality: Optional[str] = None
):
    if category != "All":
        check_category(category)

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                s.id,
                s.shop_name,
                s.category,
                s.address,
                s.phone,
                s.owner_name,
                s.locality,
                s.image_url,
                s.is_verified,
                w.ward_number,
                w.ward_name,
                (
                    SELECT COUNT(*)
                    FROM prices p
                    WHERE p.shop_id=s.id
                    AND p.is_live=TRUE
                    AND (p.valid_until IS NULL OR p.valid_until>=NOW())
                ) AS price_count
            FROM shops s
            JOIN wards w ON w.id=s.ward_id
            WHERE (%s::integer IS NULL OR w.ward_number=%s)
              AND (%s::text='All' OR s.category=%s)
              AND (%s::text IS NULL OR LOWER(s.locality)=LOWER(%s))
            ORDER BY s.shop_name
            LIMIT 1000
            """,
            (
                ward_number, ward_number,
                category, category,
                locality, locality
            )
        ).fetchall()

    return {"shops": rows}


@app.post("/api/admin/shops")
def admin_create_shop(
    payload: dict,
    x_admin_token: Optional[str] = Header(None)
):
    check_admin(x_admin_token)

    name = str(payload.get("shop_name", "")).strip()
    category = check_category(payload.get("category"))

    try:
        ward_number = int(payload.get("ward_number"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid ward number")

    if not name:
        raise HTTPException(400, "Shop name required")

    if not 1 <= ward_number <= TOTAL_WARDS:
        raise HTTPException(400, "Ward must be 1-86")

    locality = str(payload.get("locality", "")).strip()

    if not locality:
        raise HTTPException(
            400,
            "Enter the shop locality/area name"
        )

    shop_token = secrets.token_urlsafe(32)

    with get_db() as conn:
        ward = conn.execute(
            """
            SELECT id FROM wards
            WHERE ward_number=%s
            """,
            (ward_number,)
        ).fetchone()

        if not ward:
            raise HTTPException(404, "Ward not found")

        conn.execute(
            """
            INSERT INTO localities(ward_id, locality_name)
            VALUES(%s,%s)
            ON CONFLICT(ward_id, locality_name) DO NOTHING
            """,
            (ward["id"], locality)
        )

        shop = conn.execute(
            """
            INSERT INTO shops(
                shop_name, category, ward_id, address,
                phone, owner_name, locality,
                image_url, shop_update_token, is_verified
            )
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE)
            RETURNING id, shop_name, category
            """,
            (
                name,
                category,
                ward["id"],
                payload.get("address"),
                payload.get("phone"),
                payload.get("owner_name"),
                locality,
                payload.get("image_url"),
                shop_token
            )
        ).fetchone()

    return {
        "success": True,
        "shop": shop,
        "shop_update_token": shop_token,
        "message": "Save this token and share it securely with the shop owner."
    }


@app.get("/api/products")
def get_products(
    category: str = "All",
    search: Optional[str] = None
):
    if category != "All":
        check_category(category)

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT id, product_name, category, unit
            FROM products
            WHERE (%s::text='All' OR category=%s)
              AND (
                %s::text IS NULL
                OR product_name ILIKE '%' || %s || '%'
              )
            ORDER BY category, product_name
            LIMIT 1000
            """,
            (category, category, search, search)
        ).fetchall()

    return {"products": rows}


@app.post("/api/shop/price-update")
def shop_price_update(
    payload: dict,
    x_shop_token: Optional[str] = Header(None)
):
    if not x_shop_token:
        raise HTTPException(401, "X-Shop-Token required")

    name = str(payload.get("product_name", "")).strip()
    category = check_category(payload.get("category"))
    unit = str(payload.get("unit", "kg")).strip() or "kg"

    try:
        price = float(payload.get("price"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Enter a valid price")

    if not name or price <= 0:
        raise HTTPException(400, "Product and positive price required")

    if payload.get("confirm_same_as_shop_price") is not True:
        raise HTTPException(
            400,
            "Owner must confirm current in-store price"
        )

    with get_db() as conn:
        shop = conn.execute(
            """
            SELECT id, category
            FROM shops
            WHERE shop_update_token=%s
            """,
            (x_shop_token,)
        ).fetchone()

        if not shop:
            raise HTTPException(403, "Invalid shop token")

        if shop["category"] != category:
            raise HTTPException(
                400,
                "Product category must match shop category"
            )

        product = conn.execute(
            """
            SELECT id FROM products
            WHERE LOWER(product_name)=LOWER(%s)
              AND LOWER(category)=LOWER(%s)
              AND LOWER(unit)=LOWER(%s)
            LIMIT 1
            """,
            (name, category, unit)
        ).fetchone()

        if not product:
            product = conn.execute(
                """
                INSERT INTO products(product_name, category, unit)
                VALUES(%s,%s,%s)
                RETURNING id
                """,
                (name, category, unit)
            ).fetchone()

        conn.execute(
            """
            INSERT INTO prices(
                shop_id, product_id, price,
                price_type, source_name, source_type,
                is_live, in_store_confirmed,
                valid_until, updated_at
            )
            VALUES(
                %s,%s,%s,'retail','Shop owner',
                'shop_owner_confirmed',TRUE,TRUE,
                NOW()+INTERVAL '24 hours',NOW()
            )
            """,
            (shop["id"], product["id"], price)
        )

    return {
        "success": True,
        "status": "Verified",
        "valid_for_hours": 24
    }


@app.get("/api/unified-price-tracker")
def unified_price_tracker(
    category: str = "All",
    ward_number: Optional[int] = Query(None, ge=1, le=86),
    product: Optional[str] = None,
    locality: Optional[str] = None,
    sort: str = "low"
):
    if category != "All":
        check_category(category)

    order = "DESC" if sort.lower() == "high" else "ASC"

    sql = f"""
        SELECT
            p.product_name,
            p.category,
            p.unit,
            pr.price,
            pr.source_type,
            pr.source_name,
            pr.in_store_confirmed,
            pr.valid_until,
            pr.updated_at,
            s.id AS shop_id,
            s.shop_name,
            s.address,
            s.phone,
            s.locality,
            w.ward_number,
            w.ward_name
        FROM prices pr
        JOIN products p ON p.id=pr.product_id
        JOIN shops s ON s.id=pr.shop_id
        JOIN wards w ON w.id=s.ward_id
        WHERE pr.is_live=TRUE
          AND (pr.valid_until IS NULL OR pr.valid_until>=NOW())
          AND (%s::text='All' OR LOWER(p.category)=LOWER(%s))
          AND (%s::integer IS NULL OR w.ward_number=%s)
          AND (%s::text IS NULL OR LOWER(p.product_name)=LOWER(%s))
          AND (%s::text IS NULL OR LOWER(s.locality)=LOWER(%s))
        ORDER BY pr.price {order}, pr.updated_at DESC
        LIMIT 1000
    """

    with get_db() as conn:
        rows = conn.execute(
            sql,
            (
                category, category,
                ward_number, ward_number,
                product, product,
                locality, locality
            )
        ).fetchall()

    for row in rows:
        row["status"] = get_price_status(row)

    return {
        "count": len(rows),
        "prices": rows,
        "categories": CATEGORIES,
        "message": (
            "Only current shop-owner confirmed retail prices are shown."
        )
    }


@app.get("/api/price-tracker")
def price_tracker(
    category: str = "All",
    product: Optional[str] = None,
    ward_number: Optional[int] = Query(None, ge=1, le=86)
):
    return unified_price_tracker(
        category=category,
        product=product,
        ward_number=ward_number
    )


@app.get("/api/price-comparison")
def price_comparison(
    product: str = Query(..., min_length=1),
    ward_number: Optional[int] = Query(None, ge=1, le=86),
    sort: str = "low"
):
    return unified_price_tracker(
        category="All",
        product=product,
        ward_number=ward_number,
        sort=sort
    )


@app.get("/api/offers")
def get_offers(
    ward_number: Optional[int] = Query(None, ge=1, le=86)
):
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT
                o.id, o.title, o.description,
                o.discount_text, o.valid_from,
                o.valid_until, s.shop_name,
                s.category, s.locality,
                w.ward_number
            FROM offers o
            JOIN shops s ON s.id=o.shop_id
            JOIN wards w ON w.id=s.ward_id
            WHERE o.is_active=TRUE
              AND (o.valid_until IS NULL OR o.valid_until>=NOW())
              AND (%s::integer IS NULL OR w.ward_number=%s)
            ORDER BY o.updated_at DESC
            LIMIT 300
            """,
            (ward_number, ward_number)
        ).fetchall()

    return {"offers": rows}


@app.post("/api/admin/offers")
def admin_create_offer(
    payload: dict,
    x_admin_token: Optional[str] = Header(None)
):
    check_admin(x_admin_token)

    try:
        shop_id = int(payload.get("shop_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Valid shop_id required")

    title = str(payload.get("title", "")).strip()

    if not title:
        raise HTTPException(400, "Offer title required")

    with get_db() as conn:
        shop = conn.execute(
            "SELECT id FROM shops WHERE id=%s",
            (shop_id,)
        ).fetchone()

        if not shop:
            raise HTTPException(404, "Shop not found")

        row = conn.execute(
            """
            INSERT INTO offers(
                shop_id, title, description,
                discount_text, valid_until, is_active
            )
            VALUES(
                %s,%s,%s,%s,%s,TRUE
            )
            RETURNING id, title
            """,
            (
                shop_id,
                title,
                payload.get("description"),
                payload.get("discount_text"),
                payload.get("valid_until")
            )
        ).fetchone()

    return {"success": True, "offer": row}


@app.get("/api/market-prices")
def market_prices(
    district: str = "Krishna",
    limit: int = Query(100, ge=1, le=500)
):
    if not DATA_GOV_IN_API_KEY:
        return {
            "available": False,
            "source": "AGMARKNET / data.gov.in",
            "message": "DATA_GOV_IN_API_KEY is not configured",
            "prices": []
        }

    params = urllib.parse.urlencode({
        "api-key": DATA_GOV_IN_API_KEY,
        "format": "json",
        "limit": limit,
        "filters[state]": "Andhra Pradesh",
        "filters[district]": district
    })

    url = (
        "https://api.data.gov.in/resource/"
        + AGMARKNET_RESOURCE + "?" + params
    )

    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "BezawadaDeals/11.0"}
        )

        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(
                response.read().decode("utf-8")
            )

        output = []

        for row in data.get("records", []):
            output.append({
                "commodity": row.get("commodity"),
                "market": row.get("market"),
                "district": row.get("district"),
                "variety": row.get("variety"),
                "arrival_date": row.get("arrival_date"),
                "min_price": row.get("min_price"),
                "max_price": row.get("max_price"),
                "modal_price": row.get("modal_price"),
                "unit": "quintal",
                "status": "Government wholesale",
                "source": "AGMARKNET / data.gov.in"
            })

        return {
            "available": True,
            "source": "AGMARKNET / data.gov.in",
            "note": (
                "Wholesale mandi prices per quintal. "
                "Not shop retail prices or ward-specific prices."
            ),
            "prices": output
        }

    except Exception as exc:
        return {
            "available": False,
            "source": "AGMARKNET / data.gov.in",
            "message": type(exc).__name__,
            "prices": []
        }


@app.get("/api/official-sources")
def official_sources():
    return {
        "categories": [
            {
                "category": category,
                "retail_source": "Shop-owner confirmed updates",
                "official_retail_feed": False
            }
            for category in CATEGORIES
        ],
        "government_source": {
            "name": "AGMARKNET / data.gov.in",
            "type": "Agricultural wholesale mandi prices",
            "retail_equivalent": False
        }
    }


@app.get("/api/owner-guide")
def owner_guide():
    return {
        "steps": [
            "Admin registers a shop.",
            "Admin shares the private shop token with its owner.",
            "Owner submits the current in-store price.",
            "Owner confirms the price is current.",
            "Verified price remains valid for 24 hours."
        ]
    }
