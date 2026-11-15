# ==========================================================
# BEZAWADADEALS - GOVERNMENT AGMARKNET MARKET PRICE API
# Source: data.gov.in
# Data type: Daily mandi wholesale prices
# ==========================================================

import urllib.request
import urllib.parse
import json
from datetime import datetime

DATA_GOV_RESOURCE_ID = "9ef84268-d588-465a-a308-a864a43d0070"
DATA_GOV_API_URL = (
    "https://api.data.gov.in/resource/"
    + DATA_GOV_RESOURCE_ID
)


def fetch_government_market_prices(
    commodity=None,
    district="Krishna",
    limit=100
):
    api_key = os.getenv("DATA_GOV_IN_API_KEY")

    if not api_key:
        return {
            "success": False,
            "source": "Government of India - AGMARKNET",
            "error": "DATA_GOV_IN_API_KEY is not configured",
            "prices": []
        }

    params = {
        "api-key": api_key,
        "format": "json",
        "limit": str(limit),
        "filters[state]": "Andhra Pradesh",
        "filters[district]": district,
        "sort[arrival_date]": "desc"
    }

    if commodity:
        params["filters[commodity]"] = commodity

    url = DATA_GOV_API_URL + "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "BezawadaDeals/1.0"
        }
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20
        ) as response:
            raw = response.read().decode("utf-8")

        payload = json.loads(raw)
        records = payload.get("records", [])

        prices = []

        for item in records:
            try:
                modal = float(item.get("modal_price") or 0)
                minimum = float(item.get("min_price") or 0)
                maximum = float(item.get("max_price") or 0)

                if modal <= 0:
                    continue

                prices.append({
                    "state": item.get("state"),
                    "district": item.get("district"),
                    "market": item.get("market"),
                    "commodity": item.get("commodity"),
                    "variety": item.get("variety"),
                    "grade": item.get("grade"),
                    "arrival_date": item.get("arrival_date"),
                    "min_price": minimum,
                    "max_price": maximum,
                    "modal_price": modal,
                    "unit": "quintal",
                    "price_type": "Mandi wholesale",
                    "source": "Government of India - AGMARKNET"
                })

            except (TypeError, ValueError):
                continue

        return {
            "success": True,
            "source": "Government of India - AGMARKNET",
            "source_type": "mandi_wholesale",
            "district": district,
            "total_records": len(prices),
            "prices": prices,
            "checked_at": datetime.now().astimezone().isoformat()
        }

    except Exception as error:
        return {
            "success": False,
            "source": "Government of India - AGMARKNET",
            "error": str(error),
            "prices": []
        }


@app.get("/api/market-prices")
async def government_market_prices(
    district: str = Query(default="Krishna"),
    product_name: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500)
):
    return fetch_government_market_prices(
        commodity=product_name,
        district=district,
        limit=limit
    )


@app.get("/api/market-feed-status")
async def market_feed_status():
    api_key_configured = bool(
        os.getenv("DATA_GOV_IN_API_KEY")
    )

    return {
        "success": True,
        "provider": "data.gov.in",
        "dataset": "AGMARKNET Daily Market Prices",
        "api_key_configured": api_key_configured,
        "data_type": "Daily mandi wholesale prices",
        "district": "Krishna",
        "ward_level_prices": False,
        "retail_shop_prices": False
    }
