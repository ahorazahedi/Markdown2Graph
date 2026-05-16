from app.repositories.graph_repository import GraphRepository


def test_sanitize_label_pascal_safe():
    assert GraphRepository._sanitize_label("disease") == "Disease"
    assert GraphRepository._sanitize_label("co-morbidity") == "Co_morbidity"
    assert GraphRepository._sanitize_label("") == ""


def test_sanitize_rel_upper_snake():
    assert GraphRepository._sanitize_rel("treats") == "TREATS"
    assert GraphRepository._sanitize_rel("used to treat") == "USED_TO_TREAT"
    assert GraphRepository._sanitize_rel("a-b") == "A_B"
