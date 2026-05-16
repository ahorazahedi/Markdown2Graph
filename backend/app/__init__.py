from __future__ import annotations

from flask import Flask
from flask_cors import CORS

from .config import get_settings
from .extensions import init_logging, neo4j_manager
from .api import register_blueprints
from .errors import register_error_handlers


def create_app() -> Flask:
    settings = get_settings()
    init_logging(settings.log_level)

    app = Flask(__name__)
    app.config["SETTINGS"] = settings

    CORS(
        app,
        resources={r"/api/*": {"origins": settings.cors_origins_list}},
        supports_credentials=False,
    )

    neo4j_manager.configure(settings)

    register_blueprints(app)
    register_error_handlers(app)

    @app.teardown_appcontext
    def _close(_exc):
        # Driver is process-wide; no per-request close.
        return None

    return app
