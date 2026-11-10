from time import perf_counter

from fastapi import FastAPI, Request, Response

from src.api import auth, events, experiments, flags, health, resolve
from src.observability import log_event
from starlette.middleware.base import RequestResponseEndpoint


app = FastAPI(
    title="Feature Flag Service",
    description="Backend-сервис для управления feature flags и A/B экспериментами",
    version="0.1.0",
)
app.state.resolve_requests = 0


@app.middleware("http")
async def observe_request(request: Request, call_next: RequestResponseEndpoint) -> Response:
    """Записать итог запроса без параметров, заголовков и тела."""
    start = perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        if route_path == "/resolve" and request.method == "POST":
            request.app.state.resolve_requests += 1
        log_event(
            "http.request", method=request.method, route=route_path,
            handler=getattr(route, "name", None), status_code=status_code,
            duration_ms=round((perf_counter() - start) * 1000, 2),
        )


app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(auth.router, tags=["auth"])
app.include_router(flags.router, prefix="/api/flags", tags=["flags"])
app.include_router(resolve.router, tags=["resolve"])
app.include_router(events.router, prefix="/api/events", tags=["events"])
app.include_router(experiments.router, prefix="/api/experiments", tags=["experiments"])


@app.get("/")
async def root():
    return {"message": "Feature Flag Service is running"}
