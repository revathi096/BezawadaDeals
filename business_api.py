import os
import hmac
import hashlib
import secrets
import base64
import json
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Header, HTTPException, Query


router = APIRouter(prefix="/api/v2", tags=["Business and Price APIs"])

DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
SHOP_OWNER_TOKEN = os.getenv("SHOP_OWNER_TOKEN", "")


# --------------------------------------------------
# DATABASE
# --------------------------------------------------

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    url = DATABASE_URL.strip()
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return psycopg.connect(
        url,
        row_factory=dict_row,
        connect_timeout=15
    )


def clean(value):
    return str(value or "").strip()


def admin_required(token):
    if not ADMIN_TOKEN or not hmac.compare_digest(
        str(token or ""), ADMIN_TOKEN
    ):
        raise HTTPException(401, "Invalid admin token")


# --------------------------------------------------
# PASSWORD HASHING
# --------------------------------------------------

def hash_password(password):
    if len(password) < 8:
        raise HTTPException(400, "Password must contain at least 8 characters")

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt, 310000
    )

    return (
        "pbkdf2_sha256$310000$"
        + base64.urlsafe_b64encode(salt).decode()
        + "$"
        + base64.urlsafe_b64encode(digest).decode()
    )


def verify_password(password, stored):
    try:
        algorithm, iterations, salt_text, hash_text = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False

        salt = base64.urlsafe_b64decode(salt_text)
        expected = base64.urlsafe_b64decode(hash_text)

        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            salt,
            int(iterations)
        )

        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


# --------------------------------------------------
# OWNER TOKEN
# --------------------------------------------------

def token_secret():
    secret = SHOP_OWNER_TOKEN or ADMIN_TOKEN
    if not secret:
        raise HTTPException(
            503,
            "Set SHOP_OWNER_TOKEN in Render environment variables"
        )
    return secret.encode()


def create_owner_token(owner_id):
    payload = {
        "owner_id": int(owner_id),
        "expires": int(
            (datetime.now(timezone.utc) + timedelta(days=30)).timestamp()
        )
    }

    raw = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")

    signature = hmac.new(
        token_secret(), raw.encode(), hashlib.sha256
    ).hexdigest()

    return raw + "." + signature


def get_owner_id(authorization):
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer owner token required")

    token = authorization[7:].strip()

    try:
        raw, signature = token.rsplit(".", 1)

        expected = hmac.new(
            token_secret(), raw.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid signature")

        payload = json.loads(
            base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        )

        if int(payload["expires"]) < int(datetime.now(timezone.utc).timestamp()):
            raise HTTPException(401, "Owner token expired")

        return int(payload["owner_id"])

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(401, "Invalid owner token")


def owner_required(authorization):
    owner_id = get_owner_id(authorization)

    with get_db() as con:
        owner = con.execute("""
            SELECT id, shop_id, owner_name, phone, email, is_verified, is_active
            FROM shop_owners
            WHERE id = %s AND is_active = TRUE
        """, (owner_id,)).fetchone()

    if not owner:
        raise HTTPException(401, "Owner account not found or inactive")

    return owner


# --------------------------------------------------
# SHOP REGISTRATION
# --------------------------------------------------

@router.post("/shops/register")
def register_shop(payload: dict):
    shop_name = clean(payload.get("shop_name"))
    category = clean(payload.get("category"))
    address = clean(payload.get("address"))
    phone = clean(payload.get("phone"))
    owner_name = clean(payload.get("owner_name"))
    password = clean(payload.get("password"))
    email = clean(payload.get("email"))
    locality_name = clean(payload.get("locality"))
    ward_number = payload.get("ward_number")

    if not all([shop_name, category, phone, owner_name, password]):
        raise HTTPException(
            400,
            "shop_name, category, phone, owner_name and password are required"
        )

    if not phone.isdigit() or len(phone) < 10:
        raise HTTPException(400, "Enter a valid phone number")

    password_hash = hash_password(password)

    try:
        ward_number = int(ward_number)
    except (TypeError, ValueError):
        raise HTTPException(400, "Valid ward_number is required")

    with get_db() as con:
        category_row = con.execute("""
            SELECT id, category_name
            FROM business_categories
            WHERE LOWER(category_name) = LOWER(%s)
              AND is_active = TRUE
        """, (category,)).fetchone()

        if not category_row:
            raise HTTPException(400, "Select a valid business category")

        ward = con.execute("""
            SELECT id
            FROM wards
            WHERE ward_number = %s
              AND is_active IS DISTINCT FROM FALSE
        """, (ward_number,)).fetchone()

        if not ward:
            raise HTTPException(400, "Ward not found")

        locality_id = None
        if locality_name:
            locality = con.execute("""
                SELECT id
                FROM localities
                WHERE ward_id = %s
                  AND LOWER(BTRIM(locality_name)) = LOWER(BTRIM(%s))
                LIMIT 1
            """, (ward["id"], locality_name)).fetchone()

            if not locality:
                raise HTTPException(
                    400,
                    "Locality does not belong to the selected ward"
                )
            locality_id = locality["id"]

        existing = con.execute("""
            SELECT id FROM shop_owners
            WHERE phone = %s
        """, (phone,)).fetchone()

        if existing:
            raise HTTPException(409, "Phone number is already registered")

        shop = con.execute("""
            INSERT INTO shops (
                shop_name, category, category_id, ward_id,
                locality_id, locality, address, phone,
                is_verified, is_active, data_connection_status
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                    FALSE, TRUE, 'Registration pending')
            RETURNING id
        """, (
            shop_name,
            category_row["category_name"],
            category_row["id"],
            ward["id"],
            locality_id,
            locality_name or None,
            address or None,
            phone
        )).fetchone()

        owner = con.execute("""
            INSERT INTO shop_owners (
                shop_id, owner_name, phone, email,
                password_hash, is_verified, is_active
            )
            VALUES (%s, %s, %s, %s, %s, FALSE, TRUE)
            RETURNING id
        """, (
            shop["id"],
            owner_name,
            phone,
            email or None,
            password_hash
        )).fetchone()

        con.execute("""
            INSERT INTO shop_update_logs (
                shop_id, action_type, description, submitted_by
            )
            VALUES (%s, 'SHOP_REGISTERED',
                    'New shop registration awaiting verification', %s)
        """, (shop["id"], owner_name))

        con.commit()

    return {
        "ok": True,
        "shop_id": shop["id"],
        "owner_id": owner["id"],
        "message": "Registration submitted. Admin verification is pending."
    }


# --------------------------------------------------
# OWNER LOGIN
# --------------------------------------------------

@router.post("/owners/login")
def owner_login(payload: dict):
    phone = clean(payload.get("phone"))
    password = clean(payload.get("password"))

    if not phone or not password:
        raise HTTPException(400, "Phone and password are required")

    with get_db() as con:
        owner = con.execute("""
            SELECT id, shop_id, owner_name, phone, email,
                   password_hash, is_verified, is_active
            FROM shop_owners
            WHERE phone = %s
        """, (phone,)).fetchone()

    if not owner or not owner["is_active"]:
        raise HTTPException(401, "Invalid login details")

    if not owner["password_hash"] or not verify_password(
        password, owner["password_hash"]
    ):
        raise HTTPException(401, "Invalid login details")

    return {
        "ok": True,
        "access_token": create_owner_token(owner["id"]),
        "token_type": "bearer",
        "owner": {
            "id": owner["id"],
            "shop_id": owner["shop_id"],
            "owner_name": owner["owner_name"],
            "phone": owner["phone"],
            "is_verified": owner["is_verified"]
        }
    }


@router.get("/owners/me")
def owner_profile(authorization: str = Header(default="")):
    owner = owner_required(authorization)

    with get_db() as con:
        shop = con.execute("""
            SELECT id, shop_name, category, address, locality,
                   ward_id, is_verified, data_connection_status
            FROM shops
            WHERE id = %s
        """, (owner["shop_id"],)).fetchone()

    return {"owner": owner, "shop": shop}


# --------------------------------------------------
# PRODUCT SEARCH / CREATE
# --------------------------------------------------

@router.get("/products")
def search_products(
    search: str = "",
    category: str = "",
    limit: int = Query(100, ge=1, le=500)
):
    sql = """
        SELECT id, product_name, category, unit, description
        FROM products
        WHERE TRUE
    """
    args = []

    if search:
        sql += " AND product_name ILIKE %s"
        args.append("%" + search + "%")

    if category:
        sql += " AND category ILIKE %s"
        args.append("%" + category + "%")

    sql += " ORDER BY product_name LIMIT %s"
    args.append(limit)

    with get_db() as con:
        rows = con.execute(sql, args).fetchall()

    return {"count": len(rows), "products": rows}


# --------------------------------------------------
# OWNER PRICE SUBMISSION
# --------------------------------------------------

@router.post("/owners/prices/submit")
def submit_price(
    payload: dict,
    authorization: str = Header(default="")
):
    owner = owner_required(authorization)

    try:
        product_id = int(payload.get("product_id"))
        price = float(payload.get("price"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Valid product_id and price are required")

    unit = clean(payload.get("unit"))
    evidence_url = clean(payload.get("evidence_url"))
    submitted_by = clean(payload.get("submitted_by")) or owner["owner_name"]

    if price <= 0 or price > 10000000:
        raise HTTPException(400, "Price must be between 0 and 10000000")

    if not unit:
        raise HTTPException(400, "Unit is required")

    with get_db() as con:
        product = con.execute("""
            SELECT id, unit FROM products WHERE id = %s
        """, (product_id,)).fetchone()

        if not product:
            raise HTTPException(404, "Product not found")

        if product["unit"] and product["unit"].lower() != unit.lower():
            raise HTTPException(400, "Unit does not match product unit")

        shop = con.execute("""
            SELECT id FROM shops WHERE id = %s AND is_active = TRUE
        """, (owner["shop_id"],)).fetchone()

        if not shop:
            raise HTTPException(404, "Shop not found or inactive")

        submission = con.execute("""
            INSERT INTO price_submissions (
                shop_id, product_id, submitted_price, unit,
                submitted_by, evidence_url, status, submitted_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'pending', NOW())
            RETURNING id, status, submitted_at
        """, (
            owner["shop_id"],
            product_id,
            price,
            unit,
            submitted_by,
            evidence_url or None
        )).fetchone()

        con.execute("""
            INSERT INTO shop_update_logs (
                shop_id, action_type, description, submitted_by
            )
            VALUES (%s, 'PRICE_SUBMITTED', %s, %s)
        """, (
            owner["shop_id"],
            "Price submission ID " + str(submission["id"]),
            submitted_by
        ))

        con.commit()

    return {
        "ok": True,
        "submission": submission,
        "message": "Price submitted. It will appear publicly after admin approval."
    }


# --------------------------------------------------
# OWNER SUBMISSION HISTORY
# --------------------------------------------------

@router.get("/owners/prices/submissions")
def owner_submissions(
    authorization: str = Header(default="")
):
    owner = owner_required(authorization)

    with get_db() as con:
        rows = con.execute("""
            SELECT
                ps.id,
                p.product_name,
                ps.submitted_price,
                ps.unit,
                ps.status,
                ps.admin_note,
                ps.submitted_at,
                ps.reviewed_at
            FROM price_submissions ps
            JOIN products p ON p.id = ps.product_id
            WHERE ps.shop_id = %s
            ORDER BY ps.submitted_at DESC
        """, (owner["shop_id"],)).fetchall()

    return {"count": len(rows), "submissions": rows}


# --------------------------------------------------
# ADMIN: PENDING SUBMISSIONS
# --------------------------------------------------

@router.get("/admin/prices/pending")
def pending_prices(
    x_admin_token: str = Header(default="")
):
    admin_required(x_admin_token)

    with get_db() as con:
        rows = con.execute("""
            SELECT
                ps.id AS submission_id,
                ps.shop_id,
                s.shop_name,
                s.address,
                s.locality,
                ps.product_id,
                p.product_name,
                ps.submitted_price,
                ps.unit,
                ps.submitted_by,
                ps.evidence_url,
                ps.submitted_at
            FROM price_submissions ps
            JOIN shops s ON s.id = ps.shop_id
            JOIN products p ON p.id = ps.product_id
            WHERE ps.status = 'pending'
            ORDER BY ps.submitted_at ASC
        """).fetchall()

    return {"count": len(rows), "submissions": rows}


# --------------------------------------------------
# ADMIN: APPROVE PRICE + HISTORY
# --------------------------------------------------

@router.post("/admin/prices/{submission_id}/approve")
def approve_price(
    submission_id: int,
    payload: dict = {},
    x_admin_token: str = Header(default="")
):
    admin_required(x_admin_token)
    admin_name = clean(payload.get("reviewed_by")) or "admin"

    with get_db() as con:
        submission = con.execute("""
            SELECT *
            FROM price_submissions
            WHERE id = %s
            FOR UPDATE
        """, (submission_id,)).fetchone()

        if not submission:
            raise HTTPException(404, "Submission not found")

        if submission["status"] != "pending":
            raise HTTPException(409, "Submission has already been reviewed")

        current = con.execute("""
            SELECT id, price
            FROM prices
            WHERE shop_id = %s AND product_id = %s
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            FOR UPDATE
        """, (
            submission["shop_id"],
            submission["product_id"]
        )).fetchone()

        old_price = float(current["price"]) if current else None
        new_price = float(submission["submitted_price"])

        if current:
            con.execute("""
                INSERT INTO price_history (
                    shop_id, product_id, old_price, new_price,
                    unit, source_type, changed_at
                )
                VALUES (%s, %s, %s, %s, %s, 'Admin-verified', NOW())
            """, (
                submission["shop_id"],
                submission["product_id"],
                old_price,
                new_price,
                submission["unit"]
            ))

            con.execute("""
                UPDATE prices
                SET price = %s,
                    price_type = 'Retail',
                    source_name = 'Admin verified owner submission',
                    source_type = 'Verified',
                    is_live = TRUE,
                    in_store_confirmed = TRUE,
                    updated_at = NOW(),
                    valid_until = NOW() + INTERVAL '24 hours'
                WHERE id = %s
            """, (new_price, current["id"]))

            price_id = current["id"]

        else:
            new_row = con.execute("""
                INSERT INTO prices (
                    shop_id, product_id, price, price_type,
                    source_name, source_type, is_live,
                    in_store_confirmed, updated_at, valid_until
                )
                VALUES (
                    %s, %s, %s, 'Retail',
                    'Admin verified owner submission',
                    'Verified', TRUE, TRUE, NOW(),
                    NOW() + INTERVAL '24 hours'
                )
                RETURNING id
            """, (
                submission["shop_id"],
                submission["product_id"],
                new_price
            )).fetchone()

            price_id = new_row["id"]

            con.execute("""
                INSERT INTO price_history (
                    shop_id, product_id, old_price, new_price,
                    unit, source_type, changed_at
                )
                VALUES (%s, %s, NULL, %s, %s, 'Admin-verified', NOW())
            """, (
                submission["shop_id"],
                submission["product_id"],
                new_price,
                submission["unit"]
            ))

        con.execute("""
            UPDATE price_submissions
            SET status = 'approved',
                reviewed_at = NOW(),
                reviewed_by = %s,
                admin_note = %s
            WHERE id = %s
        """, (
            admin_name,
            clean(payload.get("admin_note")) or "Approved",
            submission_id
        ))

        con.execute("""
            INSERT INTO shop_update_logs (
                shop_id, action_type, description, submitted_by
            )
            VALUES (%s, 'PRICE_APPROVED', %s, %s)
        """, (
            submission["shop_id"],
            "Submission " + str(submission_id) + " approved",
            admin_name
        ))

        con.commit()

    return {
        "ok": True,
        "submission_id": submission_id,
        "price_id": price_id,
        "old_price": old_price,
        "new_price": new_price,
        "status": "approved"
    }


# --------------------------------------------------
# ADMIN: REJECT PRICE
# --------------------------------------------------

@router.post("/admin/prices/{submission_id}/reject")
def reject_price(
    submission_id: int,
    payload: dict = {},
    x_admin_token: str = Header(default="")
):
    admin_required(x_admin_token)

    note = clean(payload.get("admin_note")) or "Rejected by admin"
    reviewed_by = clean(payload.get("reviewed_by")) or "admin"

    with get_db() as con:
        row = con.execute("""
            UPDATE price_submissions
            SET status = 'rejected',
                admin_note = %s,
                reviewed_at = NOW(),
                reviewed_by = %s
            WHERE id = %s AND status = 'pending'
            RETURNING id, shop_id
        """, (note, reviewed_by, submission_id)).fetchone()

        if not row:
            raise HTTPException(
                404,
                "Pending submission not found"
            )

        con.execute("""
            INSERT INTO shop_update_logs (
                shop_id, action_type, description, submitted_by
            )
            VALUES (%s, 'PRICE_REJECTED', %s, %s)
        """, (row["shop_id"], note, reviewed_by))

        con.commit()

    return {"ok": True, "submission_id": submission_id, "status": "rejected"}


# --------------------------------------------------
# PUBLIC: LOW TO HIGH PRICE COMPARISON
# --------------------------------------------------

@router.get("/price-comparison")
def price_comparison(
    product_id: int,
    ward_number: int = None,
    locality: str = "",
    sort: str = "low"
):
    if sort not in ("low", "high"):
        raise HTTPException(400, "sort must be low or high")

    sql = """
        SELECT
            p.id AS price_id,
            pr.id AS product_id,
            pr.product_name,
            pr.unit,
            p.price,
            p.price_type,
            p.source_type,
            p.source_name,
            p.updated_at,
            p.valid_until,
            s.id AS shop_id,
            s.shop_name,
            s.category AS shop_category,
            s.address,
            s.locality,
            w.ward_number
        FROM prices p
        JOIN products pr ON pr.id = p.product_id
        JOIN shops s ON s.id = p.shop_id
        LEFT JOIN wards w ON w.id = s.ward_id
        WHERE p.product_id = %s
          AND p.price > 0
          AND p.in_store_confirmed = TRUE
          AND p.source_type = 'Verified'
          AND s.is_active IS DISTINCT FROM FALSE
          AND (p.valid_until IS NULL OR p.valid_until >= NOW())
    """
    args = [product_id]

    if ward_number is not None:
        sql += " AND w.ward_number = %s"
        args.append(ward_number)

    if locality:
        sql += " AND LOWER(COALESCE(s.locality, '')) = LOWER(%s)"
        args.append(locality)

    sql += " ORDER BY p.price " + ("DESC" if sort == "high" else "ASC")

    with get_db() as con:
        rows = con.execute(sql, args).fetchall()

    for row in rows:
        row["price"] = float(row["price"])

    return {
        "count": len(rows),
        "sort": sort,
        "prices": rows,
        "note": "Only currently valid, in-store-confirmed, admin-verified prices are shown."
    }


# --------------------------------------------------
# PRICE HISTORY
# --------------------------------------------------

@router.get("/price-history")
def price_history(
    product_id: int,
    shop_id: int = None,
    limit: int = Query(100, ge=1, le=500)
):
    sql = """
        SELECT
            h.id,
            h.shop_id,
            s.shop_name,
            h.product_id,
            p.product_name,
            h.old_price,
            h.new_price,
            h.unit,
            h.source_type,
            h.changed_at
        FROM price_history h
        JOIN shops s ON s.id = h.shop_id
        JOIN products p ON p.id = h.product_id
        WHERE h.product_id = %s
    """
    args = [product_id]

    if shop_id is not None:
        sql += " AND h.shop_id = %s"
        args.append(shop_id)

    sql += " ORDER BY h.changed_at DESC LIMIT %s"
    args.append(limit)

    with get_db() as con:
        rows = con.execute(sql, args).fetchall()

    for row in rows:
        if row["old_price"] is not None:
            row["old_price"] = float(row["old_price"])
        row["new_price"] = float(row["new_price"])

    return {"count": len(rows), "history": rows}


# --------------------------------------------------
# ADMIN: ADD VERIFIED OFFER
# --------------------------------------------------

@router.post("/admin/offers")
def create_offer(
    payload: dict,
    x_admin_token: str = Header(default="")
):
    admin_required(x_admin_token)

    try:
        shop_id = int(payload.get("shop_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Valid shop_id is required")

    title = clean(payload.get("title"))
    if not title:
        raise HTTPException(400, "Offer title is required")

    product_id = payload.get("product_id")
    if product_id not in (None, ""):
        try:
            product_id = int(product_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid product_id")
    else:
        product_id = None

    with get_db() as con:
        shop = con.execute("""
            SELECT id FROM shops
            WHERE id = %s AND is_active IS DISTINCT FROM FALSE
        """, (shop_id,)).fetchone()

        if not shop:
            raise HTTPException(404, "Shop not found")

        if product_id is not None:
            product = con.execute("""
                SELECT id FROM products WHERE id = %s
            """, (product_id,)).fetchone()

            if not product:
                raise HTTPException(404, "Product not found")

        row = con.execute("""
            INSERT INTO offers (
                shop_id, product_id, title, description,
                discount_text, valid_from, valid_until,
                is_active, offer_type, terms_conditions,
                source_type, is_verified, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s,
                COALESCE(%s::timestamptz, NOW()),
                %s, TRUE, %s, %s,
                'Admin-verified', TRUE, NOW()
            )
            RETURNING id
        """, (
            shop_id,
            product_id,
            title,
            clean(payload.get("description")),
            clean(payload.get("discount_text")),
            payload.get("valid_from") or None,
            payload.get("valid_until") or None,
            clean(payload.get("offer_type")) or "Shop offer",
            clean(payload.get("terms_conditions"))
        )).fetchone()

        con.commit()

    return {"ok": True, "offer_id": row["id"], "status": "verified"}


# --------------------------------------------------
# PUBLIC: VERIFIED ACTIVE OFFERS
# --------------------------------------------------

@router.get("/offers")
def public_offers(
    ward_number: int = None,
    category: str = "",
    limit: int = Query(100, ge=1, le=500)
):
    sql = """
        SELECT
            o.id,
            o.title,
            o.description,
            o.discount_text,
            o.offer_type,
            o.terms_conditions,
            o.valid_from,
            o.valid_until,
            s.id AS shop_id,
            s.shop_name,
            s.category,
            s.address,
            s.locality,
            w.ward_number,
            p.product_name
        FROM offers o
        JOIN shops s ON s.id = o.shop_id
        LEFT JOIN wards w ON w.id = s.ward_id
        LEFT JOIN products p ON p.id = o.product_id
        WHERE o.is_active = TRUE
          AND o.is_verified = TRUE
          AND s.is_active IS DISTINCT FROM FALSE
          AND (o.valid_from IS NULL OR o.valid_from <= NOW())
          AND (o.valid_until IS NULL OR o.valid_until >= NOW())
    """
    args = []

    if ward_number is not None:
        sql += " AND w.ward_number = %s"
        args.append(ward_number)

    if category:
        sql += " AND LOWER(s.category) = LOWER(%s)"
        args.append(category)

    sql += " ORDER BY o.updated_at DESC LIMIT %s"
    args.append(limit)

    with get_db() as con:
        rows = con.execute(sql, args).fetchall()

    return {"count": len(rows), "offers": rows}


# --------------------------------------------------
# ADMIN DASHBOARD
# --------------------------------------------------

@router.get("/admin/dashboard")
def admin_dashboard(
    x_admin_token: str = Header(default="")
):
    admin_required(x_admin_token)

    with get_db() as con:
        counts = con.execute("""
            SELECT
                (SELECT COUNT(*) FROM shops) AS shops,
                (SELECT COUNT(*) FROM shops WHERE is_verified = TRUE) AS verified_shops,
                (SELECT COUNT(*) FROM shop_owners) AS owners,
                (SELECT COUNT(*) FROM price_submissions WHERE status = 'pending') AS pending_prices,
                (SELECT COUNT(*) FROM price_submissions WHERE status = 'approved') AS approved_prices,
                (SELECT COUNT(*) FROM price_submissions WHERE status = 'rejected') AS rejected_prices,
                (SELECT COUNT(*) FROM prices WHERE source_type = 'Verified'
                    AND in_store_confirmed = TRUE
                    AND (valid_until IS NULL OR valid_until >= NOW())) AS live_verified_prices,
                (SELECT COUNT(*) FROM offers WHERE is_active = TRUE
                    AND is_verified = TRUE
                    AND (valid_until IS NULL OR valid_until >= NOW())) AS active_offers
        """).fetchone()

    return {"ok": True, "dashboard": counts}


# --------------------------------------------------
# ROUTER REGISTRATION
# --------------------------------------------------

def register_business_routes(app):
    app.include_router(router)
