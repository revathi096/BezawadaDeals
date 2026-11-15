import os
import re
import json
import time
from datetime import date, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Query

router = APIRouter()

BASE_URL = "https://api.ceda.ashoka.edu.in/agmarknet"

STATE_ID = 28  # Andhra Pradesh

CACHE = {}
CACHE_SECONDS = 1800

# Supported agricultural commodities.
COMMODITIES = {
    "Tomato": ["tomato"],
    "Onion": ["onion"],
    "Potato": ["potato"],
    "Brinjal": ["brinjal", "eggplant"],
    "Cabbage": ["cabbage"],
    "Cauliflower": ["cauliflower"],
    "Carrot": ["carrot"],
    "Green Chilli": ["green chilli", "chilli"],
    "Banana": ["banana"],
    "Mango": ["mango"],
    "Apple": ["apple"],
    "Grapes": ["grapes"],
    "Orange": ["orange"],
    "Papaya": ["papaya"],
    "Rice": ["rice"],
    "Wheat": ["wheat"],
    "Maize": ["maize"],
    "Groundnut": ["groundnut"],
    "Turmeric": ["turmeric"],
    "Cotton": ["cotton"],
}

CATEGORY_PRODUCTS = {
    "Vegetables": [
        "Tomato", "Onion", "Potato", "Brinjal", "Cabbage",
        "Cauliflower", "Carrot", "Green Chilli"
    ],
    "Fruits": [
        "Banana", "Mango", "Apple", "Grapes", "Orange", "Papaya"
    ],
    "Rice & Pulses": ["Rice", "Wheat"],
    "Groceries": [
        "Rice", "Wheat", "Maize", "Groundnut", "Turmeric"
    ],
}

def clean_name(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def find_rows(obj):
    """Find record arrays in different API response structures."""
    if isinstance(obj, list):
        return obj

    if isinstance(obj, dict):
        for key in ("data", "records", "results", "prices", "rows"):
            value = obj.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                found = find_rows(value)
                if found:
                    return found

        for value in obj.values():
            if isinstance(value, (dict, list)):
                found = find_rows(value)
                if found:
                    return found

    return []


async def get_commodities(client):
    response = await client.get(
        f"{BASE_URL}/commodities",
        timeout=25
    )
    response.raise_for_status()
    return response.json()


async def get_commodity_id(client, product_name):
    data = await get_commodities(client)
    rows = find_rows(data)

    wanted = clean_name(product_name)

    for row in rows:
        if not isinstance(row, dict):
            continue

        name = clean_name(
            row.get("commodity")
            or row.get("commodity_name")
            or row.get("name")
            or row.get("display_name")
        )

        if name == wanted:
            return (
                row.get("commodity_id")
                or row.get("id")
                or row.get("commodityId")
            )

    return None


def normalize_price_rows(payload, product_name):
    rows = find_rows(payload)
    output = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        market = (
            row.get("market")
            or row.get("market_name")
            or row.get("Market")
            or row.get("mandi")
        )

        district = (
            row.get("district")
            or row.get("district_name")
            or row.get("District")
        )

        modal = (
            row.get("modal_price")
            or row.get("modal")
            or row.get("Modal Price")
            or row.get("modalPrice")
        )

        minimum = (
            row.get("min_price")
            or row.get("minimum_price")
            or row.get("Min Price")
        )

        maximum = (
            row.get("max_price")
            or row.get("maximum_price")
            or row.get("Max Price")
        )

        reported_date = (
            row.get("arrival_date")
            or row.get("price_date")
            or row.get("date")
            or row.get("reported_date")
        )

        try:
            modal = float(str(modal).replace(",", ""))
        except (ValueError, TypeError):
            continue

        if modal <= 0:
            continue

        try:
            minimum = float(str(minimum).replace(",", ""))
        except (ValueError, TypeError):
            minimum = None

        try:
            maximum = float(str(maximum).replace(",", ""))
        except (ValueError, TypeError):
            maximum = None

        output.append({
            "product_name": product_name,
            "market_name": str(market or "Market not specified"),
            "district": str(district or ""),
            "min_price_per_quintal": minimum,
            "max_price_per_quintal": maximum,
            "modal_price_per_quintal": modal,
            "unit": "quintal",
            "reported_date": str(reported_date or ""),
            "source": "AGMARKNET / CEDA",
            "source_type": "government_mandi_wholesale"
        })

    return output


async def fetch_product_price(product_name):
    cache_key = clean_name(product_name)
    now = time.time()

    cached = CACHE.get(cache_key)
    if cached and now - cached["time"] < CACHE_SECONDS:
        return cached["data"]

    async with httpx.AsyncClient(
        headers={"Accept": "application/json"},
        follow_redirects=True
    ) as client:

        commodity_id = await get_commodity_id(client, product_name)

        if not commodity_id:
            return []

        end_date = date.today()
        start_date = end_date - timedelta(days=7)

        payload = {
            "state_id": STATE_ID,
            "commodity_id": int(commodity_id),
            "district_id": 0,
            "calculation_type": "d",
            "chart_type": "datadownload",
            "start_date": start_date.isoformat() + "T00:00:00Z",
            "end_date": end_date.isoformat() + "T23:59:59Z"
        }

        response = await client.post(
            f"{BASE_URL}/prices",
            json=payload,
            timeout=45
        )
        response.raise_for_status()

        result = normalize_price_rows(
            response.json(),
            product_name
        )

        CACHE[cache_key] = {
            "time": now,
            "data": result
        }

        return result


@router.get("/api/market-prices")
async def market_prices(
    ward_number: int = Query(..., ge=1, le=86),
    category: str = Query(...),
    product_name: Optional[str] = None
):
    category = category.strip()

    if product_name:
        selected = [
            name for name in COMMODITIES
            if clean_name(name) == clean_name(product_name)
        ]
    else:
        selected = CATEGORY_PRODUCTS.get(category, [])

    if not selected:
        return {
            "success": True,
            "ward_number": ward_number,
            "category": category,
            "price_type": "government_mandi_wholesale",
            "total": 0,
            "prices": [],
            "message": "No supported government market price feed for this category."
        }

    all_prices = []
    errors = []

    for name in selected:
        try:
            rows = await fetch_product_price(name)
            all_prices.extend(rows)
        except Exception as exc:
            errors.append({
                "product": name,
                "error": str(exc)[:250]
            })

    all_prices.sort(
        key=lambda item: (
            item.get("reported_date", ""),
            item.get("product_name", ""),
            item.get("market_name", "")
        ),
        reverse=True
    )

    return {
        "success": True,
        "ward_number": ward_number,
        "category": category,
        "coverage": "Andhra Pradesh market reference; not ward-specific",
        "price_type": "government_mandi_wholesale",
        "total": len(all_prices),
        "prices": all_prices,
        "errors": errors,
        "source": "AGMARKNET / CEDA",
        "checked_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    }
