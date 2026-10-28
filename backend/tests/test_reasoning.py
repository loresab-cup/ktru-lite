"""Optional reasoning control for models served through Yandex AI Studio."""

import providers


def test_numbered_yandex_model_sends_explicit_reasoning_override():
    env = {
        "LLM_PROVIDERS": "yandex2",
        "YANDEX2_API_KEY": "test-key",
        "YANDEX_FOLDER_ID": "test-folder",
        "YANDEX_MODEL": "deepseek-v4.1-flash/latest",
        "YANDEX_REASONING_EFFORT": "low",
        "YANDEX2_REASONING_EFFORT": "none",
    }
    [provider] = providers.load(env)
    assert providers.body(provider, "system", "[]")["reasoning_effort"] == "none"
    assert provider.model == "gpt://test-folder/deepseek-v4.1-flash/latest"


def test_unconfigured_reasoning_does_not_change_yandexgpt_request():
    [provider] = providers.load(
        {"LLM_PROVIDERS": "yandex", "YANDEX_API_KEY": "test", "YANDEX_FOLDER_ID": "folder"}
    )
    assert "reasoning_effort" not in providers.body(provider, "system", "[]")
