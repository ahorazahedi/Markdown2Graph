from __future__ import annotations

import logging
from typing import List, Tuple

from langchain_core.documents import Document
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_experimental.graph_transformers.llm import _Graph

from ..llm import build_chat_llm
from .prompt_store import PromptStore

log = logging.getLogger(__name__)


class EntityExtractor:
    """Wraps LangChain's LLMGraphTransformer with our medical-domain prompt
    and the (optional) user-approved schema."""

    def __init__(
        self,
        allowed_nodes: List[str] | None = None,
        allowed_relationships: List[Tuple[str, str, str]] | None = None,
        extra_instructions: str | None = None,
    ):
        self.llm = build_chat_llm()

        # Detect structured-output support so we can opt into rich properties.
        try:
            self.llm.with_structured_output(_Graph)
            supports = True
        except Exception:
            supports = False

        node_props = ["description"] if supports else False
        rel_props = ["description"] if supports else False

        instructions = PromptStore().render(
            "entity_extraction_instructions",
            allowed_nodes=allowed_nodes or [],
            allowed_relationships=allowed_relationships or [],
            extra_instructions=self._sanitize(extra_instructions) if extra_instructions else "",
        )

        self.transformer = LLMGraphTransformer(
            llm=self.llm,
            allowed_nodes=allowed_nodes or [],
            allowed_relationships=allowed_relationships or [],
            node_properties=node_props,
            relationship_properties=rel_props,
            ignore_tool_usage=not supports,
            additional_instructions=instructions,
        )

    async def extract_async(self, docs: List[Document]):
        from ..llm import with_tag
        with with_tag("entity_extraction"):
            return await self.transformer.aconvert_to_graph_documents(docs)

    def extract(self, docs: List[Document]):
        from ..llm import with_tag
        with with_tag("entity_extraction"):
            return self.transformer.convert_to_graph_documents(docs)

    @staticmethod
    def _sanitize(text: str) -> str:
        # block prompt-injection-y bits, mirror reference repo
        import re

        text = text.replace("{", "[").replace("}", "]")
        for pat in (r"os\.getenv\(", r"eval\(", r"exec\(", r"subprocess\.", r"import os", r"import subprocess"):
            text = re.sub(pat, "[BLOCKED]", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()
