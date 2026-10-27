from app.api.main import app

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "Pipeless",
        "docs_url": "/docs",
        "health_url": "/health",
        "demo_url": "/demo"
    }
