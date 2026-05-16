from app.config import Settings


def test_cors_list_split():
    s = Settings(cors_origins="http://a, http://b ,http://c")
    assert s.cors_origins_list == ["http://a", "http://b", "http://c"]


def test_effective_llm_url_overrides_openrouter():
    s = Settings(openrouter_base_url="https://openrouter", llm_base_url="http://lm-studio:1234/v1")
    assert s.effective_llm_base_url == "http://lm-studio:1234/v1"


def test_log_level_uppercased():
    s = Settings(log_level="debug")
    assert s.log_level == "DEBUG"
