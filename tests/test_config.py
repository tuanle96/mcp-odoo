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
    "ODOO_LOCALE",
    "ODOO_CONFIG_FILE",
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


def test_get_odoo_client_defaults_json2_database_header_on(
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


def test_load_config_includes_odoo_locale_when_set(
    monkeypatch, tmp_path, odoo_client_module
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "prod")
    monkeypatch.setenv("ODOO_USERNAME", "api-user")
    monkeypatch.setenv("ODOO_PASSWORD", "secret")
    monkeypatch.setenv("ODOO_LOCALE", "fr_FR")

    config = odoo_client_module.load_config()
    assert config["lang"] == "fr_FR"


def test_get_odoo_client_passes_lang_from_odoo_locale(monkeypatch, odoo_client_module):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "prod")
    monkeypatch.setenv("ODOO_USERNAME", "api-user")
    monkeypatch.setenv("ODOO_PASSWORD", "secret")
    monkeypatch.setenv("ODOO_LOCALE", "vi_VN")
    monkeypatch.setattr(odoo_client_module, "OdooClient", FakeClient)

    odoo_client_module.get_odoo_client()

    assert captured["lang"] == "vi_VN"


def test_get_odoo_client_lang_defaults_to_none_when_locale_unset(
    monkeypatch, odoo_client_module
):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    clear_odoo_env(monkeypatch)
    monkeypatch.setenv("ODOO_URL", "https://odoo.example.test")
    monkeypatch.setenv("ODOO_DB", "prod")
    monkeypatch.setenv("ODOO_USERNAME", "api-user")
    monkeypatch.setenv("ODOO_PASSWORD", "secret")
    monkeypatch.setattr(odoo_client_module, "OdooClient", FakeClient)

    odoo_client_module.get_odoo_client()

    assert captured["lang"] is None


# ----- Multi-instance configuration ------------------------------------------


MULTI_CONFIG = {
    "default": "acme",
    "instances": {
        "acme": {
            "url": "https://acme.odoo.test",
            "db": "acme",
            "username": "bot",
            "api_key": "acme-key",
            "transport": "json2",
        },
        "globex": {
            "url": "https://globex.odoo.test",
            "db": "globex",
            "username": "demo",
            "password": "globex-secret",
            "timeout": 60,
            "verify_ssl": False,
        },
    },
}


def write_multi_config(tmp_path, config=None):
    path = tmp_path / "odoo_config.json"
    path.write_text(json.dumps(config or MULTI_CONFIG))
    return path


def test_load_instances_config_parses_multi_instance_file(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    write_multi_config(tmp_path)

    default_name, instances = odoo_client_module.load_instances_config()

    assert default_name == "acme"
    assert set(instances) == {"acme", "globex"}
    assert instances["globex"]["db"] == "globex"


def test_load_instances_config_single_instance_needs_no_default(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = {"instances": {"solo": {"url": "https://solo.test", "db": "solo"}}}
    write_multi_config(tmp_path, config)

    default_name, instances = odoo_client_module.load_instances_config()

    assert default_name == "solo"
    assert set(instances) == {"solo"}


def test_load_instances_config_requires_default_for_multiple_instances(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = {"instances": {k: dict(v) for k, v in MULTI_CONFIG["instances"].items()}}
    write_multi_config(tmp_path, config)

    with pytest.raises(ValueError, match="'default' is required"):
        odoo_client_module.load_instances_config()


def test_load_instances_config_rejects_unknown_default(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = {**MULTI_CONFIG, "default": "ghost"}
    write_multi_config(tmp_path, config)

    with pytest.raises(ValueError, match=r"'ghost' not found.*acme.*globex"):
        odoo_client_module.load_instances_config()


def test_load_instances_config_rejects_invalid_instance_name(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = {"instances": {"bad name!": {"url": "https://x.test", "db": "x"}}}
    write_multi_config(tmp_path, config)

    with pytest.raises(ValueError, match="invalid instance name"):
        odoo_client_module.load_instances_config()


def test_load_instances_config_rejects_entry_missing_required_key(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = {"instances": {"acme": {"url": "https://acme.test"}}}
    write_multi_config(tmp_path, config)

    with pytest.raises(ValueError, match="missing required key 'db'"):
        odoo_client_module.load_instances_config()


def test_odoo_config_file_env_var_takes_priority(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    # Local flat config that would normally win
    (tmp_path / "odoo_config.json").write_text(
        json.dumps({"url": "http://local.test", "db": "local"})
    )
    explicit_dir = tmp_path / "explicit"
    explicit_dir.mkdir()
    explicit = write_multi_config(explicit_dir)
    monkeypatch.setenv("ODOO_CONFIG_FILE", str(explicit))

    default_name, instances = odoo_client_module.load_instances_config()

    assert default_name == "acme"
    assert set(instances) == {"acme", "globex"}


def test_odoo_config_file_missing_path_raises(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ODOO_CONFIG_FILE", str(tmp_path / "nope.json"))

    with pytest.raises(FileNotFoundError, match="ODOO_CONFIG_FILE"):
        odoo_client_module.load_instances_config()


def test_legacy_env_vars_win_over_multi_instance_file(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    write_multi_config(tmp_path)
    monkeypatch.setenv("ODOO_URL", "https://env.test")
    monkeypatch.setenv("ODOO_DB", "envdb")
    monkeypatch.setenv("ODOO_USERNAME", "env-user")
    monkeypatch.setenv("ODOO_PASSWORD", "env-secret")

    default_name, instances = odoo_client_module.load_instances_config()

    assert default_name == "default"
    assert set(instances) == {"default"}
    assert instances["default"]["url"] == "https://env.test"


def test_load_config_returns_default_instance_from_multi_file(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    write_multi_config(tmp_path)

    config = odoo_client_module.load_config()

    assert config["url"] == "https://acme.odoo.test"
    assert config["db"] == "acme"


def test_get_odoo_client_for_unknown_instance_lists_names_only(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    write_multi_config(tmp_path)

    with pytest.raises(ValueError) as exc_info:
        odoo_client_module.get_odoo_client_for("ghost")

    message = str(exc_info.value)
    assert "ghost" in message
    assert "acme" in message and "globex" in message
    assert "acme-key" not in message
    assert "globex-secret" not in message
    assert "odoo.test" not in message


def test_get_odoo_client_for_builds_named_instance_with_entry_settings(
    monkeypatch, tmp_path, odoo_client_module
):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    write_multi_config(tmp_path)
    monkeypatch.setattr(odoo_client_module, "OdooClient", FakeClient)

    name, _ = odoo_client_module.get_odoo_client_for("globex")

    assert name == "globex"
    assert captured["url"] == "https://globex.odoo.test"
    assert captured["timeout"] == 60
    assert captured["verify_ssl"] is False
    assert captured["password"] == "globex-secret"


def test_list_configured_instances_never_exposes_credentials(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    write_multi_config(tmp_path)

    summary = odoo_client_module.list_configured_instances()

    assert summary["acme"]["is_default"] is True
    assert summary["globex"]["is_default"] is False
    assert summary["acme"]["transport"] == "json2"
    serialized = json.dumps(summary)
    assert "acme-key" not in serialized
    assert "globex-secret" not in serialized
    assert "password" not in serialized
    assert "api_key" not in serialized


def test_env_api_key_never_leaks_into_multi_instance_entries(
    monkeypatch, tmp_path, odoo_client_module
):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    write_multi_config(tmp_path)
    monkeypatch.setenv("ODOO_API_KEY", "leftover-prod-key")
    monkeypatch.setattr(odoo_client_module, "OdooClient", FakeClient)

    # globex has no api_key of its own — it must NOT inherit the env key.
    odoo_client_module.get_odoo_client_for("globex")

    assert captured["api_key"] is None


def test_env_overrides_still_win_over_legacy_flat_config(
    monkeypatch, tmp_path, odoo_client_module
):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "odoo_config.json").write_text(
        json.dumps(
            {
                "url": "http://local.test",
                "db": "dev",
                "username": "demo",
                "password": "demo",
                "transport": "xmlrpc",
            }
        )
    )
    monkeypatch.setenv("ODOO_TRANSPORT", "json2")
    monkeypatch.setenv("ODOO_API_KEY", "env-key")
    monkeypatch.setattr(odoo_client_module, "OdooClient", FakeClient)

    odoo_client_module.get_odoo_client()

    assert captured["transport"] == "json2"
    assert captured["api_key"] == "env-key"


def test_verify_ssl_null_in_entry_falls_back_to_secure_default(
    monkeypatch, tmp_path, odoo_client_module
):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = {
        "instances": {
            "solo": {
                "url": "https://solo.test",
                "db": "solo",
                "verify_ssl": None,
            }
        }
    }
    write_multi_config(tmp_path, config)
    monkeypatch.setattr(odoo_client_module, "OdooClient", FakeClient)

    odoo_client_module.get_odoo_client_for("solo")

    assert captured["verify_ssl"] is True


def test_instance_literally_named_default_in_multi_file_is_valid(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = {
        "default": "default",
        "instances": {
            "default": {"url": "https://main.test", "db": "main"},
            "other": {"url": "https://other.test", "db": "other"},
        },
    }
    write_multi_config(tmp_path, config)

    default_name, instances = odoo_client_module.load_instances_config()

    assert default_name == "default"
    assert set(instances) == {"default", "other"}


def test_invalid_json_config_raises_with_path_not_contents(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    secret_garbage = '{"password": "top-secret-broken'
    (tmp_path / "odoo_config.json").write_text(secret_garbage)

    with pytest.raises(ValueError) as exc_info:
        odoo_client_module.load_instances_config()

    message = str(exc_info.value)
    assert "odoo_config.json" in message
    assert "top-secret-broken" not in message


def test_instances_key_must_be_nonempty_object(
    monkeypatch, tmp_path, odoo_client_module
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    for bad_instances in ({}, "not-a-dict", []):
        write_multi_config(tmp_path, {"instances": bad_instances})
        with pytest.raises(ValueError, match="non-empty object"):
            odoo_client_module.load_instances_config()


def test_instance_entry_must_be_an_object(monkeypatch, tmp_path, odoo_client_module):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    write_multi_config(tmp_path, {"instances": {"acme": "https://acme.test"}})

    with pytest.raises(ValueError, match="must be an object"):
        odoo_client_module.load_instances_config()


def test_string_timeout_in_entry_is_coerced_to_int(
    monkeypatch, tmp_path, odoo_client_module
):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    config = {
        "instances": {
            "solo": {"url": "https://solo.test", "db": "solo", "timeout": "45"}
        }
    }
    write_multi_config(tmp_path, config)
    monkeypatch.setattr(odoo_client_module, "OdooClient", FakeClient)

    odoo_client_module.get_odoo_client_for("solo")

    assert captured["timeout"] == 45


def test_warns_when_odoo_config_file_is_ignored_by_env_vars(
    monkeypatch, tmp_path, odoo_client_module, capsys
):
    clear_odoo_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    explicit = write_multi_config(tmp_path)
    monkeypatch.setenv("ODOO_CONFIG_FILE", str(explicit))
    monkeypatch.setenv("ODOO_URL", "https://env.test")
    monkeypatch.setenv("ODOO_DB", "envdb")
    monkeypatch.setenv("ODOO_USERNAME", "env-user")
    monkeypatch.setenv("ODOO_PASSWORD", "env-secret")

    default_name, instances = odoo_client_module.load_instances_config()

    assert default_name == "default"
    assert set(instances) == {"default"}
    captured = capsys.readouterr()
    assert "ODOO_CONFIG_FILE is ignored" in captured.err
