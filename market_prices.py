import os
import logging
from datetime import date, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger("bezawadadeals.agmarknet")

router = APIRouter(
    prefix="/api/agmarknet",
    tags=["AGMARKNET Wholesale Prices"]
)

CEDA_BASE_URL = os.getenv(
    "CEDA_AGMARKNET_URL",
    "https://agmarknet.ceda.ashoka.edu.in/api"
)

REQUEST_TIMEOUT = 30


async def ceda_get(path: str):
    url = f"{CEDA_BASE_URL.rstrip('/')}/{path.lstrip('/')}"

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": "BezawadaDeals/18.0",
                "Accept": "application/json",
                "Origin": "https://agmarknet.ceda.ashoka.edu.in",
                "Referer": "https://agmarknet.ceda.ashoka.edu.in/"
            }
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException:
        logger.exception("CEDA request timed out")
        raise HTTPException(
            status_code=504,
            detail="AGMARKNET source timed out"
        )

    except httpx.HTTPStatusError as exc:
        logger.exception("CEDA HTTP error")
        raise HTTPException(
            status_code=502,
            detail=f"AGMARKNET source returned HTTP {exc.response.status_code}"
        )

    except (httpx.RequestError, ValueError):
        logger.exception("CEDA connection or response error")
        raise HTTPException(
            status_code=502,
            detail="Unable to retrieve AGMARKNET data"
        )


async def ceda_post(path: str, payload: dict):
    url = f"{CEDA_BASE_URL.rstrip('/')}/{path.lstrip('/')}"

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
            headers={
                "User-Agent": "BezawadaDeals/18.0",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://agmarknet.ceda.ashoka.edu.in",
                "Referer": "https://agmarknet.ceda.ashoka.edu.in/"
            }
        ) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()

    except httpx.TimeoutException:
        logger.exception("CEDA price request timed out")
        raise HTTPException(
            status_code=504,
            detail="AGMARKNET price request timed out"
        )

    except httpx.HTTPStatusError as exc:
        logger.exception("CEDA price HTTP error")
        raise HTTPException(
            status_code=502,
            detail=f"AGMARKNET source returned HTTP {exc.response.status_code}"
        )

    except (httpx.RequestError, ValueError):
        logger.exception("CEDA price connection or response error")
        raise HTTPException(
            status_code=502,
            detail="Unable to retrieve AGMARKNET prices"
        )


# --------------------------------------------------
# SOURCE HEALTH
# --------------------------------------------------

@router.get("/health")
async def agmarknet_health():
    result = await ceda_get("commodities")

    return {
        "success": True,
        "source": "CEDA AGMARKNET",
        "connection": "available",
        "response_received": result is not None
    }


# --------------------------------------------------
# COMMODITIES
# --------------------------------------------------

@router.get("/commodities")
async def agmarknet_commodities():
    result = await ceda_get("commodities")

    return {
        "success": True,
        "source": "CEDA AGMARKNET",
        "data": result
    }


# --------------------------------------------------
# STATES AND DISTRICTS
# --------------------------------------------------

@router.get("/geographies")
async def agmarknet_geographies():
    result = await ceda_get("geographies")

    return {
        "success": True,
        "source": "CEDA AGMARKNET",
        "data": result
    }


# --------------------------------------------------
# MARKETS
# --------------------------------------------------

@router.get("/markets")
async def agmarknet_markets(
    commodity_id: int = Query(..., ge=1),
    state_id: int = Query(..., ge=1),
    district_id: int = Query(0, ge=0)
):
    payload = {
        "commodity_id": commodity_id,
        "state_id": state_id,
        "district_id": district_id
    }

    result = await ceda_post("markets", payload)

    return {
        "success": True,
        "source": "CEDA AGMARKNET",
        "filters": payload,
        "data": result
    }


# --------------------------------------------------
# WHOLESALE PRICES
# --------------------------------------------------

@router.get("/prices")
async def agmarknet_prices(
    commodity_id: int = Query(..., ge=1),
    district_id: int = Query(0, ge=0),
    state_id: int = Query(28, ge=1),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    calculation_type: str = Query("d", pattern="^(d|m|y)$")
):
    """
    CEDA AGMARKNET price API.

    calculation_type:
        d = daily
        m = monthly
        y = yearly

    Andhra Pradesh CEDA state ID: 28.
    """

    today = date.today()

    if end_date is None:
        end_date = today

    if start_date is None:
        start_date = end_date - timedelta(days=7)

    if start_date > end_date:
        raise HTTPException(
            status_code=400,
            detail="start_date cannot be after end_date"
        )

    if (end_date - start_date).days > 366:
        raise HTTPException(
            status_code=400,
            detail="Maximum date range is 366 days"
        )

    payload = {
        "state_id": state_id,
        "commodity_id": commodity_id,
        "district_id": district_id,
        "calculation_type": calculation_type,
        "chart_type": "timeseries",
        "start_date": start_date.isoformat() + "T00:00:00Z",
        "end_date": end_date.isoformat() + "T23:59:59Z"
    }

    result = await ceda_post("prices", payload)

    return {
        "success": True,
        "source": "CEDA AGMARKNET",
        "price_type": "Wholesale mandi price",
        "state_id": state_id,
        "commodity_id": commodity_id,
        "district_id": district_id,
        "start_date": start_date,
        "end_date": end_date,
        "calculation_type": calculation_type,
        "data": result,
        "note": (
            "These are wholesale mandi prices. "
            "They are not Vijayawada retail shop prices."
        )
    }
