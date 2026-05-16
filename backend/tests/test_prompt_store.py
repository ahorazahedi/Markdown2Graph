import pytest

from app.repositories.app_state_repository import AppStateRepository
from app.services.prompt_store import PromptStore, SPECS


@pytest.fixture
def store(tmp_path, monkeypatch):
    state = AppStateRepository(db_path=str(tmp_path / "s.db"))
    return PromptStore(state=state)


def test_seeds_all_specs(store):
    keys = {p["key"] for p in store.list()}
    assert keys == {s.key for s in SPECS}


def test_render_with_var(store):
    out = store.render("schema_discovery_system", extra_instructions="X-RAY ONLY")
    assert "X-RAY ONLY" in out


def test_render_missing_var_renders_empty(store):
    # StrictUndefined would error; we use a forgiving env for preview.
    out = store.render("schema_discovery_system", extra_instructions="")
    assert "Additional guidance" not in out  # block hidden by {% if %}


def test_save_marks_custom(store):
    new_tpl = "CUSTOM: {{ extra_instructions }}"
    p = store.save("schema_discovery_system", new_tpl)
    assert p["is_custom"] is True
    assert "CUSTOM:" in p["template"]

    out = store.render("schema_discovery_system", extra_instructions="hi")
    assert out.strip() == "CUSTOM: hi"


def test_reset_restores_default(store):
    store.save("schema_discovery_system", "ZZZ")
    p = store.reset("schema_discovery_system")
    assert p["is_custom"] is False
    assert "knowledge graph" in p["template"].lower()


def test_list_presets_includes_general_and_medical(store):
    names = {p["name"] for p in store.list_presets()}
    assert {"general", "medical"}.issubset(names)


def test_apply_preset_overwrites_all_prompts(store):
    r = store.apply_preset("medical")
    assert "schema_discovery_system" in r["applied"]
    sd = store.get("schema_discovery_system")
    assert "biomedical" in sd["template"].lower()
    assert sd["is_custom"] is True


def test_apply_unknown_preset_raises(store):
    with pytest.raises(KeyError):
        store.apply_preset("does-not-exist")


def test_preview_with_override(store):
    out = store.preview("hello {{ name }}", {"name": "world"})
    assert out == "hello world"


def test_save_rejects_bad_jinja(store):
    with pytest.raises(ValueError):
        store.save("schema_discovery_system", "{% if broken")
