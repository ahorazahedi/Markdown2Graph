from app.services.markdown_loader import MarkdownLoader


def test_lists_md_files(sample_md_dir):
    loader = MarkdownLoader(sample_md_dir)
    files = loader.list_files()
    assert len(files) >= 3
    assert all(p.suffix == ".md" for p in files)


def test_extracts_title_from_h1(sample_md_dir):
    loader = MarkdownLoader(sample_md_dir)
    doc = loader.load_one(sample_md_dir / "aspirin.md")
    assert doc.title == "Aspirin"
    assert doc.sha1 and len(doc.sha1) == 40
    assert "cyclooxygenase" in doc.text.lower()


def test_parses_front_matter(sample_md_dir):
    loader = MarkdownLoader(sample_md_dir)
    doc = loader.load_one(sample_md_dir / "with_frontmatter.md")
    assert doc.metadata.get("title") == "Diabetes Mellitus"
    assert doc.metadata.get("source") == "textbook"
    assert doc.title == "Diabetes Mellitus"
    assert "---" not in doc.text.splitlines()[0]
