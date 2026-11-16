
import os
import json
import hashlib
import hmac
import secrets
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# --------------------------------------------------
# 1. APPLICATION SETTINGS
# --------------------------------------------------

APP_VERSION = "16.0.0"

DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
SHOP_OWNER_TOKEN = os.getenv("SHOP_OWNER_TOKEN", "")

DATA_GOV_IN_API_KEY = os.getenv("DATA_GOV_IN_API_KEY", "")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bezawadadeals")

app = FastAPI(
    title="BezawadaDeals API",
    version=APP_VERSION,
    description="Vijayawada local shop directory and price comparison"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------
# 2. DATABASE CONNECTION
# --------------------------------------------------

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is missing")

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=20
    )


def fetch_all(query, params=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return cur.fetchall()


def fetch_one(query, params=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return cur.fetchone()


def execute_query(query, params=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params or ())
            return cur.rowcount


def now_utc():
    return datetime.now(timezone.utc)


def require_admin(token):
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_TOKEN is not configured on the server"
        )

    if not token or not hmac.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def require_owner_token(token):
    if not SHOP_OWNER_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="SHOP_OWNER_TOKEN is not configured"
        )

    if not token or not hmac.compare_digest(token, SHOP_OWNER_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid owner token")


# --------------------------------------------------
# 3. SAFE DATABASE INITIALIZATION
# --------------------------------------------------

def init_db():
    """
    Creates only missing supporting tables and indexes.
    Does not DROP or TRUNCATE existing project tables.
    """

    statements = [

        """
        CREATE TABLE IF NOT EXISTS mandi_prices (
            id BIGSERIAL PRIMARY KEY,
            commodity TEXT,
            variety TEXT,
            market TEXT,
            district TEXT,
            state TEXT,
            arrival_date DATE,
            min_price NUMERIC(12,2),
            max_price NUMERIC(12,2),
            modal_price NUMERIC(12,2),
            source_name TEXT DEFAULT 'AGMARKNET',
            source_record JSONB,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS government_retail_benchmarks (
            id BIGSERIAL PRIMARY KEY,
            commodity TEXT NOT NULL,
            unit TEXT,
            price NUMERIC(12,2),
            source_name TEXT,
            source_date DATE,
            source_record JSONB,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,

        """
        CREATE TABLE IF NOT EXISTS source_sync (
            id BIGSERIAL PRIMARY KEY,
            source_name TEXT UNIQUE NOT NULL,
            last_sync_at TIMESTAMPTZ,
            status TEXT,
            message TEXT,
            records_count INTEGER DEFAULT 0
        )
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_mandi_district
        ON mandi_prices(district)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_mandi_commodity
        ON mandi_prices(commodity)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_mandi_updated
        ON mandi_prices(updated_at DESC)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_benchmark_commodity
        ON government_retail_benchmarks(commodity)
        """,

        """
        CREATE INDEX IF NOT EXISTS idx_benchmark_date
        ON government_retail_benchmarks(source_date DESC)
        """
    ]

    with get_db() as conn:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)

    logger.info("Database initialization completed safely")


@app.on_event("startup")
def startup_event():
    try:
        init_db()
        logger.info("BezawadaDeals API started")
    except Exception:
        logger.exception("Database initialization failed")


# --------------------------------------------------
# 4. COMMON VALIDATION HELPERS
# --------------------------------------------------

def clean_text(value, max_length=250):
    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    return value[:max_length]


def valid_price(value):
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid price")

    if not price.is_finite() or price <= 0:
        raise HTTPException(
            status_code=400,
            detail="Price must be greater than zero"
        )

    return price.quantize(Decimal("0.01"))


def verify_shop_exists(shop_id):
    shop = fetch_one(
        """
        SELECT id, shop_name, category, ward_id, locality_id,
               is_verified, is_active
        FROM shops
        WHERE id = %s
        """,
        (shop_id,)
    )

    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    if shop.get("is_active") is False:
        raise HTTPException(status_code=400, detail="Shop is inactive")

    return shop


def verify_product_exists(product_id):
    product = fetch_one(
        """
        SELECT id, product_name, category, unit
        FROM products
        WHERE id = %s
        """,
        (product_id,)
    )

    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    return product


# --------------------------------------------------
# 5. DATABASE STATUS
# --------------------------------------------------

@app.get("/health")
def health():
    try:
        result = fetch_one("SELECT NOW() AS database_time")

        return {
            "status": "ok",
            "application": "BezawadaDeals",
            "version": APP_VERSION,
            "database": "connected",
            "database_time": result["database_time"]
        }

    except Exception as exc:
        logger.exception("Health check failed")

        return {
            "status": "error",
            "database": "disconnected",
            "message": str(exc)
        }


@app.get("/api/status")
def api_status():
    tables = [
        "wards",
        "localities",
        "business_categories",
        "shops",
        "products",
        "prices",
        "offers",
        "shop_owners",
        "price_submissions",
        "price_history"
    ]

    counts = {}

    with get_db() as conn:
        with conn.cursor() as cur:
            for table in tables:
                # Table names are fixed internal constants, not user input.
                cur.execute(f"SELECT COUNT(*) AS total FROM {table}")
                result = cur.fetchone()
                counts[table] = result["total"]

    return {
        "application": "BezawadaDeals",
        "version": APP_VERSION,
        "database": "connected",
        "counts": counts
    }

   
# --------------------------------------------------
# 6. CATEGORIES API
# --------------------------------------------------

@app.get("/api/categories")
def get_categories():
    return fetch_all(
        """
        SELECT id, category_name
        FROM business_categories
        WHERE is_active = TRUE
        ORDER BY category_name
        """
    )


# --------------------------------------------------
# 7. WARDS API
# --------------------------------------------------

@app.get("/api/wards")
def get_wards():
    return fetch_all(
        """
        SELECT
            id,
            ward_number,
            COALESCE(ward_name, area_name) AS ward_name,
            area_name,
            is_active
        FROM wards
        WHERE COALESCE(is_active, TRUE) = TRUE
        ORDER BY ward_number
        """
    )


# --------------------------------------------------
# 8. LOCALITIES API
# --------------------------------------------------

@app.get("/api/localities")
def get_localities(
    ward_id: int | None = None,
    include_unverified: bool = True
):
    query = """
        SELECT
            l.id,
            l.ward_id,
            l.locality_name,
            l.locality_name_te,
            l.is_verified,
            w.ward_number,
            COALESCE(w.ward_name, w.area_name) AS ward_name
        FROM localities l
        LEFT JOIN wards w ON w.id = l.ward_id
        WHERE 1=1
    """

    params = []

    if ward_id is not None:
        query += " AND l.ward_id = %s"
        params.append(ward_id)

    if not include_unverified:
        query += " AND l.is_verified = TRUE"

    query += " ORDER BY w.ward_number, l.locality_name"

    return fetch_all(query, params)


# --------------------------------------------------
# 9. SHOPS DIRECTORY API
# --------------------------------------------------

@app.get("/api/shops")
def get_shops(
    category: str | None = None,
    ward_id: int | None = None,
    locality_id: int | None = None,
    search: str | None = None,
    verified_only: bool = False,
    limit: int = Query(default=500, ge=1, le=2000)
):
    query = """
        SELECT
            s.id,
            s.shop_name,
            s.category,
            s.category_id,
            s.address,
            s.phone,
            s.email,
            s.website,
            s.opening_hours,
            s.description,
            s.image_url,
            s.ward_id,
            s.locality_id,
            COALESCE(s.locality, l.locality_name) AS locality,
            COALESCE(w.ward_name, w.area_name) AS ward_name,
            w.ward_number,
            s.is_verified,
            s.is_active,
            CASE
                WHEN s.is_verified = TRUE THEN 'Verified'
                ELSE 'Unverified'
            END AS verification_status
        FROM shops s
        LEFT JOIN wards w ON w.id = s.ward_id
        LEFT JOIN localities l ON l.id = s.locality_id
        WHERE COALESCE(s.is_active, TRUE) = TRUE
    """

    params = []

    if category:
        query += " AND LOWER(s.category) = LOWER(%s)"
        params.append(category.strip())

    if ward_id is not None:
        query += " AND s.ward_id = %s"
        params.append(ward_id)

    if locality_id is not None:
        query += " AND s.locality_id = %s"
        params.append(locality_id)

    if verified_only:
        query += " AND s.is_verified = TRUE"

    if search:
        query += """
            AND (
                s.shop_name ILIKE %s
                OR s.category ILIKE %s
                OR COALESCE(s.address, '') ILIKE %s
                OR COALESCE(s.locality, l.locality_name, '') ILIKE %s
            )
        """
        term = f"%{search.strip()}%"
        params.extend([term, term, term, term])

    query += " ORDER BY s.shop_name LIMIT %s"
    params.append(limit)

    return fetch_all(query, params)


# --------------------------------------------------
# 10. PRODUCTS API
# --------------------------------------------------

@app.get("/api/products")
def get_products(
    category: str | None = None,
    search: str | None = None
):
    query = """
        SELECT id, product_name, category, unit, description
        FROM products
        WHERE 1=1
    """

    params = []

    if category:
        query += " AND category ILIKE %s"
        params.append(f"%{category.strip()}%")

    if search:
        query += " AND product_name ILIKE %s"
        params.append(f"%{search.strip()}%")

    query += " ORDER BY product_name"

    return fetch_all(query, params)


# --------------------------------------------------
# 11. VERIFIED PRICE COMPARISON
# --------------------------------------------------

@app.get("/api/unified-price-tracker")
@app.get("/api/price-tracker")
def unified_price_tracker(
    search: str | None = None,
    category: str | None = None,
    ward_id: int | None = None,
    locality_id: int | None = None,
    shop_id: int | None = None,
    sort: str = "low_to_high",
    limit: int = Query(default=500, ge=1, le=2000)
):
    query = """
        SELECT
            p.id AS price_id,
            pr.id AS product_id,
            pr.product_name,
            pr.category AS product_category,
            pr.unit,
            p.price,
            p.price_type,
            p.source_name,
            p.source_type,
            p.updated_at,
            p.valid_until,
            s.id AS shop_id,
            s.shop_name,
            s.category AS shop_category,
            s.address,
            s.phone,
            s.ward_id,
            s.locality_id,
            COALESCE(s.locality, l.locality_name) AS locality,
            COALESCE(w.ward_name, w.area_name) AS ward_name,
            w.ward_number,
            s.is_verified AS shop_verified
        FROM prices p
        JOIN products pr ON pr.id = p.product_id
        JOIN shops s ON s.id = p.shop_id
        LEFT JOIN wards w ON w.id = s.ward_id
        LEFT JOIN localities l ON l.id = s.locality_id
        WHERE
            p.price > 0
            AND COALESCE(s.is_active, TRUE) = TRUE
            AND p.in_store_confirmed = TRUE
            AND (p.valid_until IS NULL OR p.valid_until > NOW())
    """

    params = []

    if search:
        query += " AND pr.product_name ILIKE %s"
        params.append(f"%{search.strip()}%")

    if category:
        # This filter is for the shop/business category.
        query += " AND s.category ILIKE %s"
        params.append(f"%{category.strip()}%")

    if ward_id is not None:
        query += " AND s.ward_id = %s"
        params.append(ward_id)

    if locality_id is not None:
        query += " AND s.locality_id = %s"
        params.append(locality_id)

    if shop_id is not None:
        query += " AND s.id = %s"
        params.append(shop_id)

    if sort == "high_to_low":
        query += " ORDER BY pr.product_name, p.price DESC"
    else:
        query += " ORDER BY pr.product_name, p.price ASC"

    query += " LIMIT %s"
    params.append(limit)

    return fetch_all(query, params)


# --------------------------------------------------
# 12. OFFERS API
# --------------------------------------------------

@app.get("/api/offers")
def get_offers(
    category: str | None = None,
    ward_id: int | None = None,
    limit: int = Query(default=300, ge=1, le=1000)
):
    query = """
        SELECT
            o.id,
            o.shop_id,
            s.shop_name,
            s.category AS shop_category,
            s.address,
            s.ward_id,
            COALESCE(w.ward_name, w.area_name) AS ward_name,
            o.product_id,
            p.product_name,
            o.title,
            o.description,
            o.discount_text,
            o.offer_type,
            o.terms_conditions,
            o.valid_from,
            o.valid_until,
            o.source_type,
            o.is_verified
        FROM offers o
        JOIN shops s ON s.id = o.shop_id
        LEFT JOIN products p ON p.id = o.product_id
        LEFT JOIN wards w ON w.id = s.ward_id
        WHERE
            o.is_active = TRUE
            AND COALESCE(s.is_active, TRUE) = TRUE
            AND (o.valid_from IS NULL OR o.valid_from <= NOW())
            AND (o.valid_until IS NULL OR o.valid_until >= NOW())
    """

    params = []

    if category:
        query += " AND s.category ILIKE %s"
        params.append(f"%{category.strip()}%")

    if ward_id is not None:
        query += " AND s.ward_id = %s"
        params.append(ward_id)

    query += " ORDER BY o.valid_until NULLS LAST LIMIT %s"
    params.append(limit)

    return fetch_all(query, params)


# --------------------------------------------------
# 13. OFFICIAL PRICE DATA ENDPOINTS
# --------------------------------------------------

@app.get("/api/market-prices")
def get_market_prices(
    commodity: str | None = None,
    district: str | None = None,
    limit: int = Query(default=300, ge=1, le=1000)
):
    query = """
        SELECT
            id,
            commodity,
            variety,
            market,
            district,
            state,
            arrival_date,
            min_price,
            max_price,
            modal_price,
            source_name,
            updated_at
        FROM mandi_prices
        WHERE 1=1
    """

    params = []

    if commodity:
        query += " AND commodity ILIKE %s"
        params.append(f"%{commodity.strip()}%")

    if district:
        query += " AND district ILIKE %s"
        params.append(f"%{district.strip()}%")

    query += " ORDER BY arrival_date DESC NULLS LAST LIMIT %s"
    params.append(limit)

    return fetch_all(query, params)


@app.get("/api/official-retail-prices")
def get_official_retail_prices(
    commodity: str | None = None,
    limit: int = Query(default=300, ge=1, le=1000)
):
    query = """
        SELECT
            id,
            commodity,
            unit,
            price,
            source_name,
            source_date,
            updated_at
        FROM government_retail_benchmarks
        WHERE 1=1
    """

    params = []

    if commodity:
        query += " AND commodity ILIKE %s"
        params.append(f"%{commodity.strip()}%")

    query += " ORDER BY source_date DESC NULLS LAST LIMIT %s"
    params.append(limit)

    return fetch_all(query, params)


@app.get("/api/official-sources")
def official_sources():
    return {
        "sources": [
            {
                "name": "AGMARKNET",
                "type": "Wholesale mandi prices",
                "note": "Not the same as prices in Vijayawada retail shops"
            },
            {
                "name": "Department of Consumer Affairs",
                "type": "Government retail benchmark",
                "note": "Reference benchmark; not a branch-specific shop quote"
            },
            {
                "name": "Vijayawada physical shops",
                "type": "Local shop prices",
                "note": "Shown only when submitted and confirmed through the website workflow"
            }
        ]
    }


# --------------------------------------------------
# 14. REQUEST MODELS
# --------------------------------------------------

class PriceUpdateRequest(BaseModel):
    shop_name: str = Field(min_length=2, max_length=200)
    product_id: int
    price: Decimal
    unit: str = Field(min_length=1, max_length=50)
    ward_id: int | None = None
    locality_id: int | None = None
    submitted_by: str | None = None
    evidence_url: str | None = None


class OfferRequest(BaseModel):
    shop_id: int
    title: str = Field(min_length=2, max_length=250)
    description: str | None = None
    discount_text: str | None = None
    product_id: int | None = None
    offer_type: str | None = None
    terms_conditions: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None


# --------------------------------------------------
# 15. SHOP OWNER PRICE SUBMISSION
# --------------------------------------------------

@app.post("/api/shop/price-update")
def submit_shop_price(
    data: PriceUpdateRequest,
    x_shop_owner_token: str | None = Header(default=None)
):
    require_owner_token(x_shop_owner_token)

    price = valid_price(data.price)
    product = verify_product_exists(data.product_id)

    # Match only a single existing shop.
    # Branches with different localities must not be merged.
    query = """
        SELECT id, shop_name, ward_id, locality_id
        FROM shops
        WHERE LOWER(shop_name) = LOWER(%s)
          AND COALESCE(is_active, TRUE) = TRUE
    """
    params = [data.shop_name.strip()]

    if data.ward_id is not None:
        query += " AND (ward_id = %s OR ward_id IS NULL)"
        params.append(data.ward_id)

    if data.locality_id is not None:
        query += " AND (locality_id = %s OR locality_id IS NULL)"
        params.append(data.locality_id)

    query += """
        ORDER BY
            CASE WHEN ward_id = %s THEN 0 ELSE 1 END,
            CASE WHEN locality_id = %s THEN 0 ELSE 1 END
        LIMIT 2
    """
    params.extend([data.ward_id, data.locality_id])

    matches = fetch_all(query, params)

    if len(matches) == 0:
        raise HTTPException(
            status_code=404,
            detail="Shop not found. Register the shop before submitting prices."
        )

    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail="Multiple shop branches found. Contact admin to select the correct branch."
        )

    shop = matches[0]

    if data.ward_id is not None and shop["ward_id"] is not None:
        if shop["ward_id"] != data.ward_id:
            raise HTTPException(status_code=400, detail="Shop ward mismatch")

    if data.locality_id is not None and shop["locality_id"] is not None:
        if shop["locality_id"] != data.locality_id:
            raise HTTPException(status_code=400, detail="Shop locality mismatch")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO price_submissions (
                    shop_id,
                    product_id,
                    submitted_price,
                    unit,
                    submitted_by,
                    evidence_url,
                    status,
                    submitted_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', NOW())
                RETURNING id
                """,
                (
                    shop["id"],
                    data.product_id,
                    price,
                    data.unit.strip(),
                    clean_text(data.submitted_by, 150),
                    clean_text(data.evidence_url, 1000)
                )
            )

            submission = cur.fetchone()

            cur.execute(
                """
                INSERT INTO shop_update_logs (
                    shop_id,
                    action_type,
                    description,
                    submitted_by
                )
                VALUES (%s, 'PRICE_SUBMITTED', %s, %s)
                """,
                (
                    shop["id"],
                    f"Price submission for {product['product_name']}",
                    clean_text(data.submitted_by, 150)
                )
            )

    return {
        "success": True,
        "message": "Price submitted for admin verification",
        "submission_id": submission["id"],
        "status": "pending"
    }


# --------------------------------------------------
# 16. ADMIN: VIEW PENDING PRICE SUBMISSIONS
# --------------------------------------------------

@app.get("/api/admin/price-submissions")
def admin_price_submissions(
    x_admin_token: str | None = Header(default=None),
    status: str = "pending"
):
    require_admin(x_admin_token)

    if status not in ("pending", "approved", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid status")

    return fetch_all(
        """
        SELECT
            ps.id,
            ps.shop_id,
            s.shop_name,
            s.category AS shop_category,
            s.address,
            ps.product_id,
            p.product_name,
            ps.submitted_price,
            ps.unit,
            ps.submitted_by,
            ps.evidence_url,
            ps.status,
            ps.admin_note,
            ps.submitted_at
        FROM price_submissions ps
        JOIN shops s ON s.id = ps.shop_id
        JOIN products p ON p.id = ps.product_id
        WHERE ps.status = %s
        ORDER BY ps.submitted_at DESC
        """,
        (status,)
    )


# --------------------------------------------------
# 17. ADMIN: APPROVE OR REJECT PRICE
# --------------------------------------------------

@app.post("/api/admin/price-submissions/{submission_id}/approve")
def approve_price_submission(
    submission_id: int,
    x_admin_token: str | None = Header(default=None)
):
    require_admin(x_admin_token)

    with get_db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT
                    ps.*,
                    p.product_name,
                    s.shop_name
                FROM price_submissions ps
                JOIN products p ON p.id = ps.product_id
                JOIN shops s ON s.id = ps.shop_id
                WHERE ps.id = %s
                FOR UPDATE OF ps
                """,
                (submission_id,)
            )

            submission = cur.fetchone()

            if not submission:
                raise HTTPException(
                    status_code=404,
                    detail="Submission not found"
                )

            if submission["status"] != "pending":
                raise HTTPException(
                    status_code=409,
                    detail="This submission has already been reviewed"
                )

            # Lock matching current-price rows to prevent simultaneous
            # approvals from creating competing current prices.
            cur.execute(
                """
                SELECT id, price
                FROM prices
                WHERE shop_id = %s
                  AND product_id = %s
                  AND in_store_confirmed = TRUE
                  AND (valid_until IS NULL OR valid_until > NOW())
                ORDER BY updated_at DESC NULLS LAST, id DESC
                FOR UPDATE
                """,
                (submission["shop_id"], submission["product_id"])
            )

            old_rows = cur.fetchall()
            old_price = old_rows[0]["price"] if old_rows else None

            # Expire existing confirmed prices for this shop/product.
            cur.execute(
                """
                UPDATE prices
                SET valid_until = NOW(),
                    updated_at = NOW()
                WHERE shop_id = %s
                  AND product_id = %s
                  AND in_store_confirmed = TRUE
                  AND (valid_until IS NULL OR valid_until > NOW())
                """,
                (submission["shop_id"], submission["product_id"])
            )

            # Insert the newly approved price.
            cur.execute(
                """
                INSERT INTO prices (
                    shop_id,
                    product_id,
                    price,
                    price_type,
                    source_name,
                    updated_at,
                    source_type,
                    is_live,
                    in_store_confirmed,
                    valid_until
                )
                VALUES (
                    %s, %s, %s,
                    'Retail',
                    'BezawadaDeals Admin Verification',
                    NOW(),
                    'Verified',
                    TRUE,
                    TRUE,
                    NOW() + INTERVAL '24 hours'
                )
                RETURNING id
                """,
                (
                    submission["shop_id"],
                    submission["product_id"],
                    submission["submitted_price"]
                )
            )

            new_price_row = cur.fetchone()

            cur.execute(
                """
                INSERT INTO price_history (
                    shop_id,
                    product_id,
                    old_price,
                    new_price,
                    unit,
                    source_type,
                    changed_at
                )
                VALUES (%s, %s, %s, %s, %s, 'Admin Verified', NOW())
                """,
                (
                    submission["shop_id"],
                    submission["product_id"],
                    old_price,
                    submission["submitted_price"],
                    submission["unit"]
                )
            )

            cur.execute(
                """
                UPDATE price_submissions
                SET status = 'approved',
                    reviewed_at = NOW(),
                    reviewed_by = 'admin'
                WHERE id = %s
                """,
                (submission_id,)
            )

            cur.execute(
                """
                INSERT INTO shop_update_logs (
                    shop_id,
                    action_type,
                    description,
                    submitted_by
                )
                VALUES (%s, 'PRICE_APPROVED', %s, 'admin')
                """,
                (
                    submission["shop_id"],
                    f"Approved price for {submission['product_name']}"
                )
            )

    return {
        "success": True,
        "message": "Price approved and published",
        "price_id": new_price_row["id"],
        "valid_for_hours": 24
    }


@app.post("/api/admin/price-submissions/{submission_id}/reject")
def reject_price_submission(
    submission_id: int,
    note: str = Query(default="Rejected by admin"),
    x_admin_token: str | None = Header(default=None)
):
    require_admin(x_admin_token)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE price_submissions
                SET status = 'rejected',
                    admin_note = %s,
                    reviewed_at = NOW(),
                    reviewed_by = 'admin'
                WHERE id = %s
                  AND status = 'pending'
                RETURNING id, shop_id
                """,
                (clean_text(note, 1000), submission_id)
            )

            result = cur.fetchone()

            if not result:
                raise HTTPException(
                    status_code=404,
                    detail="Pending submission not found"
                )

            cur.execute(
                """
                INSERT INTO shop_update_logs (
                    shop_id,
                    action_type,
                    description,
                    submitted_by
                )
                VALUES (%s, 'PRICE_REJECTED', %s, 'admin')
                """,
                (
                    result["shop_id"],
                    clean_text(note, 1000)
                )
            )

    return {
        "success": True,
        "message": "Price submission rejected"
    }


# --------------------------------------------------
# 18. LEGACY ADMIN PRICE VERIFICATION
# --------------------------------------------------

@app.post("/api/admin/prices/{price_id}/verify")
def verify_existing_price(
    price_id: int,
    x_admin_token: str | None = Header(default=None)
):
    require_admin(x_admin_token)

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE prices
                SET source_type = 'Verified',
                    in_store_confirmed = TRUE,
                    is_live = TRUE,
                    updated_at = NOW(),
                    valid_until = NOW() + INTERVAL '24 hours'
                WHERE id = %s
                  AND price > 0
                RETURNING id, shop_id, product_id, price
                """,
                (price_id,)
            )

            result = cur.fetchone()

            if not result:
                raise HTTPException(
                    status_code=404,
                    detail="Price not found"
                )

    return {
        "success": True,
        "message": "Existing price verified",
        "price_id": result["id"]
    }


# --------------------------------------------------
# 19. ADMIN: ADD OFFER
# --------------------------------------------------

@app.post("/api/admin/offers")
def admin_add_offer(
    data: OfferRequest,
    x_admin_token: str | None = Header(default=None)
):
    require_admin(x_admin_token)

    verify_shop_exists(data.shop_id)

    if data.product_id is not None:
        verify_product_exists(data.product_id)

    if (
        data.valid_from is not None
        and data.valid_until is not None
        and data.valid_until <= data.valid_from
    ):
        raise HTTPException(
            status_code=400,
            detail="Offer end time must be after start time"
        )

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO offers (
                    shop_id,
                    title,
                    description,
                    discount_text,
                    valid_from,
                    valid_until,
                    is_active,
                    updated_at,
                    product_id,
                    offer_type,
                    terms_conditions,
                    source_type,
                    is_verified
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    TRUE, NOW(), %s, %s, %s,
                    'Admin Added', TRUE
                )
                RETURNING id
                """,
                (
                    data.shop_id,
                    data.title.strip(),
                    clean_text(data.description, 2000),
                    clean_text(data.discount_text, 500),
                    data.valid_from,
                    data.valid_until,
                    data.product_id,
                    clean_text(data.offer_type, 100),
                    clean_text(data.terms_conditions, 2000)
                )
            )

            result = cur.fetchone()

    return {
        "success": True,
        "offer_id": result["id"],
        "message": "Offer added"
    }


# --------------------------------------------------
# 20. ADMIN: REFRESH STATUS
# --------------------------------------------------

@app.post("/api/admin/refresh")
def admin_refresh(
    x_admin_token: str | None = Header(default=None)
):
    require_admin(x_admin_token)

    # This endpoint does not fabricate or import market data.
    # Government-source sync can be connected separately.
    return {
        "success": True,
        "message": "Refresh request received. No external data was imported.",
        "timestamp": now_utc()
    }


# --------------------------------------------------
# 21. PRICE HISTORY API
# --------------------------------------------------

@app.get("/api/price-history")
def get_price_history(
    product_id: int,
    shop_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500)
):
    query = """
        SELECT
            ph.id,
            ph.shop_id,
            s.shop_name,
            ph.product_id,
            p.product_name,
            ph.old_price,
            ph.new_price,
            ph.unit,
            ph.source_type,
            ph.changed_at
        FROM price_history ph
        JOIN shops s ON s.id = ph.shop_id
        JOIN products p ON p.id = ph.product_id
        WHERE ph.product_id = %s
    """

    params = [product_id]

    if shop_id is not None:
        query += " AND ph.shop_id = %s"
        params.append(shop_id)

    query += " ORDER BY ph.changed_at DESC LIMIT %s"
    params.append(limit)

    return fetch_all(query, params)


# --------------------------------------------------
# 22. HOME PAGE
# --------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home():
    index_path = os.path.join(os.path.dirname(__file__), "index.html")

    if os.path.isfile(index_path):
        with open(index_path, "r", encoding="utf-8") as file:
            return HTMLResponse(file.read())

    return HTMLResponse(
        """
        <!doctype html>
        <html>
        <head>
            <title>BezawadaDeals</title>
            <meta name="viewport" content="width=device-width, initial-scale=1">
        </head>
        <body>
            <h1>BezawadaDeals API is running</h1>
            <p>Frontend index.html was not found.</p>
            <p><a href="/docs">Open API documentation</a></p>
        </body>
        </html>
        """
    )


# --------------------------------------------------
# 23. ROOT API INFORMATION
# --------------------------------------------------

@app.get("/api")
def api_home():
    return {
        "name": "BezawadaDeals",
        "version": APP_VERSION,
        "status": "running",
        "docs": "/docs",
        "health": "/health"
    }

