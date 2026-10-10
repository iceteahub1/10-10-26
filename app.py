"""Phase 5 local dashboard using the shared AI, FSM and SQLite services."""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from bistro.config import load_settings
from bistro.paths import ROOT
from bistro.browser_api import BrowserAPI, APIError, install_routes


def create_app(*, settings=None, db_path=None, client_factory=None, acceptance_log_path=None):
    settings = settings or load_settings()
    api = BrowserAPI(settings, db_path, client_factory, acceptance_log_path)

    @asynccontextmanager
    async def lifespan(app):
        api.events.start_run()
        try:
            api.initialize()
        except Exception:
            api.events.record('workflow_error', code='application_failure', stage='startup')
            raise
        # Resolve interrupted durable claims without replaying any saved AI effects.
        from bistro.database import connection
        from bistro.conversation_store import encode
        with connection(api.db_path, write=True) as con:
            con.execute('UPDATE browser_requests SET result_json=? WHERE result_json IS NULL', (encode({
                'ok': False, 'code': 'request_interrupted', 'message': 'This request was interrupted. Refresh your cart to check saved changes before trying a new request.', 'http_status': 409}),))
        try:
            api.ai_status()
            api.menu()
        except Exception:
            api.events.record('workflow_error', code='application_failure', stage='startup')
        yield

    app = FastAPI(title='Perpetual Bistro', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.browser_api = api

    @app.middleware('http')
    async def local_security(request: Request, call_next):
        # Exact loopback Host/Origin checking prevents DNS rebinding and cross-site writes.
        valid_hosts = {f'127.0.0.1:{settings.port}', f'localhost:{settings.port}'}
        if settings.port == 80:
            valid_hosts |= {'127.0.0.1', 'localhost'}
        if request.headers.get('host') not in valid_hosts:
            api.events.record('workflow_error', code='forbidden', stage='request')
            return JSONResponse({'ok': False, 'code': 'forbidden', 'message': 'Use the local dashboard address.'}, status_code=403)
        if request.method not in ('GET', 'HEAD') and request.headers.get('origin') != 'http://' + request.headers['host']:
            api.events.record('workflow_error', code='forbidden', stage='request')
            return JSONResponse({'ok': False, 'code': 'forbidden', 'message': 'Open this request from the local dashboard.'}, status_code=403)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        api.events.record('workflow_error', code=exc.code, stage='request')
        return JSONResponse({'ok': False, 'code': exc.code, 'message': str(exc)}, status_code=exc.status)

    @app.exception_handler(Exception)
    async def failure(request, exc):
        api.events.record('workflow_error', code='application_failure', stage='request')
        import logging
        logging.getLogger(__name__).error('Dashboard failure', exc_info=exc)
        return JSONResponse({'ok': False, 'code': 'application_failure', 'message': 'Application failure. Check logs/application.log and refresh your cart.'}, status_code=500)

    app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
    @app.get('/')
    def dashboard():
        return FileResponse(ROOT / 'templates/index.html')
    @app.get('/health')
    def health():
        return {'project': 'perpetual-bistro', 'phase': 5, 'status': 'ready',
                'launch_token': os.getenv('BISTRO_LAUNCH_TOKEN', ''), 'browser_ai_chat': True,
                'ai_available': os.getenv('BISTRO_AI_AVAILABLE') == '1', 'ai_model': settings.model,
                'ai_reason': os.getenv('BISTRO_AI_REASON', 'Check AI status in the dashboard.')}
    install_routes(app, api)
    return app


app = create_app()
