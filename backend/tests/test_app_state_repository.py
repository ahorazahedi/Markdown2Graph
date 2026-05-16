from app.repositories.app_state_repository import AppStateRepository


def test_schema_default_and_save(tmp_path):
    repo = AppStateRepository(db_path=str(tmp_path / "state.db"))
    s = repo.get_schema()
    assert s["node_labels"] == []
    assert s["triplets"] == []

    repo.save_schema(
        node_labels=["Disease", "Drug"],
        triplets=[["Drug", "TREATS", "Disease"]],
        extra="medical",
        source="manual",
    )
    s = repo.get_schema()
    assert s["node_labels"] == ["Disease", "Drug"]
    assert s["triplets"] == [["Drug", "TREATS", "Disease"]]
    assert s["extra"] == "medical"

    versions = repo.list_schema_versions()
    assert len(versions) == 1


def test_documents_upsert_status_counts(tmp_path):
    repo = AppStateRepository(db_path=str(tmp_path / "state.db"))
    did = repo.upsert_document(
        file_name="a.md", title="A", sha1="aaa",
        source_path="/tmp/a.md", size_bytes=42,
    )
    assert did > 0

    # upsert same name with same sha => preserves status
    repo.set_status(did, "completed")
    repo.upsert_document(file_name="a.md", title="A", sha1="aaa",
                         source_path="/tmp/a.md", size_bytes=42)
    d = repo.get_document(did)
    assert d["status"] == "completed"

    # different sha resets status to pending
    repo.upsert_document(file_name="a.md", title="A", sha1="bbb",
                         source_path="/tmp/a.md", size_bytes=50)
    d = repo.get_document(did)
    assert d["status"] == "pending"

    repo.set_counts(did, chunk_count=5, entity_count=12, relationship_count=8)
    d = repo.get_document(did)
    assert d["chunk_count"] == 5
    assert d["entity_count"] == 12
    assert d["relationship_count"] == 8

    stats = repo.stats()
    assert stats["total"] == 1
    assert stats["entities"] == 12

    assert repo.delete_document(did) is True
    assert repo.get_document(did) is None
