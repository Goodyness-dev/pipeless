import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from fastapi import Request
from app.api.main import app

@app.get("/api/debug")
def debug(request: Request):
    return {
        "scope_path": request.scope.get("path"),
        "headers": dict(request.headers),
    }

@app.middleware("http")
async def vercel_path_rewrite(request: Request, call_next):
    # Check x-matched-path or x-forwarded-uri if available
    matched = request.headers.get("x-matched-path")
    if matched and matched != request.scope.get("path"):
        request.scope["path"] = matched
    else:
        path = request.scope.get("path", "")
        for prefix in ["/api/index.py", "/api/index"]:
            if path.startswith(prefix):
                request.scope["path"] = path[len(prefix):] or "/"
                break
    return await call_next(request)
