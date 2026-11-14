
import os
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request

app = FastAPI(
    title="BezawadaDeals",
    description="Vijayawada Local Shopping Marketplace",
    version="1.0.0"
)

if os.path.isdir("public"):
    app.mount("/static", StaticFiles(directory="public"), name="static")

templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {"request": request}
    )

@app.get("/health")
async def health():
    return {"status": "online", "website": "BezawadaDeals"}

