from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List


_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


@dataclass
class MarkdownDoc:
    path: Path
    file_name: str
    text: str
    sha1: str
    title: str | None = None
    metadata: dict = field(default_factory=dict)

    @property
    def length(self) -> int:
        return len(self.text)


class MarkdownLoader:
    """Lightweight loader for a folder of `.md` files.

    Strips YAML front matter into `metadata`, captures the first H1 as title,
    and computes a stable SHA-1 over the raw bytes for deduplication.
    """

    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()

    def list_files(self) -> List[Path]:
        if not self.root.is_dir():
            return []
        return sorted(p for p in self.root.rglob("*.md") if p.is_file())

    def load_one(self, path: Path) -> MarkdownDoc:
        raw = path.read_bytes()
        sha1 = hashlib.sha1(raw).hexdigest()
        text = raw.decode("utf-8", errors="replace")

        metadata: dict = {}
        m = _FRONT_MATTER_RE.match(text)
        if m:
            metadata = self._parse_front_matter(m.group(1))
            text = text[m.end():]

        title = metadata.get("title") or self._first_heading(text)
        return MarkdownDoc(
            path=path,
            file_name=path.name,
            text=text,
            sha1=sha1,
            title=title,
            metadata=metadata,
        )

    def load_many(self, paths: Iterable[Path]) -> List[MarkdownDoc]:
        return [self.load_one(p) for p in paths]

    @staticmethod
    def _parse_front_matter(block: str) -> dict:
        out: dict = {}
        for line in block.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
        return out

    @staticmethod
    def _first_heading(text: str) -> str | None:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("# "):
                return line.lstrip("#").strip()
        return None
