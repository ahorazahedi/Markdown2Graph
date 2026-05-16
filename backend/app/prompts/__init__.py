"""Prompt templates for the medical-domain knowledge-graph pipeline.

Adapted from neo4j-labs/llm-graph-builder constants and tuned for medical
textbook content (anatomy, physiology, pathology, pharmacology, clinical
guidelines).
"""
from pathlib import Path

_DIR = Path(__file__).parent


def load(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


SCHEMA_DISCOVERY_SYSTEM = load("schema_discovery_system.md")
ENTITY_EXTRACTION_INSTRUCTIONS = load("entity_extraction_instructions.md")
GRAPH_CLEANUP_SYSTEM = load("graph_cleanup_system.md")

__all__ = [
    "load",
    "SCHEMA_DISCOVERY_SYSTEM",
    "ENTITY_EXTRACTION_INSTRUCTIONS",
    "GRAPH_CLEANUP_SYSTEM",
]
