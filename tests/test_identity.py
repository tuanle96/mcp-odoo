"""Unit tests for the per-request identity core (Algorithma fork).

Covers the pure ``odoo_mcp.identity`` module plus the request-scoped client
factory in ``odoo_mcp.odoo_client``. No MCP surface, no network.
"""

import time

import pytest

from odoo_mcp import identity, odoo_client

KEY_A = "a-key-0123456789abcdef0123456789abcdef01"
KEY_B = "b-key-0123456789abcdef0123456789abcdef02"


# --- identity mode ----------------------------------------------------------


def test_identity_mode_defaults_to_configured(monkeypatch):
    monkeypatch.delenv(identity.IDENTITY_MODE_ENV, raising=False)
    assert identity.identity_mode() == "configured"
    assert identity.request_identity_mode() is False


@pytest.mark.parametrize("raw", ["request", "REQUEST", " request "])
def test_identity_mode_request_is_case_insensitive(monkeypatch, raw):
    monkeypatch.setenv(identity.IDENTITY_MODE_ENV, raw)
    assert identity.identity_mode() == "request"
    assert identity.request_identity_mode() is True


def test_identity_mode_rejects_unknown_values(monkeypatch):
    monkeypatch.setenv(identity.IDENTITY_MODE_ENV, "per-user")
    with pytest.raises(ValueError, match="ODOO_MCP_IDENTITY_MODE"):
        identity.identity_mode()


# --- header resolution ------------------------------------------------------


def test_resolve_identity_reads_headers_case_insensitively():
    resolved = identity.resolve_request_identity(
        {
            "X-User-Email": "anna@example.ch",
            "X-ODOO-API-KEY": KEY_A,
            "x-librechat-user-id": "lc-42",
        }
    )
    assert resolved.email == "anna@example.ch"
    assert resolved.api_key == KEY_A
    assert resolved.librechat_user_id == "lc-42"
    assert resolved.principal == "anna@example.ch"


def test_resolve_identity_accepts_bytes_and_strips_whitespace():
    resolved = identity.resolve_request_identity(
        {"x-user-email": b"  bob@example.ch ", "x-odoo-api-key": f" {KEY_B} "}
    )
    assert resolved.email == "bob@example.ch"
    assert resolved.api_key == KEY_B
    assert resolved.librechat_user_id is None


def test_resolve_identity_fails_closed_without_headers():
    with pytest.raises(identity.MissingIdentityError, match="stdio"):
        identity.resolve_request_identity(None)


@pytest.mark.parametrize(
    "headers, expected",
    [
        ({}, "X-User-Email, X-Odoo-Api-Key"),
        ({"x-user-email": "anna@example.ch"}, "X-Odoo-Api-Key"),
        ({"x-odoo-api-key": KEY_A}, "X-User-Email"),
        ({"x-user-email": "   ", "x-odoo-api-key": KEY_A}, "X-User-Email"),
    ],
)
def test_resolve_identity_names_missing_headers_only(headers, expected):
    with pytest.raises(identity.MissingIdentityError) as info:
        identity.resolve_request_identity(headers)
    assert expected in str(info.value)
    assert KEY_A not in str(info.value)


def test_resolve_identity_rejects_malformed_values():
    with pytest.raises(identity.InvalidIdentityError, match="X-Odoo-Api-Key"):
        identity.resolve_request_identity(
            {"x-user-email": "anna@example.ch", "x-odoo-api-key": "bad key"}
        )
    with pytest.raises(identity.InvalidIdentityError, match="X-User-Email"):
        identity.resolve_request_identity(
            {"x-user-email": "a" * 300, "x-odoo-api-key": KEY_A}
        )
    with pytest.raises(identity.InvalidIdentityError):
        identity.resolve_request_identity(
            {"x-user-email": "anna\x00", "x-odoo-api-key": KEY_A}
        )


# --- RequestIdentity --------------------------------------------------------


def test_identity_never_exposes_key_in_repr_or_audit_fields():
    who = identity.RequestIdentity("anna@example.ch", KEY_A, "lc-1")
    assert KEY_A not in repr(who)
    assert KEY_A not in str(who)
    assert "<redacted>" in repr(who)
    assert who.audit_fields() == {"principal": "anna@example.ch", "client_user_id": "lc-1"}
    assert KEY_A not in str(who.audit_fields())
    assert KEY_A not in who.credential_digest()


def test_cache_key_separates_instance_database_user_and_credential():
    anna = identity.RequestIdentity("anna@example.ch", KEY_A)
    anna_rotated = identity.RequestIdentity("anna@example.ch", KEY_B)
    bob = identity.RequestIdentity("bob@example.ch", KEY_A)
    base = anna.cache_key("demo", "demo")
    assert base != anna.cache_key("demo2", "demo")  # other instance
    assert base != anna.cache_key("demo", "other_db")  # other database
    assert base != bob.cache_key("demo", "demo")  # other user
    assert base != anna_rotated.cache_key("demo", "demo")  # rotated key
    assert base == identity.RequestIdentity("ANNA@example.ch", KEY_A).cache_key(
        "demo", "demo"
    )  # logins are case-insensitive, keys are not
    assert KEY_A not in base


def test_approval_binding_separates_user_and_instance():
    anna = identity.RequestIdentity("anna@example.ch", KEY_A)
    bob = identity.RequestIdentity("bob@example.ch", KEY_B)
    assert anna.approval_binding("demo") != bob.approval_binding("demo")
    assert anna.approval_binding("demo") != anna.approval_binding("demo2")
    assert KEY_A not in anna.approval_binding("demo")


# --- bounded client cache ---------------------------------------------------


def test_identity_cache_builds_once_per_key_and_isolates_keys():
    cache = identity.IdentityClientCache(max_entries=8, ttl_seconds=60)
    built = []

    def factory(label):
        def build():
            built.append(label)
            return {"client": label}

        return build

    a1 = cache.get_or_create("key-a", factory("A"))
    a2 = cache.get_or_create("key-a", factory("A"))
    b1 = cache.get_or_create("key-b", factory("B"))
    assert a1 is a2
    assert a1 is not b1
    assert b1 == {"client": "B"}  # B never receives A's client
    assert built == ["A", "B"]
    assert len(cache) == 2
    assert cache.posture() == {"max_entries": 8, "ttl_seconds": 60, "size": 2}


def test_identity_cache_expires_entries_by_ttl():
    cache = identity.IdentityClientCache(max_entries=8, ttl_seconds=0.05)
    builds = []
    cache.get_or_create("key-a", lambda: builds.append(1) or "one")
    time.sleep(0.08)
    assert cache.get("key-a") is None
    cache.get_or_create("key-a", lambda: builds.append(2) or "two")
    assert builds == [1, 2]


def test_identity_cache_is_bounded_and_invalidates():
    cache = identity.IdentityClientCache(max_entries=2, ttl_seconds=60)
    cache.get_or_create("k1", lambda: "c1")
    cache.get_or_create("k2", lambda: "c2")
    cache.get_or_create("k3", lambda: "c3")
    assert len(cache) == 2
    assert cache.get("k1") is None  # LRU-evicted
    cache.invalidate("k3")
    assert cache.get("k3") is None
    cache.clear()
    assert len(cache) == 0


def test_identity_cache_settings_from_env(monkeypatch):
    monkeypatch.setenv(identity.IDENTITY_CACHE_TTL_ENV, "30")
    monkeypatch.setenv(identity.IDENTITY_CACHE_MAX_ENV, "5")
    assert identity.IdentityClientCache.settings()["ttl_seconds"] == 30.0
    assert identity.IdentityClientCache.settings()["max_entries"] == 5
    monkeypatch.setenv(identity.IDENTITY_CACHE_MAX_ENV, "not-a-number")
    assert (
        identity.IdentityClientCache.settings()["max_entries"]
        == identity.DEFAULT_IDENTITY_CACHE_MAX_ENTRIES
    )


# --- posture ----------------------------------------------------------------


def test_identity_posture_configured_mode_is_quiet(monkeypatch):
    monkeypatch.delenv(identity.IDENTITY_MODE_ENV, raising=False)
    posture = identity.identity_posture(
        configured_credentials_present=True, transport="stdio"
    )
    assert posture["identity_mode"] == "configured"
    assert posture["request_identity_required"] is False
    assert posture["configured_shared_credentials_used"] is True
    assert posture["warnings"] == []
    assert posture["ok"] is True


def test_identity_posture_flags_insecure_request_mode_setups(monkeypatch):
    monkeypatch.setenv(identity.IDENTITY_MODE_ENV, "request")
    posture = identity.identity_posture(
        configured_credentials_present=True,
        transport="stdio",
        registered_tools=["search_records", "index_knowledge", "get_async_task"],
    )
    assert posture["request_identity_required"] is True
    assert posture["configured_shared_credentials_used"] is False
    assert posture["ok"] is False
    joined = "\n".join(posture["warnings"])
    assert "stdio" in joined
    assert "ignored in request mode" in joined
    assert "index_knowledge" in joined and "get_async_task" in joined
    assert "search_records" not in joined


def test_identity_posture_clean_request_mode(monkeypatch):
    monkeypatch.setenv(identity.IDENTITY_MODE_ENV, "request")
    posture = identity.identity_posture(
        configured_credentials_present=False,
        transport="streamable-http",
        registered_tools=["search_records", "health_check"],
    )
    assert posture["ok"] is True
    assert posture["identity_headers"] == [
        "x-user-email",
        "x-odoo-api-key",
        "x-librechat-user-id",
    ]


def test_identity_posture_reports_invalid_mode(monkeypatch):
    monkeypatch.setenv(identity.IDENTITY_MODE_ENV, "bogus")
    posture = identity.identity_posture(configured_credentials_present=False)
    assert posture["identity_mode"] == "invalid"
    assert posture["ok"] is False


# --- request-scoped client factory -----------------------------------------


class _RecordingClient:
    """Stands in for OdooClient: records constructor kwargs, no network."""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _RecordingClient.instances.append(self)


def test_build_identity_client_uses_request_credentials_only(monkeypatch, capsys):
    _RecordingClient.instances.clear()
    monkeypatch.setattr(odoo_client, "OdooClient", _RecordingClient)
    entry = {
        "url": "http://127.0.0.1:8071",
        "db": "demo",
        "username": "shared-bot",  # must be ignored
        "password": "shared-secret",  # must be ignored
        "api_key": "shared-api-key",  # must be ignored
        "timeout": 7,
        "verify_ssl": False,
    }
    who = identity.RequestIdentity("anna@example.ch", KEY_A, "lc-1")
    client = odoo_client.build_identity_client(entry, who, name="demo")
    assert client.kwargs["username"] == "anna@example.ch"
    assert client.kwargs["password"] == KEY_A
    assert client.kwargs["api_key"] is None  # xmlrpc: key is the password
    assert client.kwargs["db"] == "demo"
    assert client.kwargs["timeout"] == 7
    assert client.kwargs["verify_ssl"] is False
    assert "shared" not in str(client.kwargs.values())
    err = capsys.readouterr().err
    assert "anna@example.ch" in err
    assert KEY_A not in err
    assert "shared-secret" not in err


def test_build_identity_client_json2_uses_key_as_bearer(monkeypatch):
    _RecordingClient.instances.clear()
    monkeypatch.setattr(odoo_client, "OdooClient", _RecordingClient)
    entry = {"url": "http://odoo19.local", "db": "demo", "transport": "json2"}
    who = identity.RequestIdentity("anna@example.ch", KEY_A)
    client = odoo_client.build_identity_client(entry, who)
    assert client.kwargs["transport"] == "json2"
    assert client.kwargs["api_key"] == KEY_A


@pytest.mark.parametrize(
    "raised, expected, fragment",
    [
        (
            ValueError("Authentication failed: Invalid username or password"),
            identity.IdentityAuthenticationError,
            "invalid login or API key",
        ),
        (
            ValueError("Failed to authenticate with Odoo: database demo9 not found"),
            identity.IdentityAuthenticationError,
            "check the database name",
        ),
        (
            ConnectionError("Failed to connect to Odoo server: refused"),
            identity.IdentityOdooUnreachableError,
            "unreachable",
        ),
    ],
)
def test_build_identity_client_sanitizes_failures(monkeypatch, raised, expected, fragment):
    class Boom:
        def __init__(self, **kwargs):
            raise raised

    monkeypatch.setattr(odoo_client, "OdooClient", Boom)
    who = identity.RequestIdentity("anna@example.ch", KEY_A)
    with pytest.raises(expected) as info:
        odoo_client.build_identity_client(
            {"url": "http://127.0.0.1:8071", "db": "demo"}, who, name="demo"
        )
    message = str(info.value)
    assert fragment in message
    assert KEY_A not in message
    assert "demo" in message


def test_request_mode_env_config_needs_only_url_and_db(monkeypatch):
    monkeypatch.setenv(identity.IDENTITY_MODE_ENV, "request")
    monkeypatch.setenv("ODOO_URL", "http://127.0.0.1:8071")
    monkeypatch.setenv("ODOO_DB", "demo")
    monkeypatch.delenv("ODOO_USERNAME", raising=False)
    monkeypatch.delenv("ODOO_PASSWORD", raising=False)
    default, instances = odoo_client.load_instances_config()
    assert default == "default"
    assert instances["default"] == {"url": "http://127.0.0.1:8071", "db": "demo"}
    assert "username" not in instances["default"]


def test_configured_mode_env_config_still_needs_all_four(monkeypatch):
    monkeypatch.delenv(identity.IDENTITY_MODE_ENV, raising=False)
    monkeypatch.setenv("ODOO_URL", "http://127.0.0.1:8071")
    monkeypatch.setenv("ODOO_DB", "demo")
    assert odoo_client._env_config() is None


def test_configured_credential_clients_refuse_request_mode(monkeypatch):
    monkeypatch.setenv(identity.IDENTITY_MODE_ENV, "request")
    monkeypatch.setenv("ODOO_URL", "http://127.0.0.1:8071")
    monkeypatch.setenv("ODOO_DB", "demo")
    with pytest.raises(identity.MissingIdentityError, match="disabled in request"):
        odoo_client.get_odoo_client()
    with pytest.raises(identity.MissingIdentityError, match="disabled in request"):
        odoo_client.get_odoo_client_for("default")
