import os
import traceback
from pathlib import Path

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Application
app = FastAPI(
    title="BezawadaDeals",
    description="Vijayawada Local Shopping Marketplace",
    version="2.0.0"
)

BASE_DIR = Path(__file__).resolve().parent

# Find Templates folder
if (BASE_DIR / "Templates").is_dir():
    TEMPLATE_DIR = BASE_DIR / "Templates"
elif (BASE_DIR / "templates").is_dir():
    TEMPLATE_DIR = BASE_DIR / "templates"
else:
    TEMPLATE_DIR = None

templates = None

if TEMPLATE_DIR:
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

# Static files
PUBLIC_DIR = BASE_DIR / "public"

if PUBLIC_DIR.is_dir():
    app.mount(
        "/static",
        StaticFiles(directory=str(PUBLIC_DIR)),
        name="static"
    )


# PostgreSQL connection
def get_database_url():
    url = os.getenv("DATABASE_URL")

    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    return url


# Create database tables
def create_database_tables():
    database_url = get_database_url()

    if not database_url:
        print("DATABASE_URL environment variable is missing.")
        return False

    sql_commands = [
        """
        CREATE TABLE IF NOT EXISTS wards (
            id BIGSERIAL PRIMARY KEY,
            ward_number INTEGER UNIQUE NOT NULL,
            area_name TEXT NOT NULL,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS shops (
            id BIGSERIAL PRIMARY KEY,
            shop_name TEXT NOT NULL,
            category TEXT NOT NULL,
            ward_id BIGINT REFERENCES wards(id),
            address TEXT,
            phone TEXT,
            owner_name TEXT,
            is_verified BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS products (
            id BIGSERIAL PRIMARY KEY,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            unit TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS prices (
            id BIGSERIAL PRIMARY KEY,
            shop_id BIGINT REFERENCES shops(id),
            product_id BIGINT NOT NULL REFERENCES products(id),
            price NUMERIC(12,2) NOT NULL CHECK (price >= 0),
            price_type TEXT DEFAULT 'shop_retail',
            source_name TEXT,
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_shops_ward
        ON shops(ward_id)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_products_category
        ON products(category)
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_prices_product
        ON prices(product_id)
        """
    ]

    try:
        with psycopg.connect(
            database_url,
            connect_timeout=10
        ) as connection:

            with connection.cursor() as cursor:
                for command in sql_commands:
                    cursor.execute(command)

        print("BezawadaDeals database tables are ready.")
        return True

    except Exception as error:
        print("Database setup error:", str(error))
        traceback.print_exc()
        return False


# Initialize database when application starts
@app.on_event("startup")
async def startup_event():
    create_database_tables()


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
        traceback.print_exc()

        return HTMLResponse(
            "<h2>BezawadaDeals Template Error</h2>"
            "<p>Please check Render Logs.</p>",
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


# Database connection test
@app.get("/api/db-status")
async def database_status():

    database_url = get_database_url()

    if not database_url:
        return {
            "success": False,
            "database": "not configured",
            "message": "DATABASE_URL is missing"
        }

    try:
        with psycopg.connect(
            database_url,
            connect_timeout=10
        ) as connection:

            with connection.cursor() as cursor:
                cursor.execute("SELECT version()")
                version = cursor.fetchone()[0]

                cursor.execute("""
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = 'public'
                    ORDER BY table_name
                """)

                tables = [row[0] for row in cursor.fetchall()]

        return {
            "success": True,
            "database": "connected",
            "tables": tables,
            "message": "BezawadaDeals PostgreSQL connected successfully"
        }

    except Exception as error:
        print("Database connection test error:", str(error))

        return {
            "success": False,
            "database": "connection failed",
            "message": str(error)
        }


# Check project files
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
