from app.repositories.llm_call_repository import LLMCallRepository


def test_insert_success_and_query(tmp_path):
    repo = LLMCallRepository(db_path=str(tmp_path / "calls.db"))
    cid = repo.insert_pending(
        created_at="2026-05-16T10:00:00+00:00",
        tag="schema_discovery",
        model="google/gemini-2.5-flash",
        base_url="https://openrouter.ai/api/v1",
        provider="openrouter",
        request_json={"messages": [{"type": "human", "content": "hi"}]},
    )
    repo.mark_success(
        cid,
        finished_at="2026-05-16T10:00:01+00:00",
        latency_ms=250,
        response_text="hello",
        response_json=[[{"text": "hello"}]],
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )
    item = repo.get(cid)
    assert item["status"] == "success"
    assert item["tag"] == "schema_discovery"
    assert item["total_tokens"] == 15
    assert item["request_json"]["messages"][0]["content"] == "hi"

    listing = repo.list(tag="schema_discovery")
    assert len(listing) == 1
    assert listing[0]["id"] == cid

    assert repo.distinct_tags() == ["schema_discovery"]
    assert repo.count() == 1


def test_insert_error(tmp_path):
    repo = LLMCallRepository(db_path=str(tmp_path / "calls.db"))
    cid = repo.insert_pending(
        created_at="t0", tag="entity_extraction", model="m", base_url="u", provider="p",
        request_json={"prompts": ["p"]},
    )
    repo.mark_error(cid, finished_at="t1", latency_ms=50, error="RuntimeError: boom")
    item = repo.get(cid)
    assert item["status"] == "error"
    assert "boom" in item["error"]


def test_truncates_huge_bodies(tmp_path, monkeypatch):
    from app.config import get_settings
    s = get_settings()
    monkeypatch.setattr(s, "llm_log_max_body_chars", 100)
    repo = LLMCallRepository(db_path=str(tmp_path / "calls.db"))
    cid = repo.insert_pending(
        created_at="t0", tag="x", model=None, base_url=None, provider=None,
        request_json={"messages": [{"content": "a" * 5000}]},
    )
    item = repo.get(cid)
    assert "[truncated" in (item["request_json"] if isinstance(item["request_json"], str)
                             else str(item["request_json"]))
