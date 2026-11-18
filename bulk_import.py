from typing import Optional
import os, hmac
import psycopg
from psycopg.rows import dict_row
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(tags=["Admin bulk import"])
DATABASE_URL = os.getenv("DATABASE_URL", "")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

class ShopImportRow(BaseModel):
    shop_name: str = Field(min_length=2, max_length=180)
    category: str = Field(min_length=2, max_length=100)
    ward_number: int = Field(ge=1, le=86)
    address: Optional[str] = None
    phone: Optional[str] = None
    locality_name: Optional[str] = None
    image_url: Optional[str] = None

class BulkShopImport(BaseModel):
    shops: list[ShopImportRow] = Field(min_length=1, max_length=500)

def connect():
    if not DATABASE_URL: raise HTTPException(503,"DATABASE_URL is not configured")
    return psycopg.connect(DATABASE_URL.replace("postgres://","postgresql://",1),row_factory=dict_row)

@router.post("/api/admin/import-shops")
def import_shops(data:BulkShopImport,x_admin_token:Optional[str]=Header(default=None)):
    if not ADMIN_TOKEN: raise HTTPException(503,"ADMIN_TOKEN is not configured")
    if not x_admin_token or not hmac.compare_digest(x_admin_token,ADMIN_TOKEN): raise HTTPException(401,"Invalid admin token")
    inserted,skipped=[],[]
    with connect() as con:
        for n,item in enumerate(data.shops,1):
            ward=con.execute("SELECT id FROM wards WHERE ward_number=%s AND COALESCE(is_active,TRUE)=TRUE",(item.ward_number,)).fetchone()
            if not ward:
                skipped.append({"row":n,"shop_name":item.shop_name,"reason":"Ward not found"}); continue
            exists=con.execute("SELECT id FROM shops WHERE ward_id=%s AND LOWER(BTRIM(shop_name))=LOWER(BTRIM(%s)) LIMIT 1",(ward["id"],item.shop_name)).fetchone()
            if exists:
                skipped.append({"row":n,"shop_name":item.shop_name,"reason":"Already exists"}); continue
            loc_id=None
            if item.locality_name:
                loc=con.execute("SELECT id FROM localities WHERE ward_id=%s AND LOWER(BTRIM(locality_name))=LOWER(BTRIM(%s)) LIMIT 1",(ward["id"],item.locality_name)).fetchone()
                if not loc: loc=con.execute("INSERT INTO localities(ward_id,locality_name,is_verified) VALUES(%s,%s,FALSE) RETURNING id",(ward["id"],item.locality_name)).fetchone()
                loc_id=loc["id"]
            shop=con.execute("""INSERT INTO shops(shop_name,category,ward_id,address,phone,locality_id,locality,image_url,is_verified,is_active,created_at,updated_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,FALSE,TRUE,NOW(),NOW()) RETURNING id,shop_name,category""",
                (item.shop_name,item.category,ward["id"],item.address,item.phone,loc_id,item.locality_name,item.image_url)).fetchone()
            inserted.append(shop)
        con.commit()
    return {"success":True,"received":len(data.shops),"inserted_count":len(inserted),"skipped_count":len(skipped),"inserted":inserted,"skipped":skipped}
