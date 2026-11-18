import os
import hmac
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

APP_VERSION = "18.0.0"
DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
SHOP_OWNER_TOKEN = os.getenv("SHOP_OWNER_TOKEN", "")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bezawadadeals")

app = FastAPI(title="BezawadaDeals API", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"]
)


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is missing")

    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=20
    )


def rows(sql, params=()):
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def one(sql, params=()):
    with db() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def clean(value, limit=250):
    value = str(value or "").strip()
    return value[:limit] or None


def require_token(given, expected, label):
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=f"{label} is not configured"
        )

    if not given or not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail="Invalid token")


# --------------------------------------------------
# SAFE DATABASE INITIALIZATION
# --------------------------------------------------

def init_db():
    statements = [
        """
        CREATE INDEX IF NOT EXISTS
        idx_prices_shop_product_time
        ON prices(shop_id, product_id, updated_at DESC)
        """,
        """
        CREATE INDEX IF NOT EXISTS
        idx_submissions_status
        ON price_submissions(status)
        """,
        """
        CREATE INDEX IF NOT EXISTS
        idx_mandi_commodity
        ON mandi_prices(commodity)
        """,
        """
        CREATE INDEX IF NOT EXISTS
        idx_benchmark_commodity
        ON government_retail_benchmarks(commodity)
        """
    ]

    with db() as conn, conn.cursor() as cur:
        for statement in statements:
            cur.execute(statement)

    log.info("Database indexes checked successfully")


@app.on_event("startup")
def startup():
    try:
        init_db()
    except Exception:
        log.exception("Database initialization failed")


# --------------------------------------------------
# HEALTH AND STATUS
# --------------------------------------------------

@app.get("/health")
def health():
    try:
        result = one("SELECT NOW() AS database_time")

        return {
            "status": "ok",
            "application": "BezawadaDeals",
            "version": APP_VERSION,
            "database": "connected",
            "database_time": result["database_time"]
        }

    except Exception:
        log.exception("Health check failed")

        return {
            "status": "error",
            "database": "disconnected"
        }


@app.get("/api")
def api_home():
    return {
        "name": "BezawadaDeals",
        "version": APP_VERSION,
        "status": "running",
        "docs": "/docs",
        "health": "/health"
    }


@app.get("/api/status")
def status():
    table_names = [
        "wards",
        "localities",
        "business_categories",
        "shops",
        "products",
        "prices",
        "offers",
        "shop_owners",
        "price_submissions",
        "price_history",
        "mandi_prices",
        "government_retail_benchmarks"
    ]

    counts = {}

    with db() as conn, conn.cursor() as cur:
        for table in table_names:
            cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
            counts[table] = cur.fetchone()["n"]

        cur.execute("""
            SELECT
                source_key,
                last_attempt,
                last_success,
                status,
                message
            FROM source_sync
            ORDER BY source_key
        """)

        sync_rows = cur.fetchall()

    return {
        "application": "BezawadaDeals",
        "version": APP_VERSION,
        "database": "connected",
        "counts": counts,
        "sources": sync_rows
    }


# --------------------------------------------------
# CATEGORIES
# --------------------------------------------------

@app.get("/api/categories")
def categories():
    data = rows("""
        SELECT id, category_name
        FROM business_categories
        WHERE COALESCE(is_active, TRUE) = TRUE
        ORDER BY category_name
    """)

    return {
        "count": len(data),
        "categories": data
    }


# --------------------------------------------------
# WARDS
# --------------------------------------------------

@app.get("/api/wards")
def wards():
    data = rows("""
        SELECT
            id,
            ward_number,
            COALESCE(
                NULLIF(ward_name, ''),
                NULLIF(area_name, ''),
                'Ward ' || ward_number::text
            ) AS ward_name,
            area_name,
            north_boundary,
            east_boundary,
            south_boundary,
            west_boundary,
            area_name_source,
            boundary_source,
            boundary_source_date,
            is_active
        FROM wards
        WHERE COALESCE(is_active, TRUE) = TRUE
        ORDER BY ward_number
    """)

    return {
        "count": len(data),
        "wards": data,
        "expected_total": 86
    }


# --------------------------------------------------
# LOCALITIES
# --------------------------------------------------

@app.get("/api/localities")
def localities(
    ward_id: Optional[int] = None,
    include_unverified: bool = True
):
    sql = """
        SELECT
            l.id,
            l.ward_id,
            l.locality_name AS name,
            l.locality_name,
            l.locality_name_te,
            l.is_verified,
            w.ward_number,
            COALESCE(
                NULLIF(w.ward_name, ''),
                w.area_name
            ) AS ward_name
        FROM localities l
        LEFT JOIN wards w ON w.id = l.ward_id
        WHERE 1=1
    """

    args = []

    if ward_id is not None:
        sql += " AND l.ward_id = %s"
        args.append(ward_id)

    if not include_unverified:
        sql += " AND l.is_verified = TRUE"

    sql += " ORDER BY w.ward_number, l.locality_name"

    data = rows(sql, args)

    return {
        "count": len(data),
        "localities": data
    }


# --------------------------------------------------
# SHOPS
# --------------------------------------------------

@app.get("/api/shops")
def shops(
    category: Optional[str] = None,
    ward_id: Optional[int] = None,
    ward_number: Optional[int] = None,
    locality_id: Optional[int] = None,
    search: Optional[str] = None,
    limit: int = Query(500, ge=1, le=2000)
):
    sql = """
        SELECT
            s.id,
            s.shop_name AS name,
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
            COALESCE(s.locality, l.locality_name) AS area,
            COALESCE(s.locality, l.locality_name) AS locality,
            w.ward_number,
            COALESCE(
                NULLIF(w.ward_name, ''),
                w.area_name
            ) AS ward_name,
            s.is_verified,
            s.is_active
        FROM shops s
        LEFT JOIN wards w ON w.id = s.ward_id
        LEFT JOIN localities l ON l.id = s.locality_id
        WHERE COALESCE(s.is_active, TRUE) = TRUE
    """

    args = []

    if category:
        sql += " AND s.category ILIKE %s"
        args.append(f"%{category.strip()}%")

    if ward_id is not None:
        sql += " AND s.ward_id = %s"
        args.append(ward_id)

    if ward_number is not None:
        sql += " AND w.ward_number = %s"
        args.append(ward_number)

    if locality_id is not None:
        sql += " AND s.locality_id = %s"
        args.append(locality_id)

    if search:
        term = f"%{search.strip()}%"
        sql += """
            AND (
                s.shop_name ILIKE %s
                OR s.category ILIKE %s
                OR COALESCE(s.address, '') ILIKE %s
            )
        """
        args.extend([term, term, term])

    sql += " ORDER BY s.shop_name LIMIT %s"
    args.append(limit)

    data = rows(sql, args)

    return {
        "count": len(data),
        "shops": data
    }


# --------------------------------------------------
# PRODUCTS
# --------------------------------------------------

@app.get("/api/products")
def products(
    category: Optional[str] = None,
    search: Optional[str] = None
):
    sql = """
        SELECT id, product_name, category, unit, description
        FROM products
        WHERE 1=1
    """

    args = []

    if category:
        sql += " AND category ILIKE %s"
        args.append(f"%{category.strip()}%")

    if search:
        sql += " AND product_name ILIKE %s"
        args.append(f"%{search.strip()}%")

    sql += " ORDER BY product_name"

    data = rows(sql, args)

    return {
        "count": len(data),
        "products": data
    }


# --------------------------------------------------
# UNIVERSAL PRICE TRACKER
# --------------------------------------------------

@app.get("/api/unified-price-tracker")
@app.get("/api/price-tracker")
def price_tracker(
    search: Optional[str] = None,
    product: Optional[str] = None,
    category: Optional[str] = None,
    ward_id: Optional[int] = None,
    ward_number: Optional[int] = None,
    locality_id: Optional[int] = None,
    area: Optional[str] = None,
    source_type: Optional[str] = None,
    sort: str = "low",
    include_expired: bool = False,
    limit: int = Query(500, ge=1, le=2000)
):
    term = search or product

    sql = """
        SELECT
            p.id AS price_id,
            pr.id AS product_id,
            pr.product_name AS product,
            pr.category AS product_category,
            s.category AS shop_category,
            pr.unit,
            p.price,
            s.shop_name AS shop,
            s.shop_name,
            s.category AS shop_type,
            COALESCE(s.locality, l.locality_name) AS area,
            w.ward_number,
            p.source_name AS source,
            p.source_type,
            p.updated_at AS observed_at,
            p.valid_until,
            p.price_type,
            s.address,
            s.phone,
            s.id AS shop_id,
            'Shop confirmed' AS status
        FROM prices p
        JOIN products pr ON pr.id = p.product_id
        JOIN shops s ON s.id = p.shop_id
        LEFT JOIN wards w ON w.id = s.ward_id
        LEFT JOIN localities l ON l.id = s.locality_id
        WHERE p.price > 0
          AND COALESCE(s.is_active, TRUE) = TRUE
          AND COALESCE(p.in_store_confirmed, FALSE) = TRUE
          AND COALESCE(p.is_live, TRUE) = TRUE
    """

    args = []

    if not include_expired:
        sql += """
            AND (
                p.valid_until IS NULL
                OR p.valid_until >= NOW()
            )
        """

    if term:
        sql += " AND pr.product_name ILIKE %s"
        args.append(f"%{term.strip()}%")

    if category:
        sql += """
            AND (
                pr.category ILIKE %s
                OR s.category ILIKE %s
            )
        """
        args.extend([
            f"%{category.strip()}%",
            f"%{category.strip()}%"
        ])

    if ward_id is not None:
        sql += " AND s.ward_id = %s"
        args.append(ward_id)

    if ward_number is not None:
        sql += " AND w.ward_number = %s"
        args.append(ward_number)

    if locality_id is not None:
        sql += " AND s.locality_id = %s"
        args.append(locality_id)

    if area:
        sql += """
            AND COALESCE(s.locality, l.locality_name, '') ILIKE %s
        """
        args.append(f"%{area.strip()}%")

    if source_type:
        sql += " AND COALESCE(p.source_type, '') ILIKE %s"
        args.append(f"%{source_type.strip()}%")

    order = "DESC" if sort in ("high", "high_to_low") else "ASC"

    sql += f" ORDER BY p.price {order}, p.updated_at DESC"
    sql += " LIMIT %s"
    args.append(limit)

    data = rows(sql, args)

    return {
        "count": len(data),
        "prices": data
    }


# --------------------------------------------------
# OFFERS
# --------------------------------------------------

@app.get("/api/offers")
def offers(
    category: Optional[str] = None,
    ward_id: Optional[int] = None,
    ward_number: Optional[int] = None,
    limit: int = Query(300, ge=1, le=1000)
):
    sql = """
        SELECT
            o.id,
            o.shop_id,
            s.shop_name AS shop,
            s.shop_name,
            s.category,
            s.address,
            COALESCE(s.locality, l.locality_name) AS area,
            w.ward_number,
            o.title,
            o.description,
            o.discount_text,
            o.valid_from,
            o.valid_until,
            o.product_id,
            p.product_name,
            o.offer_type,
            o.terms_conditions,
            o.source_type,
            o.is_verified
        FROM offers o
        JOIN shops s ON s.id = o.shop_id
        LEFT JOIN wards w ON w.id = s.ward_id
        LEFT JOIN localities l ON l.id = s.locality_id
        LEFT JOIN products p ON p.id = o.product_id
        WHERE COALESCE(o.is_active, TRUE) = TRUE
          AND COALESCE(s.is_active, TRUE) = TRUE
          AND (o.valid_from IS NULL OR o.valid_from <= NOW())
          AND (o.valid_until IS NULL OR o.valid_until >= NOW())
    """

    args = []

    if category:
        sql += " AND s.category ILIKE %s"
        args.append(f"%{category.strip()}%")

    if ward_id is not None:
        sql += " AND s.ward_id = %s"
        args.append(ward_id)

    if ward_number is not None:
        sql += " AND w.ward_number = %s"
        args.append(ward_number)

    sql += " ORDER BY o.valid_until NULLS LAST LIMIT %s"
    args.append(limit)

    data = rows(sql, args)

    return {
        "count": len(data),
        "offers": data
    }


# --------------------------------------------------
# AGMARKNET WHOLESALE PRICES
# Actual table columns: source, fetched_at
# --------------------------------------------------

@app.get("/api/market-prices")
def market_prices(
    commodity: Optional[str] = None,
    district: Optional[str] = None,
    limit: int = Query(300, ge=1, le=1000)
):
    sql = """
        SELECT
            id,
            commodity,
            variety,
            grade,
            market,
            district,
            state,
            arrival_date,
            min_price,
            max_price,
            modal_price,
            source,
            fetched_at
        FROM mandi_prices
        WHERE 1=1
    """

    args = []

    if commodity:
        sql += " AND commodity ILIKE %s"
        args.append(f"%{commodity.strip()}%")

    if district:
        sql += " AND district ILIKE %s"
        args.append(f"%{district.strip()}%")

    sql += " ORDER BY arrival_date DESC NULLS LAST LIMIT %s"
    args.append(limit)

    data = rows(sql, args)

    return {
        "count": len(data),
        "prices": data,
        "note": "Wholesale mandi prices; not local retail prices."
    }


# --------------------------------------------------
# GOVERNMENT RETAIL BENCHMARKS
# Actual table columns: report_date, geographic_scope,
# source, fetched_at
# --------------------------------------------------

@app.get("/api/official-retail-prices")
def official_retail_prices(
    commodity: Optional[str] = None,
    limit: int = Query(300, ge=1, le=1000)
):
    sql = """
        SELECT
            id,
            commodity,
            unit,
            price,
            report_date,
            geographic_scope,
            source,
            fetched_at
        FROM government_retail_benchmarks
        WHERE 1=1
    """

    args = []

    if commodity:
        sql += " AND commodity ILIKE %s"
        args.append(f"%{commodity.strip()}%")

    sql += " ORDER BY report_date DESC NULLS LAST LIMIT %s"
    args.append(limit)

    data = rows(sql, args)

    return {
        "count": len(data),
        "prices": data,
        "note": "Government benchmark data; not shop-specific prices."
    }


# --------------------------------------------------
# OFFICIAL SOURCES
# --------------------------------------------------

@app.get("/api/official-sources")
def official_sources():
    return {
        "sources": [
            {
                "name": "AGMARKNET",
                "type": "Wholesale mandi prices",
                "note": "Wholesale data is separate from local retail prices."
            },
            {
                "name": "Department of Consumer Affairs",
                "type": "Government retail benchmark",
                "note": "Government benchmark, not branch-specific shop prices."
            },
            {
                "name": "Vijayawada shop-owner submissions",
                "type": "Local retail prices",
                "note": "Owner submissions require admin approval."
            }
        ]
    }


# --------------------------------------------------
# SHOP OWNER PRICE SUBMISSION
# --------------------------------------------------

class OwnerPrice(BaseModel):
    shop_name: str = Field(min_length=2, max_length=200)
    shop_type: str = Field(min_length=2, max_length=100)
    area: str = Field(min_length=2, max_length=200)
    ward_number: int = Field(ge=1, le=86)
    product: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=2, max_length=100)
    unit: str = Field(min_length=1, max_length=50)
    price: Decimal
    address: Optional[str] = None
    phone: Optional[str] = None
    confirmed: bool


@app.post("/api/shop/price-update")
def owner_price_update(
    data: OwnerPrice,
    x_shop_token: Optional[str] = Header(default=None),
    x_shop_owner_token: Optional[str] = Header(default=None)
):
    require_token(
        x_shop_token or x_shop_owner_token,
        SHOP_OWNER_TOKEN,
        "SHOP_OWNER_TOKEN"
    )

    if not data.confirmed:
        raise HTTPException(
            400,
            "Please confirm the current selling price"
        )

    try:
        price = Decimal(data.price)
    except (InvalidOperation, TypeError, ValueError):
        raise HTTPException(400, "Invalid price")

    if not price.is_finite() or price <= 0:
        raise HTTPException(
            400,
            "Price must be greater than zero"
        )

    ward = one("""
        SELECT id
        FROM wards
        WHERE ward_number = %s
          AND COALESCE(is_active, TRUE) = TRUE
    """, (data.ward_number,))

    if not ward:
        raise HTTPException(404, "Ward not found")

    with db() as conn, conn.cursor() as cur:

        cur.execute("""
            SELECT id
            FROM localities
            WHERE ward_id = %s
              AND LOWER(locality_name) = LOWER(%s)
            LIMIT 1
        """, (ward["id"], data.area.strip()))

        locality = cur.fetchone()

        if not locality:
            cur.execute("""
                INSERT INTO localities (
                    ward_id,
                    locality_name,
                    is_verified
                )
                VALUES (%s, %s, FALSE)
                RETURNING id
            """, (ward["id"], data.area.strip()))

            locality = cur.fetchone()

        cur.execute("""
            SELECT id
            FROM shops
            WHERE LOWER(shop_name) = LOWER(%s)
              AND ward_id = %s
              AND COALESCE(is_active, TRUE) = TRUE
            LIMIT 1
        """, (data.shop_name.strip(), ward["id"]))

        shop = cur.fetchone()

        if not shop:
            cur.execute("""
                INSERT INTO shops (
                    shop_name,
                    category,
                    ward_id,
                    address,
                    phone,
                    is_verified,
                    locality_id,
                    locality,
                    is_active,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    FALSE, %s, %s, TRUE, NOW(), NOW()
                )
                RETURNING id
            """, (
                data.shop_name.strip(),
                data.shop_type.strip(),
                ward["id"],
                clean(data.address, 500),
                clean(data.phone, 30),
                locality["id"],
                data.area.strip()
            ))

            shop = cur.fetchone()

        cur.execute("""
            SELECT id
            FROM products
            WHERE LOWER(product_name) = LOWER(%s)
            LIMIT 1
        """, (data.product.strip(),))

        product_row = cur.fetchone()

        if not product_row:
            cur.execute("""
                INSERT INTO products (
                    product_name,
                    category,
                    unit
                )
                VALUES (%s, %s, %s)
                RETURNING id
            """, (
                data.product.strip(),
                data.category.strip(),
                data.unit.strip()
            ))

            product_row = cur.fetchone()

        cur.execute("""
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
            VALUES (%s, %s, %s, %s, %s, NULL, 'pending', NOW())
            RETURNING id
        """, (
            shop["id"],
            product_row["id"],
            price,
            data.unit.strip(),
            data.shop_name.strip()
        ))

        submission = cur.fetchone()

    return {
        "success": True,
        "submission_id": submission["id"],
        "message": "Price submitted for admin approval."
    }


# --------------------------------------------------
# ADMIN AUTHENTICATION
# --------------------------------------------------

def admin_auth(x_admin_token):
    require_token(x_admin_token, ADMIN_TOKEN, "ADMIN_TOKEN")


# --------------------------------------------------
# ADMIN: ADD OFFER
# --------------------------------------------------

class OfferInput(BaseModel):
    shop_id: int
    title: str = Field(min_length=2, max_length=250)
    description: Optional[str] = None
    discount_text: Optional[str] = None
    product_id: Optional[int] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    offer_type: Optional[str] = None
    terms_conditions: Optional[str] = None


@app.post("/api/admin/offers")
def add_offer(
    data: OfferInput,
    x_admin_token: Optional[str] = Header(default=None)
):
    admin_auth(x_admin_token)

    if not one("SELECT id FROM shops WHERE id = %s", (data.shop_id,)):
        raise HTTPException(404, "Shop not found")

    if (
        data.valid_from
        and data.valid_until
        and data.valid_until <= data.valid_from
    ):
        raise HTTPException(400, "Invalid offer dates")

    with db() as conn, conn.cursor() as cur:
        cur.execute("""
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
        """, (
            data.shop_id,
            data.title.strip(),
            clean(data.description, 2000),
            clean(data.discount_text, 500),
            data.valid_from,
            data.valid_until,
            data.product_id,
            clean(data.offer_type, 100),
            clean(data.terms_conditions, 2000)
        ))

        result = cur.fetchone()

    return {
        "success": True,
        "offer_id": result["id"]
    }


# --------------------------------------------------
# ADMIN: VIEW SUBMISSIONS
# --------------------------------------------------

@app.get("/api/admin/price-submissions")
def pending_submissions(
    status_filter: str = "pending",
    x_admin_token: Optional[str] = Header(default=None)
):
    admin_auth(x_admin_token)

    allowed = ("pending", "approved", "rejected")

    if status_filter not in allowed:
        raise HTTPException(400, "Invalid status_filter")

    data = rows("""
        SELECT
            ps.*,
            s.shop_name,
            p.product_name,
            s.ward_id,
            s.locality
        FROM price_submissions ps
        JOIN shops s ON s.id = ps.shop_id
        JOIN products p ON p.id = ps.product_id
        WHERE ps.status = %s
        ORDER BY ps.submitted_at DESC
    """, (status_filter,))

    return {
        "count": len(data),
        "submissions": data
    }


# --------------------------------------------------
# ADMIN: APPROVE SUBMISSION
# Update existing price instead of creating duplicates.
# --------------------------------------------------

@app.post("/api/admin/price-submissions/{submission_id}/approve")
def approve_submission(
    submission_id: int,
    x_admin_token: Optional[str] = Header(default=None)
):
    admin_auth(x_admin_token)

    with db() as conn, conn.cursor() as cur:

        cur.execute("""
            SELECT *
            FROM price_submissions
            WHERE id = %s
              AND status = 'pending'
            FOR UPDATE
        """, (submission_id,))

        sub = cur.fetchone()

        if not sub:
            raise HTTPException(404, "Pending submission not found")

        cur.execute("""
            SELECT id, price
            FROM prices
            WHERE shop_id = %s
              AND product_id = %s
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            FOR UPDATE
        """, (
            sub["shop_id"],
            sub["product_id"]
        ))

        existing = cur.fetchone()

        if existing:
            price_id = existing["id"]
            old_price = existing["price"]

            cur.execute("""
                UPDATE prices
                SET
                    price = %s,
                    price_type = 'Retail',
                    source_name = 'Admin verified owner submission',
                    source_type = 'Verified',
                    is_live = TRUE,
                    in_store_confirmed = TRUE,
                    updated_at = NOW(),
                    valid_until = NOW() + INTERVAL '24 hours'
                WHERE id = %s
            """, (
                sub["submitted_price"],
                price_id
            ))

        else:
            old_price = None

            cur.execute("""
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
                    %s, %s, %s, 'Retail',
                    'Admin verified owner submission',
                    NOW(), 'Verified', TRUE, TRUE,
                    NOW() + INTERVAL '24 hours'
                )
                RETURNING id
            """, (
                sub["shop_id"],
                sub["product_id"],
                sub["submitted_price"]
            ))

            price_id = cur.fetchone()["id"]

        cur.execute("""
            INSERT INTO price_history (
                shop_id,
                product_id,
                old_price,
                new_price,
                unit,
                source_type,
                changed_at
            )
            VALUES (%s, %s, %s, %s, %s, 'Admin-verified', NOW())
        """, (
            sub["shop_id"],
            sub["product_id"],
            old_price,
            sub["submitted_price"],
            sub["unit"]
        ))

        cur.execute("""
            UPDATE price_submissions
            SET
                status = 'approved',
                reviewed_at = NOW(),
                reviewed_by = 'admin',
                admin_note = 'Approved'
            WHERE id = %s
        """, (submission_id,))

    return {
        "success": True,
        "submission_id": submission_id,
        "price_id": price_id,
        "old_price": old_price,
        "new_price": sub["submitted_price"],
        "status": "approved"
    }


# --------------------------------------------------
# ADMIN: REJECT SUBMISSION
# --------------------------------------------------

@app.post("/api/admin/price-submissions/{submission_id}/reject")
def reject_submission(
    submission_id: int,
    note: Optional[str] = None,
    x_admin_token: Optional[str] = Header(default=None)
):
    admin_auth(x_admin_token)

    result = one("""
        UPDATE price_submissions
        SET
            status = 'rejected',
            admin_note = %s,
            reviewed_at = NOW(),
            reviewed_by = 'admin'
        WHERE id = %s
          AND status = 'pending'
        RETURNING id
    """, (
        clean(note, 1000) or "Rejected by admin",
        submission_id
    ))

    if not result:
        raise HTTPException(404, "Pending submission not found")

    return {
        "success": True,
        "submission_id": result["id"],
        "status": "rejected"
    }


# --------------------------------------------------
# ADMIN: VERIFY EXISTING PRICE
# --------------------------------------------------

@app.post("/api/admin/prices/{price_id}/verify")
def verify_price(
    price_id: int,
    x_admin_token: Optional[str] = Header(default=None)
):
    admin_auth(x_admin_token)

    result = one("""
        UPDATE prices
        SET
            source_type = 'Verified',
            in_store_confirmed = TRUE,
            is_live = TRUE,
            updated_at = NOW(),
            valid_until = NOW() + INTERVAL '24 hours'
        WHERE id = %s
          AND price > 0
        RETURNING id
    """, (price_id,))

    if not result:
        raise HTTPException(404, "Price not found")

    return {
        "success": True,
        "price_id": result["id"]
    }


# --------------------------------------------------
# ADMIN: REFRESH STATUS
# --------------------------------------------------

@app.post("/api/admin/refresh")
def refresh(
    x_admin_token: Optional[str] = Header(default=None)
):
    admin_auth(x_admin_token)

    return {
        "success": True,
        "message": (
            "No external data was fabricated. "
            "External source refresh requires a configured source feed."
        ),
        "timestamp": datetime.now(timezone.utc)
    }


# --------------------------------------------------
# PRICE HISTORY
# --------------------------------------------------

@app.get("/api/price-history")
def price_history(
    product_id: int,
    shop_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500)
):
    sql = """
        SELECT
            ph.*,
            s.shop_name,
            p.product_name
        FROM price_history ph
        JOIN shops s ON s.id = ph.shop_id
        JOIN products p ON p.id = ph.product_id
        WHERE ph.product_id = %s
    """

    args = [product_id]

    if shop_id is not None:
        sql += " AND ph.shop_id = %s"
        args.append(shop_id)

    sql += " ORDER BY ph.changed_at DESC LIMIT %s"
    args.append(limit)

    data = rows(sql, args)

    return {
        "count": len(data),
        "history": data
    }


# --------------------------------------------------
# FRONTEND
# --------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home():
    base = os.path.dirname(__file__)

    paths = [
        os.path.join(base, "templates", "index.html"),
        os.path.join(base, "index.html")
    ]

    for path in paths:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as file:
                return HTMLResponse(file.read())

    return HTMLResponse(
        "<h1>BezawadaDeals API running</h1>"
        "<a href='/docs'>API documentation</a>"
    )
