"""Small runtime controls for local execution and a hosted Streamlit instance."""
from __future__ import annotations

import hashlib
import hmac
import os
from pathlib import Path

import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError

from textlab.transport import ConfigurationError


_SECRET_KEYS = (
    "LLM_API_KEY", "GPTZERO_API_KEY", "LLM_MODEL", "APP_ACCESS_PASSWORD",
    "TEXTLAB_HOSTED", "TEXTLAB_SAVE_HISTORY", "TEXTLAB_MAX_CONCURRENT_RUNS",
)
_PASSWORD_FIELD = "_textlab_access_password"
_AUTH_FIELD = "_textlab_access_fingerprint"
_FAILED_FIELD = "_textlab_access_failed"


def _load_secret_defaults() -> None:
    """Read only supported root secret keys; preserve existing environment values.

    Streamlit itself exports root TOML scalars on first access. Restore our known
    existing bindings after that parse so environment settings retain precedence.
    """
    existing = {name: os.environ[name] for name in _SECRET_KEYS if name in os.environ}
    try:
        for name in _SECRET_KEYS:
            try:
                value = st.secrets[name]
            except KeyError:
                continue
            if name in existing:
                continue
            if type(value) not in (str, int, bool):
                raise ConfigurationError(f"Streamlit Secrets의 {name}은 문자열·정수·불리언이어야 합니다.")
            os.environ.setdefault(name, str(value).lower() if type(value) is bool else str(value))
    except StreamlitSecretNotFoundError as exc:
        # This Streamlit version also wraps TOML parse failures in this class.
        # Missing files have a distinct message. Invalid paths can also use this
        # exception without a cause, so they must not be treated as optional.
        if exc.__cause__ is not None or not str(exc).startswith("No secrets found."):
            raise ConfigurationError("Streamlit Secrets 설정 파일을 읽지 못했습니다. TOML 형식을 확인하세요.") from None
    finally:
        for name, value in existing.items():
            os.environ[name] = value


def _boolean(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in ("1", "true"):
        return True
    if normalized in ("0", "false"):
        return False
    raise ConfigurationError(f"{name}은 1/0 또는 true/false로 설정하세요.")


def _site_password() -> str | None:
    password = os.environ.get("APP_ACCESS_PASSWORD", "")
    if password and not password.strip():
        raise ConfigurationError("APP_ACCESS_PASSWORD에는 공백 이외의 문자를 입력하세요.")
    return password or None


def _fingerprint(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _submit_login() -> None:
    """A callback runs before the next widget render, so its key can be removed."""
    supplied = st.session_state.pop(_PASSWORD_FIELD, "")
    password = _site_password()
    allowed = password is not None and hmac.compare_digest(
        str(supplied).encode("utf-8"), password.encode("utf-8"),
    )
    if allowed:
        st.session_state[_AUTH_FIELD] = _fingerprint(password)
        st.session_state.pop(_FAILED_FIELD, None)
    else:
        st.session_state.pop(_AUTH_FIELD, None)
        st.session_state[_FAILED_FIELD] = True


def require_site_access() -> None:
    """Call immediately after set_page_config, before rendering any application data."""
    _load_secret_defaults()
    password = _site_password()
    if password is None:
        st.session_state.pop(_AUTH_FIELD, None)
        st.session_state.pop(_PASSWORD_FIELD, None)
        return
    expected = _fingerprint(password)
    if hmac.compare_digest(str(st.session_state.get(_AUTH_FIELD, "")), expected):
        st.session_state.pop(_PASSWORD_FIELD, None)
        return
    st.session_state.pop(_AUTH_FIELD, None)
    st.title("문장 실험실 로그인")
    st.caption("운영자가 설정한 사이트 비밀번호를 입력하세요.")
    with st.form("_textlab_login_form"):
        st.text_input("사이트 비밀번호", type="password", key=_PASSWORD_FIELD)
        st.form_submit_button("로그인", on_click=_submit_login)
    if st.session_state.pop(_FAILED_FIELD, False):
        st.error("비밀번호가 올바르지 않습니다.")
    st.stop()


def audit_directory() -> Path | None:
    """Hosted instances default to memory-only sessions, local runs to .runs/."""
    hosted = _boolean("TEXTLAB_HOSTED", False)
    save = _boolean("TEXTLAB_SAVE_HISTORY", not hosted)
    return Path(".runs") if save else None


def validate_live_hosting() -> None:
    """Public demo remains available; hosted paid APIs require a configured gate."""
    hosted = _boolean("TEXTLAB_HOSTED", False)
    _boolean("TEXTLAB_SAVE_HISTORY", not hosted)
    password = _site_password()
    if hosted and password is None:
        raise ConfigurationError(
            "호스팅 환경의 실제 API 모드는 APP_ACCESS_PASSWORD 설정이 필요합니다. "
            "호스팅 서비스의 Secrets 또는 환경변수에 사이트 비밀번호를 설정하세요."
        )


def max_concurrent_runs() -> int:
    try:
        value = int(os.environ.get("TEXTLAB_MAX_CONCURRENT_RUNS", "2"))
    except ValueError:
        raise ConfigurationError("TEXTLAB_MAX_CONCURRENT_RUNS는 1~8의 정수여야 합니다.") from None
    if not 1 <= value <= 8:
        raise ConfigurationError("TEXTLAB_MAX_CONCURRENT_RUNS는 1~8의 정수여야 합니다.")
    return value
