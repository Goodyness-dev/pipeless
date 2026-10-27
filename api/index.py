import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from fastapi import Request
from app.api.main import app

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Pipeless",
        "docs_url": "/docs",
        "health_url": "/health"
    }

@app.middleware("http")
async def vercel_path_rewrite(request: Request, call_next):
    # When Vercel rewrites to /api/index.py, the request path can include /api or /api/index.py
    path = request.scope.get("path", "")
    for prefix in ["/api/index.py", "/api/index"]:
        if path.startswith(prefix):
            new_path = path[len(prefix):] or "/"
            request.scope["path"] = new_path
            break
    return await call_next(request)
