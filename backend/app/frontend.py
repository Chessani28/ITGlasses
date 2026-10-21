"""Serve the dependency-free local testing interface from the existing API."""
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from backend.app.core.config import PROJECT_ROOT


def mount_frontend(app):
    directory = PROJECT_ROOT / "frontend"
    app.mount("/ui", StaticFiles(directory=directory), name="frontend")

    @app.get("/", include_in_schema=False)
    async def home():
        return FileResponse(directory / "index.html", media_type="text/html")

    @app.middleware("http")
    async def frontend_headers(request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/ui/"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "media-src 'self' blob:; connect-src 'self'; img-src 'self'; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            )
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
        return response
