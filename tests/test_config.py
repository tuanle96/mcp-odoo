import json

import pytest

ODOO_ENV_KEYS = (
    "ODOO_URL",
    "ODOO_DB",
    "ODOO_USERNAME",
    "ODOO_PASSWORD",
    "ODOO_TRANSPORT",
    "ODOO_API_KEY",
    "ODOO_JSON2_DATABASE_HEADER",
)


def clear_odoo_env(monkeypatch):
    for key in ODOO_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_load_config_returns_environment_values_when_complete(
    monkeypatch, tmp_path, odoo_client_module
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "prod")
    monkeypatch.setenv("ODOO_USERNAME", "api-user")
    monkeypatch.setenv("ODOO_PASSWORD", "secret")

    assert odoo_client_module.load_config() == {
        "url": "https://odoo.example.test",
        "db": "prod",
        "username": "api-user",
        "password": "secret",
    }


def test_load_config_includes_optional_json2_environment_values(
    monkeypatch, tmp_path, odoo_client_module
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "prod")
    monkeypatch.setenv("ODOO_USERNAME", "api-user")
    monkeypatch.setenv("ODOO_PASSWORD", "legacy-password")
    monkeypatch.setenv("ODOO_TRANSPORT", "json2")
    monkeypatch.setenv("ODOO_API_KEY", "api-key")
    monkeypatch.setenv("ODOO_JSON2_DATABASE_HEADER", "0")

    assert odoo_client_module.load_config() == {
        "url": "https://odoo.example.test",
        "db": "prod",
        "username": "api-user",
        "password": "legacy-password",
        "transport": "json2",
        "api_key": "api-key",
        "json2_database_header": "0",
    }


def test_get_odoo_client_defaults_json2_database_header_on(monkeypatch, odoo_client_module):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "prod")
    monkeypatch.setenv("ODOO_USERNAME", "api-user")
    monkeypatch.setenv("ODOO_PASSWORD", "legacy-password")
    monkeypatch.setenv("ODOO_TRANSPORT", "json2")
    monkeypatch.setenv("ODOO_API_KEY", "api-key")
    monkeypatch.setattr(odoo_client_module, "OdooClient", FakeClient)

    odoo_client_module.get_odoo_client()

    assert captured["json2_database_header"] is True


def test_get_odoo_client_allows_json2_database_header_opt_out(
    monkeypatch, odoo_client_module
):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "prod")
    monkeypatch.setenv("ODOO_USERNAME", "api-user")
    monkeypatch.setenv("ODOO_PASSWORD", "legacy-password")
    monkeypatch.setenv("ODOO_TRANSPORT", "json2")
    monkeypatch.setenv("ODOO_API_KEY", "api-key")
    monkeypatch.setenv("ODOO_JSON2_DATABASE_HEADER", "false")
    monkeypatch.setattr(odoo_client_module, "OdooClient", FakeClient)

    odoo_client_module.get_odoo_client()

    assert captured["json2_database_header"] is False


def test_load_config_returns_local_config_file_when_environment_missing(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = {
        "url": "http://localhost:8069",
        "db": "dev",
        "username": "demo",
        "password": "demo",
    }
    (tmp_path / "odoo_config.json").write_text(json.dumps(config))

    assert odoo_client_module.load_config() == config


def test_load_config_raises_when_no_environment_or_config_file(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("os.path.expanduser", lambda path: str(tmp_path / path))

    with pytest.raises(FileNotFoundError):
        odoo_client_module.load_config()
