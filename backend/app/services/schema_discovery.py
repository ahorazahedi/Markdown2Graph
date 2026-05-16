from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import List, Optional

from ..config import get_settings
from .markdown_loader import MarkdownLoader

log = logging.getLogger(__name__)


class SchemaDiscoveryService:
    """Sample N markdown files, ask the LLM to propose node labels +
    relationship triplets. The user reviews/edits before extraction."""

    def __init__(self):
        self.settings = get_settings()

    def discover(
        self,
        files: List[Path],
        sample_size: Optional[int] = None,
        extra_instructions: Optional[str] = None,
    ) -> dict:
        size = sample_size or self.settings.schema_discovery_sample_size
        size = min(size, len(files))
        sample = random.sample(files, size) if size < len(files) else files

        loader = MarkdownLoader(files[0].parent)
        docs = loader.load_many(sample)

        budget = self.settings.schema_discovery_max_chars
        per_doc = max(500, budget // max(1, len(docs)))
        excerpts = []
        for d in docs:
            excerpt = d.text[:per_doc].strip()
            if excerpt:
                excerpts.append(f"## File: {d.file_name}\n\n{excerpt}")
        joined = "\n\n---\n\n".join(excerpts)

        from langchain_core.messages import HumanMessage, SystemMessage

        from ..llm import build_chat_llm, with_tag
        from ..prompts import SCHEMA_DISCOVERY_SYSTEM

        sys_prompt = SCHEMA_DISCOVERY_SYSTEM
        if extra_instructions:
            sys_prompt += f"\n\nAdditional user guidance:\n{extra_instructions.strip()}"

        llm = build_chat_llm()
        log.info("schema_discovery: sampling %d/%d files (%d chars)", len(docs), len(files), len(joined))
        with with_tag("schema_discovery"):
            resp = llm.invoke(
                [SystemMessage(content=sys_prompt), HumanMessage(content=joined)]
            )
        raw = resp.content if hasattr(resp, "content") else str(resp)

        parsed = self._parse_json(raw)
        node_labels = self._dedup(parsed.get("node_labels") or parsed.get("nodes") or [])
        triplets = self._dedup(parsed.get("triplets") or [])

        # Validate: triplet endpoints must be in node_labels.
        valid_triplets = []
        label_set = set(node_labels)
        for t in triplets:
            try:
                src, mid = t.split("-", 1)
                rel, dst = mid.split("->", 1)
                src, rel, dst = src.strip(), rel.strip(), dst.strip()
                if src in label_set and dst in label_set and rel:
                    valid_triplets.append(f"{src}-{rel}->{dst}")
            except ValueError:
                continue

        return {
            "node_labels": node_labels,
            "triplets": valid_triplets,
            "sampled_files": [d.file_name for d in docs],
        }

    @staticmethod
    def _parse_json(raw: str) -> dict:
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        # locate first { and last }
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"LLM returned no JSON object: {raw[:200]}")
        return json.loads(raw[start : end + 1])

    @staticmethod
    def _dedup(seq: List[str]) -> List[str]:
        seen, out = set(), []
        for s in seq:
            if not isinstance(s, str):
                continue
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out
