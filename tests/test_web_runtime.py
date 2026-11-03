from pathlib import Path

import pytest
from streamlit.errors import StreamlitSecretNotFoundError
from streamlit.testing.v1 import AppTest

from textlab import web_runtime
from textlab.transport import ConfigurationError


@pytest.fixture(autouse=True)
def clean_runtime_environment(monkeypatch):
    for name in web_runtime._SECRET_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(web_runtime.st, "secrets", {})


def test_only_known_secret_roots_are_loaded_and_existing_env_wins(monkeypatch, capsys):
    monkeypatch.setenv("LLM_API_KEY", "already-configured-private-value")
    monkeypatch.setattr(web_runtime.st, "secrets", {
        "LLM_API_KEY": "ignored-secret-value", "GPTZERO_API_KEY": "private-api-value",
        "TEXTLAB_HOSTED": True, "TEXTLAB_MAX_CONCURRENT_RUNS": 3,
        "unknown_key": "should-not-be-loaded", "nested": {"LLM_API_KEY": "nested-value"},
    })
    web_runtime._load_secret_defaults()
    import os
    assert os.environ["LLM_API_KEY"] == "already-configured-private-value"
    assert os.environ["GPTZERO_API_KEY"] == "private-api-value"
    assert os.environ["TEXTLAB_HOSTED"] == "true"
    assert os.environ["TEXTLAB_MAX_CONCURRENT_RUNS"] == "3"
    assert "unknown_key" not in os.environ
    assert "nested" not in os.environ
    assert capsys.readouterr() == ("", "")


def test_preserves_env_even_when_streamlit_parser_exports_root_values(monkeypatch):
    import os

    class ExportingSecrets:
        def __getitem__(self, name):
            os.environ["LLM_API_KEY"] = "streamlit-overwrite"
            if name == "LLM_API_KEY":
                return "streamlit-overwrite"
            raise KeyError(name)

    monkeypatch.setenv("LLM_API_KEY", "existing-value")
    monkeypatch.setattr(web_runtime.st, "secrets", ExportingSecrets())
    web_runtime._load_secret_defaults()
    assert os.environ["LLM_API_KEY"] == "existing-value"


@pytest.mark.parametrize("name", ["LLM_API_KEY", "GPTZERO_API_KEY"])
@pytest.mark.parametrize("blank", ["", " \t\n "])
def test_blank_api_environment_values_are_filled_from_root_secrets(monkeypatch, capsys, name, blank):
    import os

    monkeypatch.setenv(name, blank)
    monkeypatch.setattr(web_runtime.st, "secrets", {name: "fake-configured-api-key"})

    web_runtime._load_secret_defaults()

    assert os.environ[name] == "fake-configured-api-key"
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("existing_name", ["LLM_API_KEY", "GPTZERO_API_KEY"])
@pytest.mark.parametrize("blank", ["", " \t\n "])
def test_parser_exports_fill_blank_api_values_and_preserve_valid_bindings(monkeypatch, capsys, existing_name, blank):
    import os

    secrets = {"LLM_API_KEY": "fake-secret-llm-key", "GPTZERO_API_KEY": "fake-secret-detector-key"}
    blank_name = next(name for name in secrets if name != existing_name)

    class ExportingSecrets:
        parsed = False

        def __getitem__(self, name):
            if not self.parsed:
                # Streamlit exports all root scalars during its initial parse.
                os.environ.update(secrets)
                self.parsed = True
            return secrets[name]

    monkeypatch.setenv(existing_name, "fake-existing-runtime-key")
    monkeypatch.setenv(blank_name, blank)
    monkeypatch.setattr(web_runtime.st, "secrets", ExportingSecrets())

    web_runtime._load_secret_defaults()

    assert os.environ[existing_name] == "fake-existing-runtime-key"
    assert os.environ[blank_name] == secrets[blank_name]
    assert capsys.readouterr() == ("", "")


def test_missing_secrets_file_is_optional(monkeypatch):
    class MissingSecrets:
        def __getitem__(self, name):
            raise StreamlitSecretNotFoundError("No secrets found. Valid paths: []")

    monkeypatch.setattr(web_runtime.st, "secrets", MissingSecrets())
    web_runtime._load_secret_defaults()


def test_malformed_secrets_file_is_not_silently_ignored(monkeypatch):
    class MalformedSecrets:
        def __getitem__(self, name):
            raise StreamlitSecretNotFoundError("may contain secret values") from ValueError("private-value")

    monkeypatch.setattr(web_runtime.st, "secrets", MalformedSecrets())
    with pytest.raises(ConfigurationError) as error:
        web_runtime._load_secret_defaults()
    assert "TOML" in str(error.value)
    assert "private-value" not in str(error.value)


def test_invalid_secret_path_is_not_treated_as_missing_optional_file(monkeypatch):
    class InvalidPathSecrets:
        def __getitem__(self, name):
            raise StreamlitSecretNotFoundError("Invalid secrets path: private/path")

    monkeypatch.setattr(web_runtime.st, "secrets", InvalidPathSecrets())
    with pytest.raises(ConfigurationError) as error:
        web_runtime._load_secret_defaults()
    assert "private/path" not in str(error.value)


def test_unrelated_secret_configuration_failure_propagates(monkeypatch):
    class BrokenSecrets:
        def __getitem__(self, name):
            raise RuntimeError("configuration failure")

    monkeypatch.setattr(web_runtime.st, "secrets", BrokenSecrets())
    with pytest.raises(RuntimeError, match="configuration failure"):
        web_runtime._load_secret_defaults()


@pytest.mark.parametrize("value", [[], {}, 1.2])
def test_invalid_known_secret_types_are_rejected(monkeypatch, value):
    monkeypatch.setattr(web_runtime.st, "secrets", {"LLM_API_KEY": value})
    with pytest.raises(ConfigurationError):
        web_runtime._load_secret_defaults()


def test_audit_defaults_and_explicit_overrides(monkeypatch):
    assert web_runtime.audit_directory() == Path(".runs")
    monkeypatch.setenv("TEXTLAB_HOSTED", "true")
    assert web_runtime.audit_directory() is None
    monkeypatch.setenv("TEXTLAB_SAVE_HISTORY", "1")
    assert web_runtime.audit_directory() == Path(".runs")
    monkeypatch.setenv("TEXTLAB_HOSTED", "false")
    monkeypatch.setenv("TEXTLAB_SAVE_HISTORY", "0")
    assert web_runtime.audit_directory() is None


@pytest.mark.parametrize("name", ["TEXTLAB_HOSTED", "TEXTLAB_SAVE_HISTORY"])
@pytest.mark.parametrize("value", ["yes", "no", "", "2"])
def test_invalid_booleans_fail_closed(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigurationError, match=name):
        web_runtime.audit_directory()
    with pytest.raises(ConfigurationError, match=name):
        web_runtime.validate_live_hosting()


def test_hosted_live_mode_requires_gate_but_local_does_not(monkeypatch):
    web_runtime.validate_live_hosting()
    monkeypatch.setenv("TEXTLAB_HOSTED", "1")
    with pytest.raises(ConfigurationError, match="APP_ACCESS_PASSWORD"):
        web_runtime.validate_live_hosting()
    monkeypatch.setenv("APP_ACCESS_PASSWORD", "secure-site-password")
    web_runtime.validate_live_hosting()


def test_whitespace_only_site_password_is_invalid(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_PASSWORD", "  ")
    with pytest.raises(ConfigurationError, match="APP_ACCESS_PASSWORD"):
        web_runtime.validate_live_hosting()


@pytest.mark.parametrize("value", ["0", "9", "2.1", "", "not-an-int"])
def test_invalid_concurrency_limits(monkeypatch, value):
    monkeypatch.setenv("TEXTLAB_MAX_CONCURRENT_RUNS", value)
    with pytest.raises(ConfigurationError, match="1~8"):
        web_runtime.max_concurrent_runs()


def test_concurrency_default_and_valid_override(monkeypatch):
    assert web_runtime.max_concurrent_runs() == 2
    monkeypatch.setenv("TEXTLAB_MAX_CONCURRENT_RUNS", "8")
    assert web_runtime.max_concurrent_runs() == 8


LOGIN_APP = '''
import streamlit as st
from textlab.web_runtime import require_site_access
st.set_page_config(page_title="Access test")
require_site_access()
st.write("authorized app content")
'''


def test_optional_gate_allows_public_mock_content():
    at = AppTest.from_string(LOGIN_APP).run()
    assert not at.exception
    assert not at.text_input
    assert any(item.value == "authorized app content" for item in at.markdown)


def test_login_rejects_wrong_password_accepts_unicode_and_forgets_plaintext(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_PASSWORD", "비밀-사이트-password")
    at = AppTest.from_string(LOGIN_APP).run()
    assert not at.exception
    assert at.text_input[0].proto.type == 1
    assert not any(item.value == "authorized app content" for item in at.markdown)
    at.text_input[0].set_value("wrong").run()
    at.button[0].click().run()
    assert not at.exception
    assert any("올바르지" in item.value for item in at.error)
    assert at.text_input[0].value == ""
    at.text_input[0].set_value("비밀-사이트-password").run()
    at.button[0].click().run()
    assert not at.exception
    assert not at.text_input
    assert any(item.value == "authorized app content" for item in at.markdown)
    assert web_runtime._PASSWORD_FIELD not in at.session_state
    assert at.session_state[web_runtime._AUTH_FIELD] == web_runtime._fingerprint("비밀-사이트-password")
    assert "비밀-사이트-password" not in str(at.session_state.filtered_state)


def test_password_rotation_requires_new_login(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_PASSWORD", "first-password")
    at = AppTest.from_string(LOGIN_APP).run()
    at.text_input[0].set_value("first-password").run()
    at.button[0].click().run()
    assert any(item.value == "authorized app content" for item in at.markdown)
    monkeypatch.setenv("APP_ACCESS_PASSWORD", "rotated-password")
    at.run()
    assert not at.exception
    assert at.text_input
    assert not any(item.value == "authorized app content" for item in at.markdown)
    assert web_runtime._AUTH_FIELD not in at.session_state
    at.text_input[0].set_value("rotated-password").run()
    at.button[0].click().run()
    assert any(item.value == "authorized app content" for item in at.markdown)
