from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from podify.config import APP_DESCRIPTION, APP_TITLE, STATIC_DIR
from podify.routes.access import router as access_router
from podify.routes.admin import router as admin_router
from podify.routes.public import router as public_router
from podify.security import RateLimitMiddleware, SecurityHeadersMiddleware

app = FastAPI(
    title=APP_TITLE,
    description=APP_DESCRIPTION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost",
        "http://localhost:5000",
        "http://127.0.0.1:5000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "X-Admin-Token"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(public_router)
app.include_router(access_router)
app.include_router(admin_router)
