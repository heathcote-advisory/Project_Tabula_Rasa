"""FastAPI factory, process lifecycle and safe request instrumentation."""

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from anyio import CapacityLimiter, to_thread
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse
from .static import HashedAssets
from .alpha import PUBLIC_PROBES, gate_response

from tabula_rasa.telemetry import Telemetry, TelemetrySettings, configure_console
from tabula_rasa.errors import StorageError
from tabula_rasa.application.mileage import MileageValidationError
from tabula_rasa.application.documents import DocumentError
from tabula_rasa.application.identity import IdentityError
from .composition import PostgresRuntime, open_runtime
from .config import HttpSettings
from .authorization import PUBLIC_ROUTES, ROUTE_POLICIES, authorize_request, validate_routes
from .errors import (
    ERRORS, TransportError, error_response, handle_error, handle_http_error, handle_validation,
    mark_identity_storage_failure, mark_postgres_failure,
)
from .models.common import Health, Version
from .routes import accounting, accounts, auth, documents, financial_csv, imports, mileage, mileage_imports, references, reporting, secondary, session
from .routes import connections, customer_export, customer_lifecycle, maps_quota, periods, support

logger = logging.getLogger("tabula_rasa.http")


def create_app(*, settings=None, runtime_factory=None, schema_only=False, static_dir=None):
    """Compose server identity/runtime factories; schema export skips runtime access."""
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings or HttpSettings.from_environment()
        app.state.alpha_workers = CapacityLimiter(2)
        factory = runtime_factory or open_runtime
        if not logger.hasHandlers():
            configure_console()
        from fastapi.routing import iter_route_contexts
        app.state.telemetry = Telemetry(
            app.state.settings.environment, app.state.settings.release_id,
            TelemetrySettings.from_environment(),
            routes={route.path for route in iter_route_contexts(app.routes)} | {"/assets/{path:path}"},
            operations=ROUTE_POLICIES.keys() | PUBLIC_ROUTES.keys(),
            actions={policy.action for policy in ROUTE_POLICIES.values()}, failure_codes=ERRORS,
        )
        try:
            try:
                app.state.runtime = await run_in_threadpool(factory, app.state.settings)
            except Exception:
                app.state.telemetry.record("runtime", "startup", failure_code="unavailable",
                                           provider_status="unavailable")
                raise
            app.state.telemetry.record("runtime", "startup", provider_status="ok")
            try:
                yield
            finally:
                await run_in_threadpool(app.state.runtime.close)
        finally:
            await run_in_threadpool(app.state.telemetry.close)

    logging.getLogger("uvicorn.access").disabled = True
    app = FastAPI(
        title="Tabula Rasa commercial API", version="1.0.0",
        lifespan=None if schema_only else lifespan,
        docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
        swagger_ui_oauth2_redirect_url=None,
        dependencies=[Depends(authorize_request)],
    )
    # Capture only FastAPI's generated endpoints before registering app routes.
    app.state.framework_documentation_endpoints = {
        route.path: route.endpoint for route in app.routes
        if route.path in {app.docs_url, app.openapi_url}
    }
    app.add_exception_handler(TransportError, handle_error)
    app.add_exception_handler(StorageError, handle_error)
    app.add_exception_handler(MileageValidationError, handle_error)
    app.add_exception_handler(DocumentError, handle_error)
    async def identity_error(request, error):
        mark_identity_storage_failure(request, error)
        response = error_response(request, error.code)
        request.state.security_event = getattr(request.state, "security_event", None) or "identity_denied"
        request.state.security_reason = getattr(request.state, "security_reason", None) or "identity"
        # Passive denials must not delete cookies by name: a delayed response
        # could erase credentials issued by a newer successful callback.
        # Server validation denies stale credentials; explicit logout clears.
        return response
    app.add_exception_handler(IdentityError, identity_error)
    app.add_exception_handler(RequestValidationError, handle_validation)
    app.add_exception_handler(HTTPException, handle_http_error)

    @app.middleware("http")
    async def safe_request(request: Request, call_next):
        started = perf_counter()
        request.state.request_id = uuid4().hex
        # Never export caller-controlled headers verbatim, even when token-shaped.
        request.state.correlation_id = request.headers.get("x-correlation-id", "")[:1025]
        try:
            configured = getattr(app.state, "settings", None)
            if (configured and request.url.path not in {"/health/live", "/health/ready", "/version"}
                    and (request.headers.get("host") or "").lower() != configured.public_origin.split("://", 1)[1].lower()):
                response = error_response(request, "forbidden")
            else:
                if configured and configured.hosted_alpha and request.url.path not in PUBLIC_PROBES:
                    response = await to_thread.run_sync(
                        gate_response, request, configured, limiter=app.state.alpha_workers,
                    )
                else:
                    response = gate_response(request, configured) if configured else None
                if response is None and (request.url.path in {"/api/docs", "/api/openapi.json"}
                        or (configured and configured.hosted_alpha and request.method not in {"GET", "HEAD", "OPTIONS"})):
                    from .context import resolve_context
                    await run_in_threadpool(resolve_context, request)
                if response is None:
                    response = await call_next(request)
        except IdentityError as error:
            response = await identity_error(request, error)
        except TransportError as error:
            response = error_response(request, error.code)
        except Exception:
            # Do not emit exception repr/tracebacks: they may contain SQL or draft values.
            response = error_response(request, "unexpected")
        response.headers["X-Request-ID"] = request.state.request_id
        if configured and configured.hosted_alpha:
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers.setdefault("Cache-Control", "no-store")
        route = request.scope.get("route")
        operation = getattr(route, "operation_id", None) or ("static_assets" if getattr(route, "path", "").startswith("/assets") else "unmatched")
        if operation == 'complete_drive_authorization':
            # Includes denied callbacks that never reach the route's redirect.
            response.headers['Referrer-Policy'] = 'no-referrer'
        policy = ROUTE_POLICIES.get(operation)
        failure = getattr(request.state, "failure_code", None)
        if response.status_code >= 500 and failure is None:
            failure = "unavailable" if response.status_code == 503 else "unexpected"
        provider = getattr(request.state, "telemetry_provider", None) or (
            "identity" if operation in {"begin_login", "begin_registration", "complete_login", "logout", "revoke_all_sessions"} else
            "postgresql" if operation == "readiness" else "application"
        )
        context = getattr(request.state, "telemetry_context", None)
        session = getattr(request.state, "named_session", None)
        app.state.telemetry.record("http_request", operation,
            request_id=request.state.request_id, correlation=request.state.correlation_id,
            route=getattr(route, "path", None), method=request.method, status=response.status_code,
            duration_ms=(perf_counter() - started) * 1000, failure_code=failure,
            security_event=getattr(request.state, "security_event", None),
            security_reason=getattr(request.state, "security_reason", None),
            login_request_id=getattr(request.state, "login_request_id", None),
            action=policy.action if policy else None, context=context,
            actor_id=getattr(session, "user_id", None), provider=provider,
            provider_status=getattr(request.state, "provider_status", None) or (
                "unavailable" if failure in {"unavailable", "unexpected"} else "ok"),
        )
        return response

    @app.get("/health/live", response_model=Health, tags=["Runtime"], operation_id="liveness")
    def live():
        return Health(status="live")

    @app.get("/health/ready", response_model=Health, responses={503: {"model": Health}},
             tags=["Runtime"], operation_id="readiness")
    def ready(request: Request):
        from starlette.responses import JSONResponse
        try:
            runtime = request.app.state.runtime
            if isinstance(runtime, PostgresRuntime):
                failure = runtime.readiness_error()
                healthy = failure is None
                if failure == "database_exhausted":
                    mark_postgres_failure(request, exhausted=True)
            else:
                healthy = runtime.ready()
        except Exception:
            healthy = False
        return JSONResponse({"status": "ready" if healthy else "unavailable"}, status_code=200 if healthy else 503)

    @app.get("/version", response_model=Version, tags=["Runtime"], operation_id="version")
    def version(request: Request):
        return Version(release=request.app.state.settings.release_id)

    app.include_router(session.router)
    app.include_router(maps_quota.router)
    from .routes import workspaces
    app.include_router(workspaces.router)
    app.include_router(auth.router)
    app.include_router(accounts.router)
    app.include_router(references.categories)
    app.include_router(references.payees)
    app.include_router(references.tags)
    app.include_router(accounting.transactions)
    app.include_router(accounting.journals)
    app.include_router(mileage.router)
    app.include_router(imports.router)
    app.include_router(mileage_imports.router)
    app.include_router(documents.router)
    app.include_router(connections.router)
    app.include_router(connections.callback_router)
    app.include_router(financial_csv.router)
    app.include_router(customer_export.router)
    app.include_router(customer_lifecycle.router)
    app.include_router(reporting.router)
    app.include_router(secondary.router)
    app.include_router(periods.router)
    app.include_router(support.router)
    assets = Path(static_dir) if static_dir is not None else Path(__file__).resolve().parents[2] / "web" / "dist"
    if not schema_only and assets.is_dir():
        @app.api_route('/assets', methods=['GET', 'HEAD'], include_in_schema=False, operation_id='asset_register')
        def asset_register(request: Request):
            if 'text/html' not in request.headers.get('accept', ''):
                raise HTTPException(404)
            return FileResponse(assets / 'index.html', headers={'Cache-Control': 'no-cache'})

        app.mount("/assets", HashedAssets(directory=assets / "assets", asset_page_index=assets / 'index.html'), name="assets")

        @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False, operation_id='browser_shell')
        def browser(request: Request, path: str):
            if (path.split("/", 1)[0] in {"api", "health", "version", "assets"}
                    or "." in path or "text/html" not in request.headers.get("accept", "")):
                raise HTTPException(404)
            return FileResponse(assets / "index.html", headers={"Cache-Control": "no-cache"})

    validate_routes(app)
    return app
