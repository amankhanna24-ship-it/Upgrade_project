"""Section-aware chunking for the policy documents.

Policy text is split on `## ` headings so that every chunk is one rule
(e.g. RET-3 "Items damaged on arrival"). Each chunk carries a breadcrumb
("Returns Policy > RET-3 Items damaged on arrival") at the top of the
embedded text, which helps the embedding keep its context. Sections that are
longer than `max_chars` are split on paragraph boundaries with a one-paragraph
overlap.

Section codes (RET-3, NC-2 ...) become chunk IDs, which gives the RAG
benchmark exact ground truth to score against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

POLICY_DIR = Path(__file__).resolve().parent.parent / "data" / "policies"
SECTION_RE = re.compile(r"^##\s+(?P<code>[A-Z]{2,4}-\d+)\s+(?P<title>.+)$", re.MULTILINE)


@dataclass
class Chunk:
    id: str
    doc: str
    doc_title: str
    section: str
    title: str
    text: str

    @property
    def embed_text(self) -> str:
        return f"{self.doc_title} > {self.section} {self.title}\n\n{self.text}"

    def metadata(self) -> dict[str, str]:
        return {
            "doc": self.doc,
            "doc_title": self.doc_title,
            "section": self.section,
            "title": self.title,
            "text": self.text,
        }


def chunk_markdown(path: Path, max_chars: int = 1200) -> list[Chunk]:
    raw = path.read_text(encoding="utf-8")
    title_match = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
    doc_title = title_match.group(1).strip() if title_match else path.stem
    matches = list(SECTION_RE.finditer(raw))
    chunks: list[Chunk] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        body = raw[m.end() : end].strip()
        parts = _split(body, max_chars)
        for j, part in enumerate(parts):
            cid = m["code"] if len(parts) == 1 else f"{m['code']}#{j + 1}"
            chunks.append(Chunk(cid, path.stem, doc_title, m["code"], m["title"].strip(), part))
    return chunks


def _split(body: str, max_chars: int) -> list[str]:
    if len(body) <= max_chars:
        return [body]
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    out: list[str] = []
    current: list[str] = []
    for p in paras:
        if current and sum(len(x) for x in current) + len(p) > max_chars:
            out.append("\n\n".join(current))
            current = current[-1:]  # one-paragraph overlap
        current.append(p)
    if current:
        out.append("\n\n".join(current))
    return out


def load_policy_chunks(directory: Path = POLICY_DIR) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        chunks.extend(chunk_markdown(path))
    return chunks


if __name__ == "__main__":
    for c in load_policy_chunks():
        print(f"{c.id:8} {c.doc:28} {len(c.text):5} chars  {c.title}")
