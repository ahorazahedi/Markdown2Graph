from __future__ import annotations

import logging
import sys
from threading import Lock
from typing import Optional

import structlog
from neo4j import Driver, GraphDatabase

from .config import Settings


def init_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
        level=level,
        stream=sys.stdout,
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, 20)),
    )


class Neo4jManager:
    """Process-wide singleton for the Neo4j driver."""

    def __init__(self) -> None:
        self._driver: Optional[Driver] = None
        self._database: str = "neo4j"
        self._lock = Lock()

    def configure(self, settings: Settings) -> None:
        with self._lock:
            if self._driver is not None:
                return
            self._driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_username, settings.neo4j_password),
                max_connection_lifetime=3600,
            )
            self._database = settings.neo4j_database

    @property
    def driver(self) -> Driver:
        if self._driver is None:
            raise RuntimeError("Neo4j driver not configured. Call configure() first.")
        return self._driver

    @property
    def database(self) -> str:
        return self._database

    def verify(self) -> bool:
        try:
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    def close(self) -> None:
        with self._lock:
            if self._driver is not None:
                self._driver.close()
                self._driver = None


neo4j_manager = Neo4jManager()
