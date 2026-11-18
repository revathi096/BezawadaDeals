import os
import hmac
from typing import Optional

import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field


router = APIRouter(tags=["Admin bulk import"])

DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")


# -----------------------------
# Request models
# -----------------------------

class ShopImportRow(BaseModel):
    shop_name: str = Field(min_length=2, max_length=180)
    category: str = Field(min_length=2, max_length=100)
    ward_number: int = Field(ge=1, le=86)

    address: Optional[str] = Field(default=None, max_length=1000)
    phone: Optional[str] = Field(default=None, max_length=30)
    locality_name: Optional[str] = Field(default=None, max_length=180)
    image_url: Optional[str] = Field(default=None, max_length=1000)


class BulkShopImport(BaseModel):
    shops: list[ShopImportRow] = Field(min_length=1, max_length=500)


# -----------------------------
# Database connection
# -----------------------------

def get_connection():
    if not DATABASE_URL:
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL is not configured"
        )

    db_url = DATABASE_URL

    if db_url.startswith("postgres://"):
        db_url = db_url.replace(
            "postgres://",
            "postgresql://",
            1
        )

    try:
        return psycopg.connect(
            db_url,
            row_factory=dict_row
        )
    except psycopg.Error:
        raise HTTPException(
            status_code=503,
            detail="Database connection failed"
        )


# -----------------------------
# Admin authentication
# -----------------------------

def verify_admin_token(token: Optional[str]):
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="ADMIN_TOKEN is not configured"
        )

    if not token or not hmac.compare_digest(
        token,
        ADMIN_TOKEN
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin token"
        )


# -----------------------------
# Bulk import shops
# -----------------------------

@router.post("/api/admin/import-shops")
def import_shops(
    data: BulkShopImport,
    x_admin_token: Optional[str] = Header(default=None)
):
    verify_admin_token(x_admin_token)

    inserted = []
    skipped = []

    try:
        with get_connection() as con:

            for row_number, item in enumerate(
                data.shops,
                start=1
            ):

                shop_name = item.shop_name.strip()
                category = item.category.strip()

                if not shop_name or not category:
                    skipped.append({
                        "row": row_number,
                        "shop_name": item.shop_name,
                        "reason": "Shop name or category is empty"
                    })
                    continue

                # Find active ward
                ward = con.execute(
                    """
                    SELECT id
                    FROM wards
                    WHERE ward_number = %s
                      AND COALESCE(is_active, TRUE) = TRUE
                    LIMIT 1
                    """,
                    (item.ward_number,)
                ).fetchone()

                if not ward:
                    skipped.append({
                        "row": row_number,
                        "shop_name": shop_name,
                        "reason": "Ward not found or inactive"
                    })
                    continue

                ward_id = ward["id"]

                # Prevent duplicate shops
                existing_shop = con.execute(
                    """
                    SELECT id
                    FROM shops
                    WHERE ward_id = %s
                      AND LOWER(BTRIM(shop_name)) =
                          LOWER(BTRIM(%s))
                    LIMIT 1
                    """,
                    (ward_id, shop_name)
                ).fetchone()

                if existing_shop:
                    skipped.append({
                        "row": row_number,
                        "shop_name": shop_name,
                        "reason": "Shop already exists in this ward"
                    })
                    continue

                # Find or create locality
                locality_id = None
                locality_name = (
                    item.locality_name.strip()
                    if item.locality_name
                    else None
                )

                if locality_name:

                    locality = con.execute(
                        """
                        SELECT id
                        FROM localities
                        WHERE ward_id = %s
                          AND LOWER(BTRIM(locality_name)) =
                              LOWER(BTRIM(%s))
                        LIMIT 1
                        """,
                        (ward_id, locality_name)
                    ).fetchone()

                    if locality:
                        locality_id = locality["id"]

                    else:
                        new_locality = con.execute(
                            """
                            INSERT INTO localities (
                                ward_id,
                                locality_name,
                                is_verified
                            )
                            VALUES (%s, %s, FALSE)
                            RETURNING id
                            """,
                            (ward_id, locality_name)
                        ).fetchone()

                        locality_id = new_locality["id"]

                # Insert shop without overwriting existing data
                shop = con.execute(
                    """
                    INSERT INTO shops (
                        shop_name,
                        category,
                        ward_id,
                        address,
                        phone,
                        locality_id,
                        locality,
                        image_url,
                        is_verified,
                        is_active,
                        created_at,
                        updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        FALSE, TRUE, NOW(), NOW()
                    )
                    RETURNING
                        id,
                        shop_name,
                        category,
                        ward_id,
                        locality_id
                    """,
                    (
                        shop_name,
                        category,
                        ward_id,
                        item.address,
                        item.phone,
                        locality_id,
                        locality_name,
                        item.image_url
                    )
                ).fetchone()

                inserted.append(shop)

            con.commit()

    except HTTPException:
        raise

    except psycopg.Error:
        raise HTTPException(
            status_code=500,
            detail="Bulk shop import failed. Database transaction rolled back."
        )

    return {
        "success": True,
        "received": len(data.shops),
        "inserted_count": len(inserted),
        "skipped_count": len(skipped),
        "inserted": inserted,
        "skipped": skipped
    }
