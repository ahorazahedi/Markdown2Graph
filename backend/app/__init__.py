"""Flask app factory.

Imports inside `create_app` are intentionally lazy so that test modules can
import `app.config`, `app.services.*`, etc. without dragging the whole
LangChain / Neo4j stack into the import graph.
"""
from __future__ import annotations


def create_app():
    from flask import Flask
    from flask_cors import CORS

    from .api import register_blueprints
    from .config import get_settings
    from .errors import register_error_handlers
    from .extensions import init_logging, neo4j_manager

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
    return app
