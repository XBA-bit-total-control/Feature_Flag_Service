from fastapi import FastAPI

from src.api import flags, health, resolve

app = FastAPI(
    title="Feature Flag Service",
    description="Backend-сервис для управления feature flags и A/B экспериментами",
    version="0.1.0",
)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(flags.router, prefix="/api/flags", tags=["flags"])
app.include_router(resolve.router, tags=["resolve"])


@app.get("/")
async def root():
    return {"message": "Feature Flag Service is running"}
