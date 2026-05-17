"""Tests for the post-processing async job API + kind-filtered job list."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import create_app
from app.repositories.app_state_repository import AppStateRepository


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_list_runs_kind_filter(tmp_path):
    repo = AppStateRepository(db_path=str(tmp_path / "state.db"))
    repo.create_run("a", kind="ingest")
    repo.create_run("b", kind="post_process")
    repo.create_run("c", kind="post_process")

    only_pp = repo.list_runs(kind="post_process")
    assert {r["id"] for r in only_pp} == {"b", "c"}

    only_ing = repo.list_runs(kind="ingest")
    assert {r["id"] for r in only_ing} == {"a"}

    # combined with status
    repo.start_run("b")
    running_pp = repo.list_runs(kind="post_process", status="running")
    assert [r["id"] for r in running_pp] == ["b"]


def test_post_process_returns_job_id(client):
    """Endpoint should submit a background job and return its id, not block
    waiting for the service to finish."""
    with patch("app.services.post_processing.PostProcessingService.run") as mock_run:
        from app.services.post_processing import PostProcessingReport
        mock_run.return_value = PostProcessingReport(elapsed_seconds=0.0)
        # disable every stage so the (mocked) call does nothing real
        resp = client.post("/api/graph/post-process", json={
            "cleanup": False, "dedup": False, "orphans": False,
            "communities": False, "summaries": False,
            "entity_embeddings": False, "community_embeddings": False,
        })
    assert resp.status_code == 200
    body = resp.get_json()
    assert "job_id" in body
    assert isinstance(body["job_id"], str) and len(body["job_id"]) > 8
    assert body["options"]["cleanup"] is False


def test_post_process_blocks_concurrent_run(client):
    """If a post_process job is already running, a second POST returns 409."""
    state = AppStateRepository()
    state.create_run("blocking-job", kind="post_process")
    state.start_run("blocking-job")

    resp = client.post("/api/graph/post-process", json={
        "cleanup": False, "dedup": False, "orphans": False,
        "communities": False, "summaries": False,
        "entity_embeddings": False, "community_embeddings": False,
    })
    assert resp.status_code == 409
    body = resp.get_json()
    assert body["job_id"] == "blocking-job"
    assert "already" in body["error"].lower()


def test_jobs_list_kind_query_param(client):
    state = AppStateRepository()
    state.create_run("ing-1", kind="ingest")
    state.create_run("pp-1", kind="post_process")
    state.create_run("pp-2", kind="post_process")

    r = client.get("/api/jobs?kind=post_process").get_json()
    assert {it["id"] for it in r["items"]} == {"pp-1", "pp-2"}
    assert all(it["kind"] == "post_process" for it in r["items"])

    r = client.get("/api/jobs?kind=ingest").get_json()
    assert [it["id"] for it in r["items"]] == ["ing-1"]
