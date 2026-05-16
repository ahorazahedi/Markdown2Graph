from __future__ import annotations

import logging
import traceback
from flask import Flask, jsonify


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None, payload: dict | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload or {}

    def to_dict(self) -> dict:
        return {"error": self.message, "status": self.status_code, **self.payload}


class NotFoundError(AppError):
    status_code = 404


class ValidationError(AppError):
    status_code = 422


class UpstreamError(AppError):
    status_code = 502


def register_error_handlers(app: Flask) -> None:
    log = logging.getLogger(__name__)

    @app.errorhandler(AppError)
    def _app_err(e: AppError):
        log.warning("AppError: %s", e.message)
        return jsonify(e.to_dict()), e.status_code

    @app.errorhandler(404)
    def _not_found(_e):
        return jsonify({"error": "not found", "status": 404}), 404

    @app.errorhandler(Exception)
    def _unhandled(e: Exception):
        log.error("Unhandled: %s\n%s", e, traceback.format_exc())
        return jsonify({"error": "internal server error", "detail": str(e), "status": 500}), 500
