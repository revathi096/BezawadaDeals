import os
import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Application
app = FastAPI(
    title="BezawadaDeals",
    description="Vijayawada Local Shopping Marketplace",
    version="1.0.0"
)

# Project directory
BASE_DIR = Path(__file__).resolve().parent

# Find Templates folder
if (BASE_DIR / "Templates").is_dir():
    TEMPLATE_DIR = BASE_DIR / "Templates"
elif (BASE_DIR / "templates").is_dir():
    TEMPLATE_DIR = BASE_DIR / "templates"
else:
    TEMPLATE_DIR = None

# Initialize templates safely
templates = None

if TEMPLATE_DIR:
    templates = Jinja2Templates(
        directory=str(TEMPLATE_DIR)
    )

# Static files
PUBLIC_DIR = BASE_DIR / "public"

if PUBLIC_DIR.is_dir():
    app.mount(
        "/static",
        StaticFiles(directory=str(PUBLIC_DIR)),
        name="static"
    )


# Homepage
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):

    if TEMPLATE_DIR is None:
        return HTMLResponse(
            "<h1>Templates folder not found</h1>",
            status_code=500
        )

    index_file = TEMPLATE_DIR / "index.html"

    if not index_file.is_file():
        return HTMLResponse(
            "<h1>index.html not found</h1>",
            status_code=500
        )

    try:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={}
        )

    except Exception:
        error_details = traceback.format_exc()
        print(error_details)

        return HTMLResponse(
            content=(
                "<h2>BezawadaDeals Template Error</h2>"
                "<p>Please check Render Logs for details.</p>"
            ),
            status_code=500
        )


# Health check
@app.get("/health")
async def health():
    return {
        "status": "online",
        "website": "BezawadaDeals"
    }


# API status
@app.get("/api/status")
async def api_status():
    return {
        "success": True,
        "message": "BezawadaDeals Backend is running"
    }


# Root directory check
@app.get("/api/check")
async def check_files():
    return {
        "templates_folder": str(TEMPLATE_DIR) if TEMPLATE_DIR else None,
        "index_html_exists": (
            (TEMPLATE_DIR / "index.html").is_file()
            if TEMPLATE_DIR else False
        ),
        "public_folder_exists": PUBLIC_DIR.is_dir()
    }
