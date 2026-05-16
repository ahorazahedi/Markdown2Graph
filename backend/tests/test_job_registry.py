import time

from app.services.job_registry import JobRegistry, JobUpdate


def test_job_runs_and_reports_progress():
    reg = JobRegistry()

    def task(update):
        update(JobUpdate(stage="a", message="start", progress=0.1))
        update(JobUpdate(stage="b", message="mid", progress=0.5))
        update(JobUpdate(stage="c", message="end", progress=1.0))
        return {"ok": True}

    jid = reg.submit(task)
    deadline = time.time() + 5
    while time.time() < deadline:
        snap = reg.get(jid).snapshot()
        if snap["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)

    snap = reg.get(jid).snapshot()
    assert snap["status"] == "succeeded"
    assert snap["progress"] == 1.0
    assert snap["result"] == {"ok": True}
    stages = [e["stage"] for e in snap["events_tail"]]
    assert stages == ["a", "b", "c"]


def test_job_captures_failure():
    reg = JobRegistry()

    def task(_u):
        raise RuntimeError("boom")

    jid = reg.submit(task)
    deadline = time.time() + 5
    while time.time() < deadline:
        snap = reg.get(jid).snapshot()
        if snap["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.05)

    snap = reg.get(jid).snapshot()
    assert snap["status"] == "failed"
    assert "boom" in (snap["error"] or "")
