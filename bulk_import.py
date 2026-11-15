from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["Bulk Shop Import"])


class ShopImportRow(BaseModel):
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
    shops: list[ShopImportRow] = Field(min_length=1, max_length=1000)


@router.post("/api/admin/import-shops")
async def import_shops(
    data: BulkShopImport,
    x_api_key: Optional[str] = Header(default=None)
):
    import app as main_app

    main_app.check_admin_key(x_api_key)

    inserted = []
    skipped = []

    with main_app.get_connection() as connection:
        with connection.cursor() as cursor:

            for row_no, shop in enumerate(data.shops, start=1):
                name = shop.shop_name.strip()

                if not name:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Row {row_no}: Shop name is empty"
                    )

                category = main_app.canonical_category(shop.category)

                cursor.execute("""
                    SELECT id FROM wards
                    WHERE ward_number=%s AND is_active=TRUE
                """, (shop.ward_number,))

                ward = cursor.fetchone()

                if not ward:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Row {row_no}: Ward not found"
                    )

                cursor.execute("""
                    SELECT id FROM shops
                    WHERE ward_id=%s
                    AND LOWER(TRIM(shop_name))=LOWER(%s)
                    AND LOWER(category)=LOWER(%s)
                    LIMIT 1
                """, (ward["id"], name, category))

                existing = cursor.fetchone()

                if existing:
                    skipped.append({
                        "shop_name": name,
                        "reason": "Already exists",
                        "shop_id": existing["id"]
                    })
                    continue

                locality_id = None

                if shop.locality_name and shop.locality_name.strip():
                    cursor.execute("""
                        INSERT INTO localities(
                            ward_id, locality_name
                        )
                        VALUES(%s,%s)
                        ON CONFLICT(ward_id, locality_name)
                        DO UPDATE SET
                            locality_name=EXCLUDED.locality_name
                        RETURNING id
                    """, (
                        ward["id"],
                        shop.locality_name.strip()
                    ))

                    locality_id = cursor.fetchone()["id"]

                cursor.execute("""
                    INSERT INTO shops(
                        shop_name,
                        category,
                        ward_id,
                        address,
                        phone,
                        owner_name,
                        locality_id,
                        latitude,
                        longitude,
                        image_url,
                        is_verified,
                        data_connection_status
                    )
                    VALUES(
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        FALSE,'not_connected'
                    )
                    RETURNING id, shop_name, category
                """, (
                    name,
                    category,
                    ward["id"],
                    shop.address,
                    shop.phone,
                    shop.owner_name,
                    locality_id,
                    shop.latitude,
                    shop.longitude,
                    shop.image_url
                ))

                inserted.append(cursor.fetchone())

        connection.commit()

    return {
        "success": True,
        "received": len(data.shops),
        "inserted_count": len(inserted),
        "skipped_count": len(skipped),
        "inserted": inserted,
        "skipped": skipped,
        "message": "Shop import completed. Shops are unverified."
    }
