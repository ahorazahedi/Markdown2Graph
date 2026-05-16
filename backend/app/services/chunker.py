from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List

from langchain_core.documents import Document
from langchain_text_splitters import TokenTextSplitter

from ..config import get_settings


@dataclass
class Chunk:
    id: str            # sha1(text) — matches reference repo
    text: str
    position: int      # 1-based
    length: int
    content_offset: int
    file_name: str

    def to_lc_document(self) -> Document:
        return Document(
            page_content=self.text,
            metadata={
                "chunk_id": self.id,
                "position": self.position,
                "file_name": self.file_name,
            },
        )


class MarkdownChunker:
    """Token-based splitter compatible with the reference llm-graph-builder
    chunking semantics (sha1 ids, position, content_offset)."""

    def __init__(self, chunk_size: int | None = None, chunk_overlap: int | None = None):
        s = get_settings()
        # Runtime tunables (SettingsService) override env-baked defaults.
        try:
            from .settings_service import SettingsService
            svc = SettingsService()
            rt_size = svc.get("chunk_token_size")
            rt_overlap = svc.get("chunk_overlap")
        except Exception:
            rt_size = None
            rt_overlap = None
        self.chunk_size = (
            chunk_size if chunk_size is not None
            else (rt_size if rt_size else s.chunk_token_size)
        )
        self.chunk_overlap = (
            chunk_overlap if chunk_overlap is not None
            else (rt_overlap if rt_overlap is not None else s.chunk_overlap)
        )
        if self.chunk_overlap >= self.chunk_size:
            self.chunk_overlap = max(0, self.chunk_size // 4)
        self.max_total = s.max_token_chunk_size
        self._splitter = TokenTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )

    def split(self, file_name: str, text: str) -> List[Chunk]:
        pieces = self._splitter.split_text(text)
        # honor a soft cap to keep cost bounded
        max_chunks = max(1, self.max_total // max(1, self.chunk_size))
        if len(pieces) > max_chunks:
            pieces = pieces[:max_chunks]

        chunks: List[Chunk] = []
        offset = 0
        for i, piece in enumerate(pieces):
            cid = hashlib.sha1(piece.encode("utf-8")).hexdigest()
            chunks.append(
                Chunk(
                    id=cid,
                    text=piece,
                    position=i + 1,
                    length=len(piece),
                    content_offset=offset,
                    file_name=file_name,
                )
            )
            offset += len(piece)
        return chunks
