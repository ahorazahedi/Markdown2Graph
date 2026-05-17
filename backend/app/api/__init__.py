from flask import Flask

from .health import bp as health_bp
from .config_api import bp as config_bp
from .schema import bp as schema_bp
from .documents import bp as documents_bp
from .ingest import bp as ingest_bp
from .graph import bp as graph_bp
from .llm_calls import bp as llm_calls_bp
from .upload import bp as upload_bp  # legacy: kept for back-compat / external callers
from .prompts_api import bp as prompts_bp
from .jobs import bp as jobs_bp
from .settings_api import bp as settings_bp
from .runtime_settings import bp as runtime_bp
from .chat_api import bp as chat_bp
from .embeddings_api import bp as embeddings_bp


def register_blueprints(app: Flask) -> None:
    app.register_blueprint(health_bp, url_prefix="/api")
    app.register_blueprint(config_bp, url_prefix="/api")
    app.register_blueprint(schema_bp, url_prefix="/api")
    app.register_blueprint(documents_bp, url_prefix="/api")
    app.register_blueprint(ingest_bp, url_prefix="/api")
    app.register_blueprint(graph_bp, url_prefix="/api")
    app.register_blueprint(llm_calls_bp, url_prefix="/api")
    app.register_blueprint(upload_bp, url_prefix="/api")
    app.register_blueprint(prompts_bp, url_prefix="/api")
    app.register_blueprint(jobs_bp, url_prefix="/api")
    app.register_blueprint(settings_bp, url_prefix="/api")
    app.register_blueprint(runtime_bp, url_prefix="/api")
    app.register_blueprint(chat_bp, url_prefix="/api")
    app.register_blueprint(embeddings_bp, url_prefix="/api")
