from flask import Flask

from .health import bp as health_bp
from .config_api import bp as config_bp
from .schema import bp as schema_bp
from .ingest import bp as ingest_bp
from .graph import bp as graph_bp


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(health_bp, url_prefix="/api")
    app.register_blueprint(config_bp, url_prefix="/api")
    app.register_blueprint(schema_bp, url_prefix="/api")
    app.register_blueprint(ingest_bp, url_prefix="/api")
    app.register_blueprint(graph_bp, url_prefix="/api")
