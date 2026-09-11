"""config.py 단위 테스트."""

from __future__ import annotations

import json

import pytest

from kakao_mac_mcp.config import (
    AppConfig,
    RoomConfig,
    load_config,
    resolve_env_vars,
    save_config,
    validate_config,
)


def _write_json(path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_missing_file_raises_with_guidance(tmp_path) -> None:
    missing = tmp_path / "config.json"
    with pytest.raises(FileNotFoundError) as exc_info:
        load_config(missing)
    assert "config.example.json" in str(exc_info.value)


def test_load_config_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    _write_json(
        config_path,
        {
            "adapter": "macos",
            "send_enabled": False,
            "auto_reply_enabled": False,
            "schedule_automation_enabled": False,
            "rooms": [],
            "privacy": {"log_actual_room_titles": False, "log_message_content": False},
            "limits": {
                "max_messages_per_read": 50,
                "max_send_per_minute": 5,
                "fingerprint_retention_days": 30,
            },
        },
    )

    config = load_config(config_path)

    assert isinstance(config, AppConfig)
    assert config.send_enabled is False
    assert config.auto_reply_enabled is False
    assert config.schedule_automation_enabled is False
    assert config.rooms == []
    assert config.limits.max_send_per_minute == 5


def test_load_config_minimal_json_uses_field_defaults(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    _write_json(config_path, {})

    config = load_config(config_path)

    assert config.send_enabled is False
    assert config.auto_reply_enabled is False
    assert config.adapter == "macos"


def test_load_config_invalid_json_raises_value_error(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(config_path)


def test_save_config_roundtrip(tmp_path) -> None:
    config_path = tmp_path / "config.json"
    original = AppConfig(send_enabled=False, rooms=[RoomConfig(room_id="r1")])

    save_config(original, config_path)
    loaded = load_config(config_path)

    assert loaded.rooms[0].room_id == "r1"
    assert loaded.send_enabled is False


# ---------------------------------------------------------------------------
# resolve_env_vars / load_config env substitution
# ---------------------------------------------------------------------------


def test_resolve_env_vars_substitutes_defined_var(monkeypatch) -> None:
    monkeypatch.setenv("KAKAO_TEST_VAR", "hello")
    assert resolve_env_vars("${KAKAO_TEST_VAR}") == "hello"


def test_resolve_env_vars_leaves_undefined_var_untouched(monkeypatch) -> None:
    monkeypatch.delenv("KAKAO_UNDEFINED_VAR", raising=False)
    assert resolve_env_vars("${KAKAO_UNDEFINED_VAR}") == "${KAKAO_UNDEFINED_VAR}"


def test_resolve_env_vars_ignores_non_string() -> None:
    assert resolve_env_vars(True) is True
    assert resolve_env_vars(42) == 42
    assert resolve_env_vars(None) is None


def test_load_config_resolves_nested_env_vars(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KAKAO_DISPLAY_HINT", "친구방")
    config_path = tmp_path / "config.json"
    _write_json(
        config_path,
        {"rooms": [{"room_id": "r1", "display_hint": "${KAKAO_DISPLAY_HINT}"}]},
    )

    config = load_config(config_path)

    assert config.rooms[0].display_hint == "친구방"


# ---------------------------------------------------------------------------
# validate_config
# ---------------------------------------------------------------------------


def test_validate_config_no_warnings_for_sane_config() -> None:
    config = AppConfig(rooms=[RoomConfig(room_id="r1")])
    warnings = validate_config(config)
    assert warnings == []


def test_validate_config_flags_send_and_auto_reply_together() -> None:
    config = AppConfig(
        send_enabled=True,
        auto_reply_enabled=True,
        rooms=[RoomConfig(room_id="r1")],
    )
    warnings = validate_config(config)
    assert any("auto_reply_enabled" in w for w in warnings)


def test_validate_config_flags_empty_rooms() -> None:
    config = AppConfig(rooms=[])
    warnings = validate_config(config)
    assert any("rooms" in w for w in warnings)


def test_validate_config_flags_duplicate_room_ids() -> None:
    config = AppConfig(rooms=[RoomConfig(room_id="r1"), RoomConfig(room_id="r1")])
    warnings = validate_config(config)
    assert any("중복" in w for w in warnings)


def test_validate_config_flags_invalid_limits() -> None:
    config = AppConfig(rooms=[RoomConfig(room_id="r1")])
    config.limits.max_send_per_minute = 0
    warnings = validate_config(config)
    assert any("max_send_per_minute" in w for w in warnings)


def test_validate_config_flags_log_actual_room_titles() -> None:
    config = AppConfig(rooms=[RoomConfig(room_id="r1")])
    config.privacy.log_actual_room_titles = True
    warnings = validate_config(config)
    assert any("log_actual_room_titles" in w for w in warnings)
