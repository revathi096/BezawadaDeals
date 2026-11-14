import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(
    title="BezawadaDeals",
    description="Vijayawada Local Shopping Marketplace",
    version="1.0.0"
)

BASE_DIR = Path(__file__).resolve().parent

# Templates folder
TEMPLATE_DIR = BASE_DIR / "Templates"

if not TEMPLATE_DIR.exists():
    TEMPLATE_DIR = BASE_DIR / "templates"

# HTML templates
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Static files
PUBLIC_DIR = BASE_DIR / "public"

if PUBLIC_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(PUBLIC_DIR)),
        name="static"
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):

    index_file = TEMPLATE_DIR / "index.html"

    if not index_file.exists():
        return HTMLResponse(
            content="Error: index.html not found inside Templates folder.",
            status_code=500
        )

    try:
        return templates.TemplateResponse(
            "index.html",
            {"request": request}
        )

    except Exception as e:
        return HTMLResponse(
            content=f"Website Error: {str(e)}",
            status_code=500
        )


@app.get("/health")
async def health():
    return {
        "status": "online",
        "website": "BezawadaDeals"
    }


@app.get("/api/status")
async def api_status():
    return {
        "success": True,
        "message": "BezawadaDeals Backend is running"
    }
