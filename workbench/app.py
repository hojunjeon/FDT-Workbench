"""Coaching-first application, with the numeric workbench kept as an expert view."""
from pathlib import Path
from fastapi.responses import FileResponse

# Keep the existing API/security implementation intact and import-compatible.
from ._numeric_app import LocalGuard, MAX_BODY, MAX_CSV, create_app as create_numeric_app
from .coaching import install_coaching


def create_app(data_dir: Path | None = None, port: int = 8765, *, test_hosts: set[str] | None = None):
    app = create_numeric_app(data_dir, port, test_hosts=test_hosts)
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'path', None) != '/']
    static = Path(__file__).resolve().parent / 'static'

    @app.get('/')
    async def home():
        return FileResponse(static / 'coach.html', media_type='text/html')

    @app.get('/workbench')
    async def workbench():
        return FileResponse(static / 'index.html', media_type='text/html')

    install_coaching(app)
    return app
