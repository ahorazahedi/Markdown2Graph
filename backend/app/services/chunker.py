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
        self.chunk_size = chunk_size or s.chunk_token_size
        self.chunk_overlap = chunk_overlap or s.chunk_overlap
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
