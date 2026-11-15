import os
import re
import time
import logging
from datetime import date, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Query

router = APIRouter()
logger = logging.getLogger("bezawadadeals.market_prices")

# CEDA public Agmarknet data API
BASE_URL = "https://agmarknet.ceda.ashoka.edu.in/api"
STATE_ID = 28  # Andhra Pradesh

CACHE_SECONDS = 30 * 60
_cache: dict[str, Any] = {
    "expires": 0,
    "data": None
}

CATEGORY_PRODUCTS = {
    "Vegetables": [
        "Tomato", "Onion", "Potato", "Brinjal",
        "Cabbage", "Cauliflower", "Carrot",
        "Green Chilli", "Drumstick", "Ladies Finger"
    ],
    "Fruits": [
        "Banana", "Mango", "Apple", "Grapes",
        "Orange", "Papaya", "Guava", "Pomegranate"
    ],
    "Rice & Pulses": [
        "Rice", "Wheat", "Maize", "Bengal Gram",
        "Black Gram", "Green Gram", "Red Gram"
    ],
    "Groceries": [
        "Turmeric", "Groundnut", "Chilli",
        "Coriander", "Cotton"
    ]
}

ALIASES = {
    "tomatoes": "Tomato",
    "onions": "Onion",
    "potatoes": "Potato",
    "brinjals": "Brinjal",
    "banana": "Banana",
    "green chilli": "Green Chilli",
    "chillies": "Chilli",
    "red chilli": "Chilli",
    "ground nut": "Groundnut",
    "paddy": "Rice",
    "arhar": "Red Gram",
    "tur": "Red Gram",
    "moong": "Green Gram",
    "urad": "Black Gram",
    "chana": "Bengal Gram"
}


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_name(value: str) -> str:
    name = clean_text(value).casefold()
    return ALIASES.get(name, name).casefold()


def find_value(row: dict, *keys):
    lowered = {
        str(key).casefold(): value
        for key, value in row.items()
    }

    for key in keys:
        value = lowered.get(key.casefold())
        if value not in (None, ""):
            return value

    return None


def as_number(value):
    if value is None:
        return None

    try:
        cleaned = str(value).replace(",", "").strip()
        if cleaned in ("", "-", "NR", "NA", "N/A"):
            return None
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def extract_rows(payload):
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("data", "prices", "results", "records", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value

            if isinstance(value, dict):
                nested = extract_rows(value)
                if nested:
                    return nested

    return []


async def api_get(client: httpx.AsyncClient, path: str):
    response = await client.get(f"{BASE_URL}{path}")
    response.raise_for_status()
    return response.json()


async def get_commodities(client):
    payload = await api_get(client, "/commodities")
    rows = extract_rows(payload)

    result = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        name = find_value(
            row, "commodity", "commodity_name",
            "name", "commodityName"
        )
        commodity_id = find_value(
            row, "commodity_id", "id", "commodityId"
        )

        if name and commodity_id is not None:
            result[normalize_name(name)] = {
                "id": commodity_id,
                "name": clean_text(name)
            }

    return result


def resolve_commodity(name: str, commodities: dict):
    target = normalize_name(name)

    if target in commodities:
        return commodities[target]

    for key, value in commodities.items():
        if target == key or target in key or key in target:
            return value

    return None


def normalize_price_row(row: dict, requested_product: str):
    market = find_value(
        row, "market", "market_name", "marketName",
        "market_name_en", "marketname"
    )
    district = find_value(
        row, "district", "district_name", "districtName"
    )
    state = find_value(
        row, "state", "state_name", "stateName"
    )
    commodity = find_value(
        row, "commodity", "commodity_name",
        "commodityName", "name"
    )
    variety = find_value(row, "variety", "variety_name")

    minimum = as_number(find_value(
        row, "min_price", "minprice", "minimum_price", "min"
    ))
    maximum = as_number(find_value(
        row, "max_price", "maxprice", "maximum_price", "max"
    ))
    modal = as_number(find_value(
        row, "modal_price", "modalprice", "modal"
    ))

    reported_date = find_value(
        row, "arrival_date", "price_date", "date",
        "reported_date", "reportedDate"
    )

    # Do not invent a market name or a price.
    if modal is None:
        return None

    return {
        "product_name": clean_text(commodity) or requested_product,
        "requested_product": requested_product,
        "variety": clean_text(variety) or None,
        "market_name": clean_text(market) or "Market name not supplied",
        "district": clean_text(district) or "District not supplied",
        "state": clean_text(state) or "Andhra Pradesh",
        "min_price": minimum,
        "max_price": maximum,
        "modal_price": modal,
        "price": modal,
        "unit": "₹ / quintal",
        "reported_date": clean_text(reported_date) or None,
        "source": "AGMARKNET via CEDA",
        "source_type": "government_mandi_wholesale",
        "price_label": "Wholesale market reference price",
        "is_shop_retail": False
    }


async def fetch_product_prices(
    client: httpx.AsyncClient,
    commodities: dict,
    product_name: str,
    start_date: str,
    end_date: str
):
    commodity = resolve_commodity(product_name, commodities)

    if not commodity:
        return []

    payload = {
        "state_id": STATE_ID,
        "commodity_id": commodity["id"],
        "district_id": 0,
        "calculation_type": "d",
        "chart_type": "datadownload",
        "start_date": f"{start_date}T00:00:00Z",
        "end_date": f"{end_date}T23:59:59Z"
    }

    response = await client.post(
        f"{BASE_URL}/prices",
        json=payload
    )
    response.raise_for_status()

    raw_rows = extract_rows(response.json())
    output = []

    for row in raw_rows:
        if not isinstance(row, dict):
            continue

        item = normalize_price_row(row, product_name)
        if item:
            output.append(item)

    return output


async def fetch_market_prices(category: str, product_name: str = ""):
    now = time.time()
    cache_key = f"{category.casefold()}::{product_name.casefold()}"

    if (
        _cache["data"] is not None
        and _cache["expires"] > now
        and _cache.get("key") == cache_key
    ):
        return _cache["data"]

    products = CATEGORY_PRODUCTS.get(category, [])

    if product_name:
        products = [
            product for product in products
            if normalize_name(product) == normalize_name(product_name)
            or normalize_name(product_name) in normalize_name(product)
        ]

        # Permit a commodity search if it is in the configured categories.
        if not products:
            products = [
                product_name.strip()
            ]

    if not products:
        return {
            "success": True,
            "source": "AGMARKNET via CEDA",
            "scope": "Andhra Pradesh state-level mandi data",
            "category": category,
            "prices": [],
            "message": (
                "ఈ categoryకి AGMARKNET agricultural commodity "
                "data mapping లేదు."
            )
        }

    end = date.today()
    start = end - timedelta(days=10)

    headers = {
        "User-Agent": "BezawadaDeals/1.0 price-information",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://agmarknet.ceda.ashoka.edu.in",
        "Referer": "https://agmarknet.ceda.ashoka.edu.in/"
    }

    timeout = httpx.Timeout(25.0, connect=10.0)

    all_prices = []
    errors = []

    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=True
    ) as client:
        try:
            commodities = await get_commodities(client)
        except Exception as error:
            logger.exception("Could not load CEDA commodities")
            return {
                "success": False,
                "source": "AGMARKNET via CEDA",
                "scope": "Andhra Pradesh state-level mandi data",
                "category": category,
                "prices": [],
                "message": "Commodity list fetch కాలేదు.",
                "error": str(error)[:250]
            }

        for product in products:
            try:
                rows = await fetch_product_prices(
                    client,
                    commodities,
                    product,
                    start.isoformat(),
                    end.isoformat()
                )
                all_prices.extend(rows)
            except Exception as error:
                logger.warning(
                    "Price fetch failed for %s: %s",
                    product,
                    error
                )
                errors.append(product)

    # Newest reported observations first when date is available.
    all_prices.sort(
        key=lambda item: item.get("reported_date") or "",
        reverse=True
    )

    result = {
        "success": True,
        "source": "AGMARKNET via CEDA",
        "scope": (
            "Andhra Pradesh state-level mandi data. "
            "Not a Vijayawada ward-level or shop-retail quote."
        ),
        "category": category,
        "requested_product": product_name or None,
        "date_range": {
            "from": start.isoformat(),
            "to": end.isoformat()
        },
        "total": len(all_prices),
        "prices": all_prices,
        "failed_products": errors,
        "message": (
            "ధరలు మార్కెట్ wholesale సమాచారం మాత్రమే. "
            "దుకాణ retail ధరలుగా పరిగణించవద్దు."
        )
    }

    _cache.update({
        "key": cache_key,
        "expires": now + CACHE_SECONDS,
        "data": result
    })

    return result


@router.get("/api/market-prices")
async def market_prices(
    ward_number: int = Query(ge=1, le=86),
    category: str = Query(min_length=2),
    product_name: str = Query(default="")
):
    category = category.strip()

    if category not in CATEGORY_PRODUCTS:
        return {
            "success": True,
            "category": category,
            "scope": "No matching public agricultural feed",
            "prices": [],
            "message": (
                "ఈ categoryకి ప్రస్తుతం కనెక్ట్ చేసిన "
                "ప్రభుత్వ మార్కెట్ ధరల feed లేదు."
            )
        }

    result = await fetch_market_prices(
        category,
        product_name.strip()
    )

    # Ward number is retained for the website filter, but the
    # external feed is state-level and is not falsely ward-mapped.
    result["selected_ward"] = ward_number
    result["ward_specific"] = False

    return result


@router.get("/api/market-feed-status")
async def market_feed_status():
    return {
        "success": True,
        "feed": "AGMARKNET via CEDA",
        "state": "Andhra Pradesh",
        "state_id": STATE_ID,
        "supported_categories": list(CATEGORY_PRODUCTS.keys()),
        "cache_minutes": CACHE_SECONDS // 60,
        "scope": "State-level mandi wholesale prices",
        "not_supported": [
            "Ward-level shop retail prices",
            "All supermarket live prices",
            "Mall store prices",
            "Mall offers without official feeds",
            "Restaurants and other non-agricultural categories"
        ]
    }
