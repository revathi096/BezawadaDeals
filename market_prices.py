import os
import httpx
from fastapi import APIRouter, Query

router = APIRouter(tags=["AGMARKNET wholesale feed"])
API_KEY = os.getenv("AGMARKNET_API_KEY", "").strip()
BASE_URL = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"

@router.get("/api/agmarknet/market-prices")
async def agmarknet_prices(commodity: str = "", district: str = "", limit: int = Query(100, ge=1, le=500)):
    if not API_KEY:
        return {"success": False, "count": 0, "prices": [], "message": "AGMARKNET_API_KEY is not configured", "scope": "Wholesale mandi prices only; not shop retail prices."}
    params = {"api-key": API_KEY, "format": "json", "limit": limit, "filters[state]": "Andhra Pradesh"}
    if commodity: params["filters[commodity]"] = commodity
    if district: params["filters[district]"] = district
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(BASE_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        records = payload.get("records", [])
        return {"success": True, "count": len(records), "prices": records, "total": payload.get("total", len(records)), "scope": "Andhra Pradesh mandi wholesale prices; not shop retail prices."}
    except Exception as exc:
        return {"success": False, "count": 0, "prices": [], "message": f"Feed request failed: {type(exc).__name__}", "scope": "Wholesale mandi prices only."}

@router.get("/api/agmarknet/status")
async def agmarknet_status():
    return {"feed": "AGMARKNET / data.gov.in", "configured": bool(API_KEY), "endpoint": "/api/agmarknet/market-prices", "scope": "Andhra Pradesh wholesale mandi rates"}
