
import os
import re
import json
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI, Header, HTTPException, Query, Request as FastAPIRequest
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


APP_VERSION = "15.0.0"

DATABASE_URL = os.getenv("DATABASE_URL", "")
DATA_GOV_IN_API_KEY = os.getenv("DATA_GOV_IN_API_KEY", "")
SHOP_OWNER_TOKEN = os.getenv("SHOP_OWNER_TOKEN", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

AGMARKNET_RESOURCE = "9ef84268-d588-465a-a308-a864a43d0070"
AGMARKNET_URL = f"https://api.data.gov.in/resource/{AGMARKNET_RESOURCE}"
CONSUMER_AFFAIRS_URL = "https://fcainfoweb.nic.in/Reports/DB/DBprices.aspx"


CATEGORIES = [
    "Vegetables", "Fruits", "Rice & Rice Products", "Wheat & Atta",
    "Dals & Pulses", "Edible Oils", "Spices & Masalas", "Sugar & Jaggery",
    "Salt", "Dairy & Milk", "Eggs", "Chicken & Meat", "Fish & Seafood",
    "Bakery", "Packaged Foods", "Snacks", "Biscuits", "Beverages",
    "Tea & Coffee", "Dry Fruits & Nuts", "Baby Food", "Baby Care",
    "Personal Care", "Skin Care", "Hair Care", "Household Cleaning",
    "Laundry", "Home & Kitchen", "Pooja Items", "Stationery",
    "School Supplies", "Electronics", "Mobile Accessories", "Clothing",
    "Footwear", "Medicines (OTC)", "Pet Supplies", "Kitchen Essentials",
    "Water & Disposables", "Frozen Foods", "Ready to Cook",
    "Organic Products", "Millets", "Cereals", "Poultry Products",
    "Bakery Ingredients", "Garden Supplies", "Hardware",
    "Furniture & Home Decor", "Other"
]


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

def db():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is missing in Render environment variables"
        )

    url = DATABASE_URL.strip()

    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return psycopg.connect(
        url,
        row_factory=dict_row,
        connect_timeout=15
    )


def iso(value):
    return value.isoformat() if value else None


def init_db():
    """
    Existing six main tables are preserved.
    Only auxiliary government-feed tables are created if missing.
    """

    with db() as con:

        con.execute("""
            CREATE TABLE IF NOT EXISTS mandi_prices (
                id BIGSERIAL PRIMARY KEY,
                state TEXT,
                district TEXT,
                market TEXT,
                commodity TEXT,
                variety TEXT,
                grade TEXT,
                arrival_date TEXT,
                min_price NUMERIC(12,2),
                max_price NUMERIC(12,2),
                modal_price NUMERIC(12,2),
                source TEXT DEFAULT 'AGMARKNET / data.gov.in',
                fetched_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS government_retail_benchmarks (
                id BIGSERIAL PRIMARY KEY,
                commodity TEXT NOT NULL,
                unit TEXT,
                price NUMERIC(12,2),
                report_date TEXT,
                geographic_scope TEXT DEFAULT 'All India average',
                source TEXT DEFAULT 'Department of Consumer Affairs',
                fetched_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS source_sync (
                source_key TEXT PRIMARY KEY,
                last_attempt TIMESTAMPTZ,
                last_success TIMESTAMPTZ,
                status TEXT,
                message TEXT DEFAULT ''
            )
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS mandi_lookup_idx
            ON mandi_prices(district, market, commodity, arrival_date)
        """)

        con.execute("""
            CREATE INDEX IF NOT EXISTS govt_benchmark_lookup_idx
            ON government_retail_benchmarks(commodity, report_date)
        """)

        con.commit()


# ---------------------------------------------------------
# SOURCE SYNC STATUS
# ---------------------------------------------------------

def mark_sync(key, success, message):

    try:
        with db() as con:

            con.execute("""
                INSERT INTO source_sync (
                    source_key,
                    last_attempt,
                    last_success,
                    status,
                    message
                )
                VALUES (
                    %s,
                    NOW(),
                    CASE WHEN %s THEN NOW() ELSE NULL END,
                    %s,
                    %s
                )
                ON CONFLICT(source_key)
                DO UPDATE SET
                    last_attempt = NOW(),
                    last_success = CASE
                        WHEN %s THEN NOW()
                        ELSE source_sync.last_success
                    END,
                    status = EXCLUDED.status,
                    message = EXCLUDED.message
            """, (
                key,
                success,
                "OK" if success else "ERROR",
                str(message)[:500],
                success
            ))

            con.commit()

    except Exception as exc:
        print(f"SYNC_STATUS_ERROR {key}: {exc}")


# ---------------------------------------------------------
# AGMARKNET WHOLESALE DATA
# ---------------------------------------------------------

def fetch_agmarknet():

    if not DATA_GOV_IN_API_KEY:
        raise RuntimeError(
            "DATA_GOV_IN_API_KEY is not configured"
        )

    total = 0

    with db() as con:

        for district in ("NTR", "Krishna"):

            params = {
                "api-key": DATA_GOV_IN_API_KEY,
                "format": "json",
                "limit": 1000,
                "filters[state]": "Andhra Pradesh",
                "filters[district]": district
            }

            request = Request(
                AGMARKNET_URL + "?" + urlencode(params),
                headers={
                    "User-Agent": "BezawadaDeals/15.0"
                }
            )

            with urlopen(request, timeout=35) as response:
                payload = json.loads(
                    response.read().decode("utf-8")
                )

            for row in payload.get("records", []):

                dist = str(
                    row.get("district") or district
                ).strip()

                if dist.casefold() not in ("ntr", "krishna"):
                    continue

                market = row.get("market")
                commodity = row.get("commodity")
                variety = row.get("variety")
                arrival_date = row.get("arrival_date")
                grade = row.get("grade")

                min_price = row.get("min_price") or None
                max_price = row.get("max_price") or None
                modal_price = row.get("modal_price") or None

                # Refresh matching record instead of adding duplicates.
                con.execute("""
                    DELETE FROM mandi_prices
                    WHERE LOWER(COALESCE(district, '')) = LOWER(%s)
                      AND COALESCE(market, '') = COALESCE(%s, '')
                      AND COALESCE(commodity, '') = COALESCE(%s, '')
                      AND COALESCE(variety, '') = COALESCE(%s, '')
                      AND COALESCE(grade, '') = COALESCE(%s, '')
                      AND COALESCE(arrival_date, '') = COALESCE(%s, '')
                """, (
                    dist,
                    market,
                    commodity,
                    variety,
                    grade,
                    arrival_date
                ))

                con.execute("""
                    INSERT INTO mandi_prices (
                        state,
                        district,
                        market,
                        commodity,
                        variety,
                        grade,
                        arrival_date,
                        min_price,
                        max_price,
                        modal_price,
                        fetched_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, NOW()
                    )
                """, (
                    row.get("state"),
                    dist,
                    market,
                    commodity,
                    variety,
                    grade,
                    arrival_date,
                    min_price,
                    max_price,
                    modal_price
                ))

                total += 1

        con.commit()

    mark_sync(
        "agmarknet",
        True,
        f"Processed {total} NTR/Krishna wholesale records"
    )

    return total


# ---------------------------------------------------------
# CONSUMER AFFAIRS NATIONAL RETAIL BENCHMARK
# ---------------------------------------------------------

def fetch_consumer_affairs():

    from html.parser import HTMLParser

    class Parser(HTMLParser):

        def __init__(self):
            super().__init__()
            self.rows = []
            self.row = None
            self.cell = None

        def handle_starttag(self, tag, attrs):

            if tag == "tr":
                self.row = []

            elif tag in ("td", "th") and self.row is not None:
                self.cell = []

        def handle_data(self, data):

            if self.cell is not None:
                value = data.strip()

                if value:
                    self.cell.append(value)

        def handle_endtag(self, tag):

            if (
                tag in ("td", "th")
                and self.cell is not None
                and self.row is not None
            ):
                self.row.append(" ".join(self.cell))
                self.cell = None

            elif tag == "tr" and self.row is not None:

                if self.row:
                    self.rows.append(self.row)

                self.row = None

    request = Request(
        CONSUMER_AFFAIRS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 BezawadaDeals/15"
        }
    )

    with urlopen(request, timeout=35) as response:
        html = response.read().decode("utf-8", "ignore")

    parser = Parser()
    parser.feed(html)

    date_match = re.search(
        r"(\d{2}/\d{2}/\d{4})",
        html
    )

    report_date = (
        date_match.group(1)
        if date_match
        else datetime.now().strftime("%d/%m/%Y")
    )

    saved = 0

    with db() as con:

        for row in parser.rows:

            if len(row) < 3:
                continue

            commodity = row[0].strip()

            if commodity.lower() in (
                "",
                "commodity",
                "commodities"
            ):
                continue

            unit = row[1].strip()

            raw = re.sub(
                r"[^0-9.]",
                "",
                row[2].replace(",", "")
            )

            try:
                price = float(raw)
            except (ValueError, TypeError):
                continue

            if price <= 0:
                continue

            con.execute("""
                DELETE FROM government_retail_benchmarks
                WHERE LOWER(commodity) = LOWER(%s)
                  AND report_date = %s
                  AND geographic_scope = 'All India average'
            """, (
                commodity,
                report_date
            ))

            con.execute("""
                INSERT INTO government_retail_benchmarks (
                    commodity,
                    unit,
                    price,
                    report_date,
                    geographic_scope,
                    source,
                    fetched_at
                )
                VALUES (
                    %s, %s, %s, %s,
                    'All India average',
                    'Department of Consumer Affairs',
                    NOW()
                )
            """, (
                commodity,
                unit,
                price,
                report_date
            ))

            saved += 1

        con.commit()

    if not saved:
        raise RuntimeError(
            "Consumer Affairs page did not yield readable price rows"
        )

    mark_sync(
        "consumer_affairs",
        True,
        f"Saved {saved} All-India benchmark rows for {report_date}"
    )

    return saved


# ---------------------------------------------------------
# AUTOMATIC GOVERNMENT DATA SYNC
# ---------------------------------------------------------

async def sync_loop():

    while True:

        for key, fn in (
            ("agmarknet", fetch_agmarknet),
            ("consumer_affairs", fetch_consumer_affairs)
        ):

            try:
                await asyncio.to_thread(fn)

            except Exception as exc:
                mark_sync(key, False, str(exc))
                print(f"SYNC_ERROR {key}: {exc}")

        await asyncio.sleep(3600)


# ---------------------------------------------------------
# FASTAPI LIFESPAN
# ---------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):

    await asyncio.to_thread(init_db)

    task = asyncio.create_task(sync_loop())

    try:
        yield

    finally:
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="BezawadaDeals",
    version=APP_VERSION,
    lifespan=lifespan
)

templates = Jinja2Templates(directory="templates")


# ---------------------------------------------------------
# HOME
# ---------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def home(request: FastAPIRequest):

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "version": APP_VERSION
        }
    )


# ---------------------------------------------------------
# HEALTH
# ---------------------------------------------------------

@app.get("/health")
def health():

    try:

        with db() as con:
            con.execute("SELECT 1")

        return {
            "ok": True,
            "version": APP_VERSION,
            "database": "connected"
        }

    except Exception as exc:

        return {
            "ok": False,
            "version": APP_VERSION,
            "database_error": str(exc)
        }


# ---------------------------------------------------------
# CATEGORIES
# ---------------------------------------------------------

@app.get("/api/categories")
def categories():

    return {
        "count": len(CATEGORIES),
        "categories": CATEGORIES
    }


# ---------------------------------------------------------
# WARDS
# ---------------------------------------------------------

@app.get("/api/wards")
def wards():

    with db() as con:

        rows = con.execute("""
            SELECT
                id,
                ward_number,
                COALESCE(
                    NULLIF(BTRIM(ward_name), ''),
                    NULLIF(BTRIM(area_name), ''),
                    'Ward ' || ward_number::text
                ) AS ward_name,
                area_name,
                boundary_description
            FROM wards
            WHERE is_active IS DISTINCT FROM FALSE
            ORDER BY ward_number
        """).fetchall()

    return {
        "count": len(rows),
        "wards": rows
    }


# ---------------------------------------------------------
# LOCALITIES
# ---------------------------------------------------------

@app.get("/api/localities")
def localities(ward_number: Optional[int] = None):

    sql = """
        SELECT
            l.id,
            l.locality_name AS name,
            l.locality_name_te,
            l.is_verified AS verified,
            l.ward_id,
            w.ward_number
        FROM localities l
        LEFT JOIN wards w ON w.id = l.ward_id
    """

    args = []

    if ward_number is not None:
        sql += " WHERE w.ward_number = %s"
        args.append(ward_number)

    sql += " ORDER BY l.locality_name"

    with db() as con:
        rows = con.execute(sql, args).fetchall()

    return {
        "count": len(rows),
        "localities": rows
    }


# ---------------------------------------------------------
# SHOPS
# Verified and unverified shops are both visible.
# ---------------------------------------------------------

@app.get("/api/shops")
def shops(
    area: Optional[str] = None,
    ward_number: Optional[int] = None,
    shop_type: Optional[str] = None
):

    sql = """
        SELECT
            s.id,
            s.shop_name AS name,
            s.category,
            s.address,
            s.phone,
            s.ward_id,
            w.ward_number,
            COALESCE(
                NULLIF(BTRIM(s.locality), ''),
                l.locality_name,
                w.area_name
            ) AS area,
            s.is_verified,
            s.data_connection_status
        FROM shops s
        LEFT JOIN wards w ON w.id = s.ward_id
        LEFT JOIN localities l ON l.id = s.locality_id
        WHERE TRUE
    """

    args = []

    if area:
        sql += """
            AND LOWER(
                COALESCE(s.locality, l.locality_name, '')
            ) = LOWER(%s)
        """
        args.append(area)

    if ward_number is not None:
        sql += " AND w.ward_number = %s"
        args.append(ward_number)

    if shop_type:
        sql += """
            AND LOWER(COALESCE(s.category, '')) = LOWER(%s)
        """
        args.append(shop_type)

    sql += " ORDER BY area, s.shop_name"

    with db() as con:
        rows = con.execute(sql, args).fetchall()

    return {
        "count": len(rows),
        "shops": rows
    }


# ---------------------------------------------------------
# UNIFIED LOCAL PRICE TRACKER
# ---------------------------------------------------------

@app.get("/api/unified-price-tracker")
@app.get("/api/price-tracker")
def unified_price_tracker(
    category: Optional[str] = None,
    area: Optional[str] = None,
    ward_number: Optional[int] = None,
    product: Optional[str] = None,
    source_type: Optional[str] = None,
    sort: str = "low",
    include_expired: bool = False
):

    sql = """
        SELECT
            p.id AS price_id,
            pr.product_name AS product,
            pr.category,
            pr.unit,
            p.price,
            s.shop_name AS shop,
            s.category AS shop_type,
            s.address,
            w.ward_number,
            COALESCE(
                NULLIF(BTRIM(s.locality), ''),
                l.locality_name,
                w.area_name
            ) AS area,
            p.source_name AS source,
            p.source_type,
            CASE
                WHEN p.valid_until IS NOT NULL
                     AND p.valid_until < NOW()
                    THEN 'Expired'
                WHEN p.source_type = 'Verified'
                    THEN 'Verified'
                WHEN p.in_store_confirmed
                    THEN 'Owner-confirmed'
                ELSE 'Estimated'
            END AS status,
            p.updated_at AS observed_at,
            p.valid_until AS expires_at
        FROM prices p
        JOIN products pr ON pr.id = p.product_id
        JOIN shops s ON s.id = p.shop_id
        LEFT JOIN wards w ON w.id = s.ward_id
        LEFT JOIN localities l ON l.id = s.locality_id
        WHERE p.is_live = TRUE
          AND p.in_store_confirmed = TRUE
    """

    args = []

    if category:
        sql += " AND LOWER(pr.category) = LOWER(%s)"
        args.append(category)

    if area:
        sql += """
            AND LOWER(
                COALESCE(s.locality, l.locality_name, '')
            ) = LOWER(%s)
        """
        args.append(area)

    if ward_number is not None:
        sql += " AND w.ward_number = %s"
        args.append(ward_number)

    if product:
        sql += " AND pr.product_name ILIKE %s"
        args.append(f"%{product}%")

    # Shop type filter must use shops.category.
    if source_type:
        sql += """
            AND LOWER(COALESCE(s.category, '')) = LOWER(%s)
        """
        args.append(source_type)

    if not include_expired:
        sql += """
            AND (
                p.valid_until IS NULL
                OR p.valid_until >= NOW()
            )
        """

    sql += " ORDER BY p.updated_at DESC"

    with db() as con:
        rows = con.execute(sql, args).fetchall()

    for row in rows:

        row["price"] = float(row["price"])

        row["observed_at"] = iso(
            row["observed_at"]
        )

        row["expires_at"] = iso(
            row["expires_at"]
        )

    rows.sort(
        key=lambda x: x["price"],
        reverse=(sort.lower() == "high")
    )

    return {
        "count": len(rows),
        "prices": rows,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }


# ---------------------------------------------------------
# WHOLESALE MANDI PRICES
# ---------------------------------------------------------

@app.get("/api/market-prices")
def market_prices(
    commodity: Optional[str] = None,
    limit: int = Query(300, ge=1, le=1000)
):

    sql = "SELECT * FROM mandi_prices"

    args = []

    if commodity:
        sql += " WHERE commodity ILIKE %s"
        args.append(f"%{commodity}%")

    sql += " ORDER BY arrival_date DESC, market LIMIT %s"
    args.append(limit)

    with db() as con:
        rows = con.execute(sql, args).fetchall()

    for row in rows:

        for key in (
            "min_price",
            "max_price",
            "modal_price"
        ):
            if row[key] is not None:
                row[key] = float(row[key])

        row["fetched_at"] = iso(row["fetched_at"])

    return {
        "count": len(rows),
        "prices": rows,
        "unit": "₹ per quintal",
        "source": "AGMARKNET / data.gov.in wholesale mandi data"
    }


# ---------------------------------------------------------
# GOVERNMENT RETAIL BENCHMARKS
# ---------------------------------------------------------

@app.get("/api/official-retail-prices")
def official_retail_prices():

    with db() as con:

        rows = con.execute("""
            SELECT
                commodity,
                unit,
                price,
                report_date,
                geographic_scope,
                source,
                fetched_at
            FROM government_retail_benchmarks
            ORDER BY commodity
        """).fetchall()

    for row in rows:

        row["price"] = (
            float(row["price"])
            if row["price"] is not None
            else None
        )

        row["fetched_at"] = iso(row["fetched_at"])

    return {
        "count": len(rows),
        "prices": rows,
        "note": (
            "All-India government benchmark only; "
            "not Vijayawada-specific shop prices."
        )
    }


# ---------------------------------------------------------
# OFFERS
# ---------------------------------------------------------

@app.get("/api/offers")
def offers(area: Optional[str] = None):

    sql = """
        SELECT
            o.id,
            o.title,
            o.description,
            o.discount_text,
            o.valid_from,
            o.valid_until,
            s.shop_name AS shop,
            COALESCE(
                NULLIF(BTRIM(s.locality), ''),
                l.locality_name,
                w.area_name
            ) AS area,
            w.ward_number
        FROM offers o
        LEFT JOIN shops s ON s.id = o.shop_id
        LEFT JOIN wards w ON w.id = s.ward_id
        LEFT JOIN localities l ON l.id = s.locality_id
        WHERE o.is_active = TRUE
          AND (
              o.valid_until IS NULL
              OR o.valid_until >= NOW()
          )
    """

    args = []

    if area:
        sql += """
            AND LOWER(
                COALESCE(s.locality, l.locality_name, '')
            ) = LOWER(%s)
        """
        args.append(area)

    sql += " ORDER BY o.updated_at DESC"

    with db() as con:
        rows = con.execute(sql, args).fetchall()

    for row in rows:

        row["valid_from"] = iso(row["valid_from"])
        row["valid_until"] = iso(row["valid_until"])

    return {
        "count": len(rows),
        "offers": rows
    }


# ---------------------------------------------------------
# DATABASE STATUS
# ---------------------------------------------------------

@app.get("/api/status")
def status():

    tables = (
        "wards",
        "localities",
        "shops",
        "products",
        "prices",
        "offers",
        "mandi_prices",
        "government_retail_benchmarks"
    )

    with db() as con:

        counts = {}

        for table in tables:

            result = con.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()

            counts[table] = result["n"]

        sync = con.execute("""
            SELECT
                source_key,
                last_attempt,
                last_success,
                status,
                message
            FROM source_sync
            ORDER BY source_key
        """).fetchall()

    for row in sync:

        row["last_attempt"] = iso(row["last_attempt"])
        row["last_success"] = iso(row["last_success"])

    return {
        "version": APP_VERSION,
        "counts": counts,
        "sources": sync,
        "page_refresh_minutes": 5
    }


# ---------------------------------------------------------
# SHOP OWNER PRICE UPDATE
# ---------------------------------------------------------

@app.post("/api/shop/price-update")
def shop_price_update(
    payload: dict,
    x_shop_token: str = Header(default="")
):

    if not SHOP_OWNER_TOKEN or x_shop_token != SHOP_OWNER_TOKEN:
        raise HTTPException(
            401,
            "Invalid SHOP_OWNER_TOKEN"
        )

    required = (
        "shop_name",
        "shop_type",
        "area",
        "ward_number",
        "product",
        "category",
        "unit",
        "price",
        "confirmed"
    )

    missing = [
        key
        for key in required
        if payload.get(key) in (None, "")
    ]

    if missing:
        raise HTTPException(
            400,
            "Missing: " + ", ".join(missing)
        )

    if payload.get("confirmed") is not True:
        raise HTTPException(
            400,
            "Confirm that this is the current selling price"
        )

    try:
        price = float(payload["price"])
        ward_number = int(payload["ward_number"])

    except (TypeError, ValueError):
        raise HTTPException(
            400,
            "Invalid price or ward number"
        )

    if price <= 0 or not 1 <= ward_number <= 86:
        raise HTTPException(
            400,
            "Price must be positive and ward must be 1–86"
        )

    category = str(payload["category"]).strip()

    if category not in CATEGORIES:
        raise HTTPException(
            400,
            "Invalid category"
        )

    shop_name = str(payload["shop_name"]).strip()
    shop_type = str(payload["shop_type"]).strip()
    area = str(payload["area"]).strip()
    product_name = str(payload["product"]).strip()
    unit = str(payload["unit"]).strip()

    if not all((
        shop_name,
        area,
        product_name,
        unit,
        shop_type
    )):
        raise HTTPException(
            400,
            "Shop, area, product and unit are required"
        )

    with db() as con:

        # Serialize same-name/same-ward submissions.
        con.execute(
            """
            SELECT pg_advisory_xact_lock(
                hashtextextended(
                    LOWER(BTRIM(%s)) || ':' || %s::text,
                    0
                )
            )
            """,
            (shop_name, ward_number)
        )

        ward = con.execute("""
            SELECT id
            FROM wards
            WHERE ward_number = %s
              AND is_active IS DISTINCT FROM FALSE
        """, (ward_number,)).fetchone()

        if not ward:
            raise HTTPException(
                400,
                "Ward not found"
            )

        locality = con.execute("""
            SELECT id
            FROM localities
            WHERE LOWER(BTRIM(locality_name)) = LOWER(BTRIM(%s))
              AND ward_id = %s
            ORDER BY id
            LIMIT 1
        """, (
            area,
            ward["id"]
        )).fetchone()

        if not locality:

            locality = con.execute("""
                INSERT INTO localities (
                    ward_id,
                    locality_name,
                    locality_name_te,
                    is_verified
                )
                VALUES (%s, %s, NULL, FALSE)
                RETURNING id
            """, (
                ward["id"],
                area
            )).fetchone()

        # Find an existing shop in the same ward first.
        # If ward_id is NULL, a matching directory record can be reused.
        shop = con.execute("""
            SELECT id
            FROM shops
            WHERE LOWER(BTRIM(shop_name)) = LOWER(BTRIM(%s))
              AND (
                    ward_id = %s
                    OR ward_id IS NULL
              )
            ORDER BY
                CASE
                    WHEN ward_id = %s THEN 0
                    ELSE 1
                END,
                id
            LIMIT 1
        """, (
            shop_name,
            ward["id"],
            ward["id"]
        )).fetchone()

        address = str(
            payload.get("address") or ""
        ).strip()

        phone = str(
            payload.get("phone") or ""
        ).strip()

        if shop:

            shop_id = shop["id"]

            con.execute("""
                UPDATE shops
                SET
                    category = %s,
                    ward_id = %s,
                    locality_id = %s,
                    locality = %s,
                    address = CASE
                        WHEN %s <> '' THEN %s
                        ELSE address
                    END,
                    phone = CASE
                        WHEN %s <> '' THEN %s
                        ELSE phone
                    END,
                    data_connection_status = 'Owner update'
                WHERE id = %s
            """, (
                shop_type,
                ward["id"],
                locality["id"],
                area,
                address,
                address,
                phone,
                phone,
                shop_id
            ))

        else:

            shop_id = con.execute("""
                INSERT INTO shops (
                    shop_name,
                    category,
                    ward_id,
                    locality_id,
                    locality,
                    address,
                    phone,
                    is_verified,
                    data_connection_status
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    FALSE,
                    'Owner update'
                )
                RETURNING id
            """, (
                shop_name,
                shop_type,
                ward["id"],
                locality["id"],
                area,
                address,
                phone
            )).fetchone()["id"]

        # Reuse an existing matching product.
        prod = con.execute("""
            SELECT id
            FROM products
            WHERE LOWER(BTRIM(product_name)) = LOWER(BTRIM(%s))
              AND LOWER(BTRIM(category)) = LOWER(BTRIM(%s))
              AND LOWER(BTRIM(unit)) = LOWER(BTRIM(%s))
            ORDER BY id
            LIMIT 1
        """, (
            product_name,
            category,
            unit
        )).fetchone()

        if not prod:

            prod = con.execute("""
                INSERT INTO products (
                    product_name,
                    category,
                    unit
                )
                VALUES (%s, %s, %s)
                RETURNING id
            """, (
                product_name,
                category,
                unit
            )).fetchone()

        # Save new price record.
        con.execute("""
            INSERT INTO prices (
                shop_id,
                product_id,
                price,
                price_type,
                source_name,
                source_type,
                is_live,
                in_store_confirmed,
                updated_at,
                valid_until
            )
            VALUES (
                %s,
                %s,
                %s,
                'Retail',
                'Shop-owner submitted',
                'Owner-confirmed',
                TRUE,
                TRUE,
                NOW(),
                NOW() + INTERVAL '24 hours'
            )
        """, (
            shop_id,
            prod["id"],
            price
        ))

        con.commit()

    return {
        "ok": True,
        "shop_id": shop_id,
        "product_id": prod["id"],
        "message": (
            "Owner-confirmed price saved for 24 hours; "
            "awaiting independent verification."
        ),
        "price": price
    }


# ---------------------------------------------------------
# ADMIN PRICE VERIFICATION
# ---------------------------------------------------------

@app.post("/api/admin/prices/{price_id}/verify")
def verify_price(
    price_id: int,
    x_admin_token: str = Header(default="")
):

    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(
            401,
            "Invalid ADMIN_TOKEN"
        )

    with db() as con:

        cur = con.execute("""
            UPDATE prices
            SET source_type = 'Verified'
            WHERE id = %s
              AND in_store_confirmed = TRUE
        """, (price_id,))

        if cur.rowcount == 0:
            raise HTTPException(
                404,
                "Price not found"
            )

        con.commit()

    return {
        "ok": True,
        "price_id": price_id,
        "status": "Verified"
    }


# ---------------------------------------------------------
# ADMIN MANUAL GOVERNMENT DATA REFRESH
# ---------------------------------------------------------

@app.post("/api/admin/refresh")
async def admin_refresh(
    x_admin_token: str = Header(default="")
):

    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(
            401,
            "Invalid ADMIN_TOKEN"
        )

    results = {}

    for key, fn in (
        ("agmarknet", fetch_agmarknet),
        ("consumer_affairs", fetch_consumer_affairs)
    ):

        try:

            saved = await asyncio.to_thread(fn)

            results[key] = {
                "ok": True,
                "saved": saved
            }

        except Exception as exc:

            mark_sync(
                key,
                False,
                str(exc)
            )

            results[key] = {
                "ok": False,
                "error": str(exc)
            }

    return results


# ---------------------------------------------------------
# ADMIN ADD OFFER
# ---------------------------------------------------------

@app.post("/api/admin/offers")
def admin_add_offer(
    payload: dict,
    x_admin_token: str = Header(default="")
):

    if not ADMIN_TOKEN or x_admin_token != ADMIN_TOKEN:
        raise HTTPException(
            401,
            "Invalid ADMIN_TOKEN"
        )

    title = str(
        payload.get("title") or ""
    ).strip()

    if not title:
        raise HTTPException(
            400,
            "Offer title is required"
        )

    shop_id = payload.get("shop_id")

    try:
        shop_id = (
            int(shop_id)
            if shop_id not in (None, "")
            else None
        )

    except (TypeError, ValueError):
        raise HTTPException(
            400,
            "shop_id must be numeric"
        )

    with db() as con:

        if shop_id is not None:

            shop = con.execute("""
                SELECT id
                FROM shops
                WHERE id = %s
            """, (shop_id,)).fetchone()

            if not shop:
                raise HTTPException(
                    404,
                    "Shop not found"
                )

        valid_until = payload.get("valid_until") or None

        row = con.execute("""
            INSERT INTO offers (
                shop_id,
                title,
                description,
                discount_text,
                valid_from,
                valid_until,
                is_active,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s,
                NOW(), %s, TRUE, NOW()
            )
            RETURNING id
        """, (
            shop_id,
            title,
            str(payload.get("description") or ""),
            str(payload.get("discount_text") or ""),
            valid_until
        )).fetchone()

        con.commit()

    return {
        "ok": True,
        "offer_id": row["id"]
    }


# ---------------------------------------------------------
# OFFICIAL DATA SOURCES
# ---------------------------------------------------------

@app.get("/api/official-sources")
def official_sources():

    return {
        "sources": [

            {
                "name": "AGMARKNET / data.gov.in",
                "type": "Wholesale mandi prices",
                "automatic": bool(DATA_GOV_IN_API_KEY),
                "scope": "NTR / Krishna records returned by the API",
                "note": (
                    "Wholesale mandi prices; usually ₹ per quintal, "
                    "not retail shop prices."
                ),
                "url": AGMARKNET_URL
            },

            {
                "name": "Department of Consumer Affairs",
                "type": "Daily retail benchmark",
                "automatic": True,
                "scope": "All-India average",
                "note": (
                    "National benchmark only; "
                    "not Vijayawada-specific."
                ),
                "url": CONSUMER_AFFAIRS_URL
            },

            {
                "name": "Local supermarkets and shops",
                "type": "Local retail",
                "automatic": False,
                "scope": "Shop-specific",
                "note": (
                    "Requires owner-confirmed or "
                    "independently verified submissions."
                )
            },

            {
                "name": "Rythu Bazaar",
                "type": "Local retail",
                "automatic": False,
                "scope": "Bazaar-specific",
                "note": (
                    "No verified public automatic feed configured; "
                    "do not substitute mandi prices."
                )
            }
        ]
    }
