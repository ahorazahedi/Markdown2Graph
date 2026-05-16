from app.services.chunker import MarkdownChunker
from app.services.markdown_loader import MarkdownLoader


def test_chunker_produces_stable_ids(sample_md_dir):
    chunker = MarkdownChunker(chunk_size=64, chunk_overlap=8)
    doc = MarkdownLoader(sample_md_dir).load_one(sample_md_dir / "aspirin.md")
    a = chunker.split(doc.file_name, doc.text)
    b = chunker.split(doc.file_name, doc.text)
    assert [c.id for c in a] == [c.id for c in b]
    assert all(len(c.id) == 40 for c in a)


def test_chunker_positions_and_offsets(sample_md_dir):
    chunker = MarkdownChunker(chunk_size=64, chunk_overlap=0)
    doc = MarkdownLoader(sample_md_dir).load_one(sample_md_dir / "myocardial_infarction.md")
    chunks = chunker.split(doc.file_name, doc.text)
    assert chunks[0].position == 1
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.position == prev.position + 1
    assert chunks[0].file_name == doc.file_name
