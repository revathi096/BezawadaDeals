# AGMARKNET endpoints are implemented in app.py to avoid duplicate route registration.
# This module is intentionally kept import-safe for existing deployments.
from fastapi import APIRouter
router = APIRouter(tags=["AGMARKNET wholesale feed"])
