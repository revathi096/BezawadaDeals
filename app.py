import os
import json
import secrets
import traceback
from pathlib import Path
from typing import Optional
from datetime import datetime

import psycopg
from psycopg.rows import dict_row
from fastapi import (
    FastAPI, Request, Header, HTTPException, Query,
    UploadFile, File
)
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

APP_VERSION = "7.0.0"
TOTAL_WARDS = 86
MAX_IMPORT_SHOPS = 1000

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

CATEGORY_LOOKUP = {x.casefold(): x for x in CATEGORIES}

app = FastAPI(
    title="BezawadaDeals",
    description="Vijayawada Local Shopping Marketplace",
    version=APP_VERSION
)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
PUBLIC_DIR = BASE_DIR / "public"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

if PUBLIC_DIR.is_dir():
    app.mount(
        "/static",
        StaticFiles(directory=str(PUBLIC_DIR)),
        name="static"
    )


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

def get_database_url():
    url = os.getenv("DATABASE_URL")

    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return url


def get_connection():
    url = get_database_url()

    if not url:
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL is not configured"
        )

    return psycopg.connect(
        url,
        connect_timeout=10,
        row_factory=dict_row
    )


def canonical_category(value):
    result = CATEGORY_LOOKUP.get(
        (value or "").strip().casefold()
    )

    if not result:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category: {value}"
        )

    return result


def check_admin_key(api_key):
    expected = os.getenv("PRICE_FEED_API_KEY")

    if not expected or not api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing API key"
        )

    if not secrets.compare_digest(expected, api_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )


# --------------------------------------------------
# DATABASE TABLE CREATION
# --------------------------------------------------

def create_database_tables():
    commands = [
        """
        CREATE TABLE IF NOT EXISTS wards (
            id BIGSERIAL PRIMARY KEY,
            ward_number INTEGER UNIQUE NOT NULL,
            area_name TEXT NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS localities (
            id BIGSERIAL PRIMARY KEY,
            ward_id BIGINT NOT NULL REFERENCES wards(id),
            locality_name TEXT NOT NULL,
            locality_name_te TEXT,
            is_verified BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(ward_id, locality_name)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS shops (
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
            shop_update_token TEXT UNIQUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS products (
            id BIGSERIAL PRIMARY KEY,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS prices (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT REFERENCES shops(id),
            product_id BIGINT NOT NULL REFERENCES products(id),
            price NUMERIC(12,2) NOT NULL CHECK(price >= 0),
            price_type TEXT DEFAULT 'shop_retail',
            source_name TEXT,
            source_type TEXT DEFAULT 'manual',
            is_live BOOLEAN DEFAULT FALSE,
            in_store_confirmed BOOLEAN DEFAULT FALSE,
            valid_until TIMESTAMPTZ,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS offers (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT NOT NULL REFERENCES shops(id)
                ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT,
            discount_text TEXT,
            valid_from TIMESTAMPTZ DEFAULT NOW(),
            valid_until TIMESTAMPTZ,
            is_active BOOLEAN DEFAULT TRUE,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        ALTER TABLE shops
        ADD COLUMN IF NOT EXISTS shop_update_token TEXT
        """,
        """
        ALTER TABLE prices
        ADD COLUMN IF NOT EXISTS in_store_confirmed BOOLEAN DEFAULT FALSE
        """,
        """
        ALTER TABLE prices
        ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_shop_update_token
        ON shops(shop_update_token)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_shops_ward
        ON shops(ward_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_shops_category
        ON shops(category)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_prices_shop_product
        ON prices(shop_id, product_id, updated_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_offers_shop
        ON offers(shop_id, is_active)
        """
    ]

    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                for command in commands:
                    cursor.execute(command)

                for ward_no in range(1, TOTAL_WARDS + 1):
                    area = WARD_AREA_NAMES.get(
                        ward_no,
                        f"Ward {ward_no}"
                    )

                    cursor.execute(
                        """
                        INSERT INTO wards(
                            ward_number, area_name, is_active
                        )
                        VALUES(%s, %s, TRUE)
                        ON CONFLICT(ward_number)
                        DO UPDATE SET
                            area_name = CASE
                                WHEN wards.area_name IS NULL
                                  OR TRIM(wards.area_name) = ''
                                  OR wards.area_name =
                                     'Ward ' || wards.ward_number::TEXT
                                THEN EXCLUDED.area_name
                                ELSE wards.area_name
                            END,
                            is_active = TRUE
                        """,
                        (ward_no, area)
                    )

            connection.commit()

        print("BezawadaDeals database setup completed")
        return True

    except Exception as error:
        print("Database setup error:", error)
        traceback.print_exc()
        return False


@app.on_event("startup")
async def startup_event():
    create_database_tables()


# --------------------------------------------------
# WEBSITE AND STATUS
# --------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    if not (TEMPLATE_DIR / "index.html").is_file():
        return HTMLResponse(
            "<h2>templates/index.html not found</h2>",
            status_code=500
        )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.get("/health")
async def health():
    return {
        "status": "online",
        "website": "BezawadaDeals",
        "version": APP_VERSION
    }


@app.get("/api/status")
async def api_status():
    return {
        "success": True,
        "version": APP_VERSION,
        "categories": CATEGORIES
    }


@app.get("/api/db-status")
async def database_status():
    try:
        with get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema='public'
                    ORDER BY table_name
                """)

                tables = [
                    row["table_name"]
                    for row in cursor.fetchall()
                ]

        return {
            "success": True,
            "database": "connected",
            "tables": tables
        }

    except Exception as error:
        return {
            "success": False,
            "database": "connection failed",
            "detail": str(error)
        }


@app.get("/api/check")
async def check_files():
    return {
        "templates_folder": str(TEMPLATE_DIR),
        "index_html_exists": (
            TEMPLATE_DIR / "index.html"
        ).is_file(),
        "public_folder_exists": PUBLIC_DIR.is_dir()
    }


# --------------------------------------------------
# CATEGORIES AND WARDS
# --------------------------------------------------

@app.get("/api/categories")
async def get_categories():
    return {
        "success": True,
        "total_categories": len(CATEGORIES),
        "categories": CATEGORIES
    }


@app.get("/api/wards")
async def get_wards():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT id, ward_number, area_name, is_active
                FROM wards
                WHERE is_active=TRUE
                ORDER BY ward_number
            """)

            rows = cursor.fetchall()

    return {
        "success": True,
        "total_wards": len(rows),
        "wards": rows
    }


# --------------------------------------------------
# LOCALITY MANAGEMENT
# --------------------------------------------------

class LocalityInput(BaseModel):
    ward_number: int = Field(ge=1, le=86)
    locality_name: str = Field(min_length=2, max_length=150)
    locality_name_te: Optional[str] = None
    is_verified: bool = False


@app.post("/api/admin/localities")
async def add_locality(
    data: LocalityInput,
    x_api_key: Optional[str] = Header(default=None)
):
    check_admin_key(x_api_key)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM wards
                WHERE ward_number=%s AND is_active=TRUE
                """,
                (data.ward_number,)
            )

            ward = cursor.fetchone()

            if not ward:
                raise HTTPException(
                    status_code=404,
                    detail="Ward not found"
                )

            cursor.execute(
                """
                INSERT INTO localities(
                    ward_id, locality_name,
                    locality_name_te, is_verified
                )
                VALUES(%s, %s, %s, %s)
                ON CONFLICT(ward_id, locality_name)
                DO UPDATE SET
                    locality_name_te=EXCLUDED.locality_name_te,
                    is_verified=EXCLUDED.is_verified
                RETURNING id, locality_name
                """,
                (
                    ward["id"],
                    data.locality_name.strip(),
                    data.locality_name_te,
                    data.is_verified
                )
            )

            result = cursor.fetchone()

        connection.commit()

    return {
        "success": True,
        "locality": result
    }


# --------------------------------------------------
# SHOP INPUT AND SINGLE SHOP ADD
# --------------------------------------------------

class ShopInput(BaseModel):
    shop_name: str = Field(min_length=2, max_length=180)
    category: str
    ward_number: int = Field(ge=1, le=86)
    address: Optional[str] = None
    phone: Optional[str] = None
    owner_name: Optional[str] = None
    locality_name: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    image_url: Optional[str] = None


def get_or_create_locality(cursor, ward_id, locality_name):
    if not locality_name:
        return None

    name = locality_name.strip()

    if len(name) < 2:
        return None

    cursor.execute(
        """
        INSERT INTO localities(ward_id, locality_name)
        VALUES(%s, %s)
        ON CONFLICT(ward_id, locality_name)
        DO UPDATE SET locality_name=EXCLUDED.locality_name
        RETURNING id
        """,
        (ward_id, name)
    )

    return cursor.fetchone()["id"]


@app.post("/api/admin/shops")
async def add_shop(
    data: ShopInput,
    x_api_key: Optional[str] = Header(default=None)
):
    check_admin_key(x_api_key)
    category = canonical_category(data.category)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM wards
                WHERE ward_number=%s AND is_active=TRUE
                """,
                (data.ward_number,)
            )

            ward = cursor.fetchone()

            if not ward:
                raise HTTPException(
                    status_code=404,
                    detail="Ward not found"
                )

            locality_id = get_or_create_locality(
                cursor,
                ward["id"],
                data.locality_name
            )

            cursor.execute(
                """
                INSERT INTO shops(
                    shop_name, category, ward_id, address,
                    phone, owner_name, locality_id,
                    latitude, longitude, image_url
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id, shop_name, category
                """,
                (
                    data.shop_name.strip(),
                    category,
                    ward["id"],
                    data.address,
                    data.phone,
                    data.owner_name,
                    locality_id,
                    data.latitude,
                    data.longitude,
                    data.image_url
                )
            )

            shop = cursor.fetchone()

        connection.commit()

    return {
        "success": True,
        "shop": shop
    }


# --------------------------------------------------
# BULK SHOP IMPORT - UP TO 1000 SHOPS PER REQUEST
# --------------------------------------------------

class BulkShopRow(BaseModel):
    shop_name: str = Field(min_length=2, max_length=180)
    category: str
    ward_number: int = Field(ge=1, le=86)
    address: Optional[str] = None
    phone: Optional[str] = None
    owner_name: Optional[str] = None
    locality_name: Optional[str] = None
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)
    image_url: Optional[str] = None


class BulkShopImport(BaseModel):
    shops: list[BulkShopRow] = Field(
        min_length=1,
        max_length=MAX_IMPORT_SHOPS
    )


@app.post("/api/admin/import-shops")
async def import_shops(
    data: BulkShopImport,
    x_api_key: Optional[str] = Header(default=None)
):
    check_admin_key(x_api_key)

    inserted = 0
    skipped = 0
    seen_in_request = set()

    with get_connection() as connection:
        with connection.cursor() as cursor:

            for item in data.shops:
                shop_name = item.shop_name.strip()
                category = canonical_category(item.category)

                duplicate_key = (
                    item.ward_number,
                    shop_name.casefold(),
                    category.casefold()
                )

                if duplicate_key in seen_in_request:
                    skipped += 1
                    continue

                seen_in_request.add(duplicate_key)

                cursor.execute(
                    """
                    SELECT id FROM wards
                    WHERE ward_number=%s AND is_active=TRUE
                    """,
                    (item.ward_number,)
                )

                ward = cursor.fetchone()

                if not ward:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Ward {item.ward_number} does not exist "
                            "or is inactive. Import cancelled."
                        )
                    )

                cursor.execute(
                    """
                    SELECT id FROM shops
                    WHERE ward_id=%s
                      AND LOWER(TRIM(shop_name))=LOWER(%s)
                      AND LOWER(category)=LOWER(%s)
                    LIMIT 1
                    """,
                    (
                        ward["id"],
                        shop_name,
                        category
                    )
                )

                existing = cursor.fetchone()

                if existing:
                    skipped += 1
                    continue

                locality_id = get_or_create_locality(
                    cursor,
                    ward["id"],
                    item.locality_name
                )

                cursor.execute(
                    """
                    INSERT INTO shops(
                        shop_name, category, ward_id, address,
                        phone, owner_name, locality_id,
                        latitude, longitude, image_url,
                        is_verified, data_connection_status
                    )
                    VALUES(
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        FALSE,'not_connected'
                    )
                    RETURNING id
                    """,
                    (
                        shop_name,
                        category,
                        ward["id"],
                        item.address,
                        item.phone,
                        item.owner_name,
                        locality_id,
                        item.latitude,
                        item.longitude,
                        item.image_url
                    )
                )

                cursor.fetchone()
                inserted += 1

        connection.commit()

    return {
        "success": True,
        "received": len(data.shops),
        "inserted": inserted,
        "skipped_duplicates": skipped,
        "message": "Shop import completed"
    }


# --------------------------------------------------
# SHOP OWNER UPDATE TOKEN
# --------------------------------------------------

@app.post("/api/admin/shops/{shop_id}/create-update-token")
async def create_shop_update_token(
    shop_id: int,
    x_api_key: Optional[str] = Header(default=None)
):
    check_admin_key(x_api_key)
    token = secrets.token_urlsafe(32)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE shops
                SET shop_update_token=%s
                WHERE id=%s
                RETURNING id, shop_name
                """,
                (token, shop_id)
            )

            shop = cursor.fetchone()

            if not shop:
                raise HTTPException(
                    status_code=404,
                    detail="Shop not found"
                )

        connection.commit()

    return {
        "success": True,
        "shop": shop,
        "shop_update_token": token,
        "note": "Keep this token private."
    }


# --------------------------------------------------
# SHOP OWNER PRICE UPDATE
# --------------------------------------------------

class PriceUpdate(BaseModel):
    product_name: str = Field(min_length=1, max_length=150)
    category: str
    unit: str = Field(min_length=1, max_length=30)
    price: float = Field(ge=0)
    confirm_same_as_shop_price: bool


@app.post("/api/shop/price-update")
async def shop_owner_price_update(
    data: PriceUpdate,
    x_shop_token: Optional[str] = Header(default=None)
):
    if not x_shop_token:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Shop-Token"
        )

    if data.confirm_same_as_shop_price is not True:
        raise HTTPException(
            status_code=400,
            detail="Confirm current in-store price"
        )

    category = canonical_category(data.category)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM shops
                WHERE shop_update_token=%s
                """,
                (x_shop_token,)
            )

            shop = cursor.fetchone()

            if not shop:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid shop token"
                )

            cursor.execute(
                """
                SELECT id FROM products
                WHERE LOWER(product_name)=LOWER(%s)
                  AND LOWER(category)=LOWER(%s)
                  AND LOWER(unit)=LOWER(%s)
                LIMIT 1
                """,
                (
                    data.product_name.strip(),
                    category,
                    data.unit.strip()
                )
            )

            product = cursor.fetchone()

            if product:
                product_id = product["id"]
            else:
                cursor.execute(
                    """
                    INSERT INTO products(
                        product_name, category, unit
                    )
                    VALUES(%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        data.product_name.strip(),
                        category,
                        data.unit.strip()
                    )
                )

                product_id = cursor.fetchone()["id"]

            cursor.execute(
                """
                INSERT INTO prices(
                    shop_id, product_id, price, price_type,
                    source_name, source_type, is_live,
                    in_store_confirmed, valid_until, updated_at
                )
                VALUES(
                    %s,%s,%s,'shop_retail',
                    'Shop owner confirmed','shop_owner_confirmed',
                    FALSE,TRUE,NOW()+INTERVAL '24 hours',NOW()
                )
                RETURNING id, updated_at, valid_until
                """,
                (shop["id"], product_id, data.price)
            )

            result = cursor.fetchone()

            cursor.execute(
                """
                UPDATE shops
                SET data_connection_status='owner_updated'
                WHERE id=%s
                """,
                (shop["id"],)
            )

        connection.commit()

    return {
        "success": True,
        "price": result,
        "message": "Confirmed in-store price saved for 24 hours"
    }


# --------------------------------------------------
# SHOP OWNER OFFER UPDATE
# --------------------------------------------------

class OfferInput(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: Optional[str] = None
    discount_text: Optional[str] = None
    valid_until: Optional[datetime] = None


@app.post("/api/shop/offer-update")
async def shop_owner_offer_update(
    data: OfferInput,
    x_shop_token: Optional[str] = Header(default=None)
):
    if not x_shop_token:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Shop-Token"
        )

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM shops
                WHERE shop_update_token=%s
                """,
                (x_shop_token,)
            )

            shop = cursor.fetchone()

            if not shop:
                raise HTTPException(
                    status_code=401,
                    detail="Invalid shop token"
                )

            cursor.execute(
                """
                INSERT INTO offers(
                    shop_id, title, description,
                    discount_text, valid_until
                )
                VALUES(%s,%s,%s,%s,%s)
                RETURNING id, title, valid_until
                """,
                (
                    shop["id"],
                    data.title,
                    data.description,
                    data.discount_text,
                    data.valid_until
                )
            )

            result = cursor.fetchone()

        connection.commit()

    return {
        "success": True,
        "offer": result
    }


# --------------------------------------------------
# SHOP LIST BY WARD AND CATEGORY
# --------------------------------------------------

@app.get("/api/shops")
async def get_shops(
    ward_number: int = Query(..., ge=1, le=86),
    category: str = Query(...)
):
    category_value = (
        "All"
        if category.strip().casefold() == "all"
        else canonical_category(category)
    )

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    s.id,
                    s.shop_name,
                    s.shop_name AS name,
                    s.category,
                    s.address,
                    s.phone,
                    s.is_verified,
                    s.data_connection_status,
                    s.latitude,
                    s.longitude,
                    s.image_url,
                    w.ward_number,
                    w.area_name,
                    l.locality_name,
                    latest.product_name,
                    latest.unit,
                    latest.price,
                    latest.updated_at
                FROM shops s
                JOIN wards w ON w.id=s.ward_id
                LEFT JOIN localities l ON l.id=s.locality_id
                LEFT JOIN LATERAL (
                    SELECT
                        p.product_name,
                        p.unit,
                        pr.price,
                        pr.updated_at
                    FROM prices pr
                    JOIN products p ON p.id=pr.product_id
                    WHERE pr.shop_id=s.id
                      AND pr.in_store_confirmed=TRUE
                      AND pr.valid_until>NOW()
                    ORDER BY pr.updated_at DESC
                    LIMIT 1
                ) latest ON TRUE
                WHERE w.ward_number=%s
                  AND (
                    %s='All'
                    OR LOWER(s.category)=LOWER(%s)
                  )
                ORDER BY s.shop_name
                """,
                (
                    ward_number,
                    category_value,
                    category_value
                )
            )

            rows = cursor.fetchall()

    return {
        "success": True,
        "ward_number": ward_number,
        "category": category_value,
        "total_shops": len(rows),
        "shops": rows
    }


# --------------------------------------------------
# SHOP PRODUCTS
# --------------------------------------------------

@app.get("/api/shops/{shop_id}/products")
async def get_shop_products(shop_id: int):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ON (p.id)
                    p.id AS product_id,
                    p.product_name,
                    p.category,
                    p.unit,
                    pr.price,
                    pr.source_name,
                    pr.updated_at,
                    pr.valid_until
                FROM prices pr
                JOIN products p ON p.id=pr.product_id
                WHERE pr.shop_id=%s
                  AND pr.in_store_confirmed=TRUE
                  AND pr.valid_until>NOW()
                ORDER BY p.id, pr.updated_at DESC
                """,
                (shop_id,)
            )

            rows = cursor.fetchall()

    return {
        "success": True,
        "shop_id": shop_id,
        "total_products": len(rows),
        "products": rows
    }


# --------------------------------------------------
# PRICE COMPARISON
# --------------------------------------------------

@app.get("/api/price-comparison")
async def price_comparison(
    ward_number: int = Query(..., ge=1, le=86),
    product_name: str = Query(..., min_length=1),
    unit: Optional[str] = None
):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    s.id AS shop_id,
                    s.shop_name,
                    s.address,
                    s.phone,
                    s.latitude,
                    s.longitude,
                    w.ward_number,
                    l.locality_name,
                    p.product_name,
                    p.unit,
                    pr.price,
                    pr.updated_at,
                    pr.valid_until
                FROM prices pr
                JOIN shops s ON s.id=pr.shop_id
                JOIN wards w ON w.id=s.ward_id
                LEFT JOIN localities l ON l.id=s.locality_id
                JOIN products p ON p.id=pr.product_id
                WHERE w.ward_number=%s
                  AND LOWER(p.product_name)=LOWER(%s)
                  AND (%s IS NULL OR LOWER(p.unit)=LOWER(%s))
                  AND pr.in_store_confirmed=TRUE
                  AND pr.valid_until>NOW()
                ORDER BY pr.price ASC, pr.updated_at DESC
                """,
                (
                    ward_number,
                    product_name,
                    unit,
                    unit
                )
            )

            rows = cursor.fetchall()

    return {
        "success": True,
        "ward_number": ward_number,
        "product_name": product_name,
        "total_prices": len(rows),
        "prices": rows
    }


# --------------------------------------------------
# OFFERS
# --------------------------------------------------

@app.get("/api/offers")
async def get_offers(
    ward_number: int = Query(..., ge=1, le=86)
):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    o.id,
                    o.title,
                    o.description,
                    o.discount_text,
                    o.valid_from,
                    o.valid_until,
                    s.id AS shop_id,
                    s.shop_name,
                    s.category,
                    s.address,
                    s.phone
                FROM offers o
                JOIN shops s ON s.id=o.shop_id
                JOIN wards w ON w.id=s.ward_id
                WHERE w.ward_number=%s
                  AND o.is_active=TRUE
                  AND o.valid_from<=NOW()
                  AND (
                    o.valid_until IS NULL
                    OR o.valid_until>NOW()
                  )
                ORDER BY o.updated_at DESC
                """,
                (ward_number,)
            )

            rows = cursor.fetchall()

    return {
        "success": True,
        "total_offers": len(rows),
        "offers": rows
    }


# --------------------------------------------------
# PRICE TRACKER
# --------------------------------------------------

@app.get("/api/price-tracker")
async def price_tracker(
    ward_number: int = Query(..., ge=1, le=86),
    category: str = Query(...),
    product_name: Optional[str] = None
):
    category = canonical_category(category)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    s.id AS shop_id,
                    s.shop_name,
                    s.address,
                    s.phone,
                    w.ward_number,
                    p.product_name,
                    p.unit,
                    pr.price,
                    pr.updated_at,
                    pr.valid_until
                FROM prices pr
                JOIN shops s ON s.id=pr.shop_id
                JOIN wards w ON w.id=s.ward_id
                JOIN products p ON p.id=pr.product_id
                WHERE w.ward_number=%s
                  AND LOWER(s.category)=LOWER(%s)
                  AND (
                    %s IS NULL
                    OR LOWER(p.product_name)=LOWER(%s)
                  )
                  AND pr.in_store_confirmed=TRUE
                  AND pr.valid_until>NOW()
                ORDER BY pr.price ASC
                """,
                (
                    ward_number,
                    category,
                    product_name,
                    product_name
                )
            )

            rows = cursor.fetchall()

    return {
        "success": True,
        "total_price_records": len(rows),
        "prices": rows
    }


# --------------------------------------------------
# WARD AND LOCALITY IMPORT
# --------------------------------------------------

class WardImportItem(BaseModel):
    ward_number: int = Field(ge=1, le=86)
    area_name: str = Field(min_length=1, max_length=200)
    localities: list[str] = Field(default_factory=list)


class WardImportRequest(BaseModel):
    wards: list[WardImportItem]


def save_wards_data(data: WardImportRequest):
    if len(data.wards) != 86:
        raise HTTPException(
            status_code=400,
            detail="Ward data must contain exactly 86 wards"
        )

    numbers = [item.ward_number for item in data.wards]

    if sorted(numbers) != list(range(1, 87)):
        raise HTTPException(
            status_code=400,
            detail="Ward numbers must be 1 to 86 exactly once"
        )

    added = 0

    with get_connection() as connection:
        with connection.cursor() as cursor:
            for item in data.wards:
                cursor.execute(
                    """
                    INSERT INTO wards(
                        ward_number, area_name, is_active
                    )
                    VALUES(%s,%s,TRUE)
                    ON CONFLICT(ward_number)
                    DO UPDATE SET
                        area_name=EXCLUDED.area_name,
                        is_active=TRUE
                    RETURNING id
                    """,
                    (
                        item.ward_number,
                        item.area_name.strip()
                    )
                )

                ward_id = cursor.fetchone()["id"]

                for locality in item.localities:
                    name = locality.strip()

                    if len(name) < 2:
                        continue

                    cursor.execute(
                        """
                        INSERT INTO localities(
                            ward_id, locality_name, is_verified
                        )
                        VALUES(%s,%s,FALSE)
                        ON CONFLICT(ward_id, locality_name)
                        DO NOTHING
                        """,
                        (ward_id, name)
                    )

                    if cursor.rowcount:
                        added += 1

        connection.commit()

    return {
        "success": True,
        "wards_processed": 86,
        "new_localities_added": added,
        "verified": False
    }


@app.post("/api/admin/import-wards-json")
async def import_wards_json(
    data: WardImportRequest,
    x_api_key: Optional[str] = Header(default=None)
):
    check_admin_key(x_api_key)
    return save_wards_data(data)


@app.post("/api/admin/import-wards")
async def import_wards_file(
    file: UploadFile = File(...),
    x_api_key: Optional[str] = Header(default=None)
):
    check_admin_key(x_api_key)

    if (
        not file.filename
        or not file.filename.lower().endswith(".json")
    ):
        raise HTTPException(
            status_code=400,
            detail="Upload a JSON file"
        )

    try:
        content = await file.read()
        raw = json.loads(content.decode("utf-8-sig"))
        data = WardImportRequest(**raw)

        return save_wards_data(data)

    except HTTPException:
        raise

    except Exception as error:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid ward JSON: {str(error)}"
        )

    finally:
        await file.close()


# --------------------------------------------------
# OWNER GUIDE
# --------------------------------------------------

@app.get("/api/owner-guide")
async def owner_guide():
    return {
        "price_update_endpoint": "POST /api/shop/price-update",
        "offer_endpoint": "POST /api/shop/offer-update",
        "required_header": "X-Shop-Token",
        "price_validity": "24 hours",
        "note": "Shop owners must confirm actual in-store prices."
    }
