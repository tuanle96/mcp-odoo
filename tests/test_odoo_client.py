import json
from io import BytesIO

import pytest


class FakeCommonProxy:
    def __init__(self, calls):
        self.calls = calls

    def authenticate(self, db, username, password, context):
        self.calls.append(("authenticate", db, username, password, context))
        return 42

    def version(self):
        self.calls.append(("version",))
        return {"server_version": "19.0"}


class FakeObjectProxy:
    def __init__(self, calls):
        self.calls = calls

    def execute_kw(self, *args):
        self.calls.append(("execute_kw", args))
        return [{"id": 7, "name": "Ada"}]


class FakeJsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def build_client(monkeypatch, odoo_client_module):
    calls = []

    def fake_server_proxy(endpoint, transport):
        calls.append(("ServerProxy", endpoint, type(transport).__name__))
        if endpoint.endswith("/xmlrpc/2/common"):
            return FakeCommonProxy(calls)
        if endpoint.endswith("/xmlrpc/2/object"):
            return FakeObjectProxy(calls)
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(
        odoo_client_module.xmlrpc.client, "ServerProxy", fake_server_proxy
    )
    client = odoo_client_module.OdooClient(
        url="odoo.example.test/",
        db="demo-db",
        username="demo-user",
        password="demo-password",
        timeout=3,
        verify_ssl=False,
    )
    return client, calls


def build_json2_client(
    monkeypatch, odoo_client_module, responses=None, json2_database_header=True
):
    calls = []
    response_payloads = list(responses or [])

    def fake_urlopen(request, timeout=None, context=None):
        body = request.data.decode("utf-8") if request.data else "{}"
        calls.append(
            {
                "url": request.full_url,
                "headers": dict(request.header_items()),
                "body": json.loads(body),
                "timeout": timeout,
                "context": context,
            }
        )
        payload = response_payloads.pop(0) if response_payloads else {"ok": True}
        return FakeJsonResponse(payload)

    monkeypatch.setattr(odoo_client_module.urllib.request, "urlopen", fake_urlopen)
    client = odoo_client_module.OdooClient(
        url="https://odoo.example.test/",
        db="demo-db",
        username="demo-user",
        password="demo-password",
        timeout=3,
        verify_ssl=False,
        transport="json-2",
        api_key="json2-key",
        json2_database_header=json2_database_header,
    )
    return client, calls


def test_client_initialization_creates_common_and_object_xmlrpc_endpoints(
    monkeypatch, odoo_client_module
):
    client, calls = build_client(monkeypatch, odoo_client_module)

    assert client.url == "http://odoo.example.test"
    assert client.uid == 42
    assert calls[:3] == [
        (
            "ServerProxy",
            "http://odoo.example.test/xmlrpc/2/common",
            "RedirectTransport",
        ),
        (
            "ServerProxy",
            "http://odoo.example.test/xmlrpc/2/object",
            "RedirectTransport",
        ),
        ("authenticate", "demo-db", "demo-user", "demo-password", {}),
    ]


def test_execute_method_passes_database_credentials_model_method_args_and_kwargs(
    monkeypatch, odoo_client_module
):
    client, calls = build_client(monkeypatch, odoo_client_module)

    result = client.execute_method(
        "res.partner",
        "write",
        [7],
        {"name": "Ada"},
        context={"lang": "en_US"},
    )

    assert result == [{"id": 7, "name": "Ada"}]
    method, payload = calls[-1]
    assert method == "execute_kw"
    assert payload[:5] == (
        "demo-db",
        42,
        "demo-password",
        "res.partner",
        "write",
    )
    assert list(payload[5]) == [[7], {"name": "Ada"}]
    assert payload[6] == {"context": {"lang": "en_US"}}


def test_search_read_passes_domain_as_single_positional_argument_with_keyword_options(
    monkeypatch, odoo_client_module
):
    client, calls = build_client(monkeypatch, odoo_client_module)

    client.search_read(
        "res.partner",
        [["is_company", "=", True]],
        fields=["name"],
        offset=5,
        limit=10,
        order="name ASC",
    )

    method, payload = calls[-1]
    assert method == "execute_kw"
    assert payload[:5] == (
        "demo-db",
        42,
        "demo-password",
        "res.partner",
        "search_read",
    )
    assert list(payload[5]) == [[["is_company", "=", True]]]
    assert payload[6] == {
        "fields": ["name"],
        "offset": 5,
        "limit": 10,
        "order": "name ASC",
    }


def test_read_records_passes_ids_as_single_positional_argument_with_fields_kwarg(
    monkeypatch, odoo_client_module
):
    client, calls = build_client(monkeypatch, odoo_client_module)

    client.read_records("res.partner", [7], fields=["name", "email"])

    method, payload = calls[-1]
    assert method == "execute_kw"
    assert payload[:5] == (
        "demo-db",
        42,
        "demo-password",
        "res.partner",
        "read",
    )
    assert list(payload[5]) == [[7]]
    assert payload[6] == {"fields": ["name", "email"]}


def test_get_model_info_passes_fields_as_keyword_argument(
    monkeypatch, odoo_client_module
):
    client, calls = build_client(monkeypatch, odoo_client_module)

    client.get_model_info("res.partner")

    method, payload = calls[-1]
    assert method == "execute_kw"
    assert payload[:5] == (
        "demo-db",
        42,
        "demo-password",
        "ir.model",
        "search_read",
    )
    assert list(payload[5]) == [[("model", "=", "res.partner")]]
    assert payload[6] == {"fields": ["name", "model"]}


def test_profile_helpers_read_version_context_and_installed_modules(
    monkeypatch, odoo_client_module
):
    calls = []

    class ProfileObjectProxy:
        def execute_kw(self, *args):
            calls.append(("execute_kw", args))
            model = args[3]
            method = args[4]
            if model == "res.users" and method == "context_get":
                return {"lang": "en_US"}
            if model == "ir.module.module" and method == "search_read":
                return [{"name": "base", "shortdesc": "Base", "state": "installed"}]
            raise AssertionError(f"unexpected call: {model}.{method}")

    def fake_server_proxy(endpoint, transport):
        calls.append(("ServerProxy", endpoint, type(transport).__name__))
        if endpoint.endswith("/xmlrpc/2/common"):
            return FakeCommonProxy(calls)
        if endpoint.endswith("/xmlrpc/2/object"):
            return ProfileObjectProxy()
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(
        odoo_client_module.xmlrpc.client, "ServerProxy", fake_server_proxy
    )
    client = odoo_client_module.OdooClient(
        url="https://odoo.example.test",
        db="demo-db",
        username="demo-user",
        password="demo-password",
        timeout=3,
        verify_ssl=True,
    )

    profile = client.get_profile(module_limit=5)

    assert profile["server_version"] == {"server_version": "19.0"}
    assert profile["user_context"] == {"lang": "en_US"}
    assert profile["installed_modules"] == [
        {"name": "base", "shortdesc": "Base", "state": "installed"}
    ]
    assert profile["installed_module_count"] == 1


def test_json2_initialization_validates_bearer_without_xmlrpc(
    monkeypatch, odoo_client_module
):
    client, calls = build_json2_client(
        monkeypatch, odoo_client_module, responses=[{"lang": "en_US"}]
    )

    assert client.transport == "json2"
    assert client.uid is None
    assert calls == [
        {
            "url": "https://odoo.example.test/json/2/res.users/context_get",
            "headers": {
                "Authorization": "bearer json2-key",
                "Content-type": "application/json",
                "Accept": "application/json",
                "X-odoo-database": "demo-db",
            },
            "body": {},
            "timeout": 3,
            "context": calls[0]["context"],
        }
    ]
    assert calls[0]["context"] is not None


def test_json2_requests_omit_x_odoo_database_header_when_configured(
    monkeypatch, odoo_client_module
):
    client, calls = build_json2_client(
        monkeypatch,
        odoo_client_module,
        responses=[{"lang": "en_US"}, [{"id": 7}]],
        json2_database_header=False,
    )

    client.search_read("res.partner", [])

    assert "X-odoo-database" not in calls[0]["headers"]
    assert "X-odoo-database" not in calls[-1]["headers"]


def test_json2_search_read_maps_common_positional_args_to_named_payload(
    monkeypatch, odoo_client_module
):
    client, calls = build_json2_client(
        monkeypatch,
        odoo_client_module,
        responses=[{"lang": "en_US"}, [{"id": 7, "name": "Ada"}]],
    )

    result = client.search_read(
        "res.partner",
        [["is_company", "=", True]],
        fields=["name"],
        offset=5,
        limit=10,
        order="name ASC",
    )

    assert result == [{"id": 7, "name": "Ada"}]
    assert (
        calls[-1]["url"] == "https://odoo.example.test/json/2/res.partner/search_read"
    )
    assert calls[-1]["body"] == {
        "domain": [["is_company", "=", True]],
        "fields": ["name"],
        "offset": 5,
        "limit": 10,
        "order": "name ASC",
    }


def test_json2_read_records_maps_ids_to_route_payload(monkeypatch, odoo_client_module):
    client, calls = build_json2_client(
        monkeypatch,
        odoo_client_module,
        responses=[{"lang": "en_US"}, [{"id": 7, "name": "Ada"}]],
    )

    result = client.read_records("res.partner", [7], fields=["name"])

    assert result == [{"id": 7, "name": "Ada"}]
    assert calls[-1]["url"] == "https://odoo.example.test/json/2/res.partner/read"
    assert calls[-1]["body"] == {"ids": [7], "fields": ["name"]}


def test_json2_write_maps_record_ids_and_values_to_named_payload(
    monkeypatch, odoo_client_module
):
    client, calls = build_json2_client(
        monkeypatch,
        odoo_client_module,
        responses=[{"lang": "en_US"}, True],
    )

    result = client.execute_method(
        "res.partner",
        "write",
        [7],
        {"name": "Ada"},
        context={"lang": "en_US"},
    )

    assert result is True
    assert calls[-1]["url"] == "https://odoo.example.test/json/2/res.partner/write"
    assert calls[-1]["body"] == {
        "ids": [7],
        "vals": {"name": "Ada"},
        "context": {"lang": "en_US"},
    }


def test_json2_rejects_unknown_methods_with_positional_args(
    monkeypatch, odoo_client_module
):
    client, _ = build_json2_client(
        monkeypatch, odoo_client_module, responses=[{"lang": "en_US"}]
    )

    with pytest.raises(ValueError, match="requires keyword arguments"):
        client.execute_method("res.partner", "custom_method", ["positional"])


def test_json2_http_error_preserves_odoo_error_shape_and_redacts_debug_by_default(
    monkeypatch, odoo_client_module
):
    calls = []
    error_payload = {
        "name": "odoo.exceptions.AccessError",
        "message": "Access denied",
        "arguments": ["Access denied"],
        "context": {"model": "res.partner"},
        "debug": "traceback details",
    }

    def fake_urlopen(request, timeout=None, context=None):
        calls.append(request.full_url)
        if len(calls) == 1:
            return FakeJsonResponse({"lang": "en_US"})
        raise odoo_client_module.urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs=None,
            fp=BytesIO(json.dumps(error_payload).encode("utf-8")),
        )

    monkeypatch.setattr(odoo_client_module.urllib.request, "urlopen", fake_urlopen)
    client = odoo_client_module.OdooClient(
        url="https://odoo.example.test/",
        db="demo-db",
        username="demo-user",
        password="demo-password",
        timeout=3,
        verify_ssl=False,
        transport="json2",
        api_key="json2-key",
    )

    with pytest.raises(odoo_client_module.OdooJson2Error) as exc_info:
        client.execute_method("res.partner", "search_read", [])

    assert exc_info.value.status_code == 403
    assert exc_info.value.odoo_error == {
        "name": "odoo.exceptions.AccessError",
        "message": "Access denied",
        "arguments": ["Access denied"],
        "context": {"model": "res.partner"},
        "debug": "[redacted]",
    }


def test_json2_get_server_version_uses_web_version_endpoint(
    monkeypatch, odoo_client_module
):
    client, calls = build_json2_client(
        monkeypatch,
        odoo_client_module,
        responses=[{"lang": "en_US"}, {"server_version": "19.0"}],
    )

    assert client.get_server_version() == {"server_version": "19.0"}
    assert calls[-1]["url"] == "https://odoo.example.test/web/version"
    assert calls[-1]["body"] == {}


def _build_xmlrpc_client_with_lang(
    monkeypatch, odoo_client_module, lang
):
    calls = []

    def fake_server_proxy(endpoint, transport):
        if endpoint.endswith("/xmlrpc/2/common"):
            return FakeCommonProxy(calls)
        if endpoint.endswith("/xmlrpc/2/object"):
            return FakeObjectProxy(calls)
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    monkeypatch.setattr(
        odoo_client_module.xmlrpc.client, "ServerProxy", fake_server_proxy
    )
    client = odoo_client_module.OdooClient(
        url="odoo.example.test/",
        db="demo-db",
        username="demo-user",
        password="demo-password",
        timeout=3,
        verify_ssl=False,
        lang=lang,
    )
    return client, calls


def test_lang_is_injected_into_context_when_caller_omits_it(
    monkeypatch, odoo_client_module
):
    client, calls = _build_xmlrpc_client_with_lang(
        monkeypatch, odoo_client_module, lang="fr_FR"
    )

    client.execute_method("res.partner", "search_read", [[]])

    method, payload = calls[-1]
    assert method == "execute_kw"
    assert payload[6] == {"context": {"lang": "fr_FR"}}


def test_caller_supplied_context_lang_wins_over_default_lang(
    monkeypatch, odoo_client_module
):
    client, calls = _build_xmlrpc_client_with_lang(
        monkeypatch, odoo_client_module, lang="fr_FR"
    )

    client.execute_method(
        "res.partner",
        "search_read",
        [[]],
        context={"lang": "vi_VN", "tz": "Asia/Saigon"},
    )

    method, payload = calls[-1]
    assert method == "execute_kw"
    assert payload[6]["context"] == {"lang": "vi_VN", "tz": "Asia/Saigon"}


def test_no_lang_means_no_context_injected(monkeypatch, odoo_client_module):
    client, calls = _build_xmlrpc_client_with_lang(
        monkeypatch, odoo_client_module, lang=None
    )

    client.execute_method("res.partner", "search_read", [[]])

    method, payload = calls[-1]
    assert method == "execute_kw"
    assert payload[6] == {}


def test_empty_string_lang_is_treated_as_none(monkeypatch, odoo_client_module):
    client, calls = _build_xmlrpc_client_with_lang(
        monkeypatch, odoo_client_module, lang=""
    )

    client.execute_method("res.partner", "search_read", [[]])
    method, payload = calls[-1]
    assert method == "execute_kw"
    assert payload[6] == {}


def test_whitespace_only_lang_is_treated_as_none(
    monkeypatch, odoo_client_module
):
    client, calls = _build_xmlrpc_client_with_lang(
        monkeypatch, odoo_client_module, lang="   "
    )

    client.execute_method("res.partner", "search_read", [[]])
    method, payload = calls[-1]
    assert payload[6] == {}


def test_lang_merges_into_caller_context_without_overwriting_other_keys(
    monkeypatch, odoo_client_module
):
    client, calls = _build_xmlrpc_client_with_lang(
        monkeypatch, odoo_client_module, lang="fr_FR"
    )

    client.execute_method(
        "res.partner",
        "search_read",
        [[]],
        context={"tz": "Europe/Paris", "active_test": False},
    )
    method, payload = calls[-1]
    assert payload[6]["context"] == {
        "tz": "Europe/Paris",
        "active_test": False,
        "lang": "fr_FR",
    }


def test_lang_injection_propagates_to_json2_payload(monkeypatch, odoo_client_module):
    calls = []
    response_payloads = [{"lang": "en_US"}, [{"id": 1}]]

    def fake_urlopen(request, timeout=None, context=None):
        body = request.data.decode("utf-8") if request.data else "{}"
        calls.append(json.loads(body))
        return FakeJsonResponse(response_payloads.pop(0))

    monkeypatch.setattr(
        odoo_client_module.urllib.request, "urlopen", fake_urlopen
    )

    client = odoo_client_module.OdooClient(
        url="https://odoo.example.test",
        db="prod",
        username="api-user",
        password="legacy-password",
        transport="json2",
        api_key="api-key",
        lang="es_ES",
    )

    client.execute_method("res.partner", "search_read", [[]])

    # Last call is search_read with body containing context.lang
    last_body = calls[-1]
    assert last_body["context"] == {"lang": "es_ES"}


# ----- normalize_transport edge cases -------------------------------------


def test_normalize_transport_aliases_match():
    from odoo_mcp.odoo_client import normalize_transport

    assert normalize_transport("XML-RPC") == "xmlrpc"
    assert normalize_transport(" json_2 ") == "json2"


def test_normalize_transport_rejects_unsupported_value():
    from odoo_mcp.odoo_client import normalize_transport

    with pytest.raises(ValueError, match="Unsupported"):
        normalize_transport("graphql")


# ----- XML-RPC connection error paths -------------------------------------


def test_xmlrpc_connection_error_during_authenticate_raises_connection_error(
    monkeypatch, odoo_client_module
):
    class BoomCommonProxy:
        def authenticate(self, *args, **kwargs):
            raise ConnectionError("network down")

    def fake_server_proxy(endpoint, transport):
        if endpoint.endswith("/xmlrpc/2/common"):
            return BoomCommonProxy()
        return BoomCommonProxy()

    monkeypatch.setattr(
        odoo_client_module.xmlrpc.client, "ServerProxy", fake_server_proxy
    )
    with pytest.raises(ConnectionError, match="Failed to connect to Odoo server"):
        odoo_client_module.OdooClient(
            url="http://odoo.example.test",
            db="db",
            username="u",
            password="p",
        )


def test_xmlrpc_authenticate_returns_falsy_uid_raises_value_error(
    monkeypatch, odoo_client_module
):
    class FalsyAuthProxy:
        def authenticate(self, *args, **kwargs):
            return 0  # Falsy → "Authentication failed"

    def fake_server_proxy(endpoint, transport):
        return FalsyAuthProxy()

    monkeypatch.setattr(
        odoo_client_module.xmlrpc.client, "ServerProxy", fake_server_proxy
    )
    with pytest.raises(ValueError, match="Authentication failed"):
        odoo_client_module.OdooClient(
            url="http://odoo.example.test",
            db="db",
            username="u",
            password="bad",
        )


def test_xmlrpc_unexpected_authenticate_error_raises_value_error(
    monkeypatch, odoo_client_module
):
    class BoomProxy:
        def authenticate(self, *args, **kwargs):
            raise RuntimeError("something else")

    def fake_server_proxy(endpoint, transport):
        return BoomProxy()

    monkeypatch.setattr(
        odoo_client_module.xmlrpc.client, "ServerProxy", fake_server_proxy
    )
    with pytest.raises(ValueError, match="Failed to authenticate with Odoo"):
        odoo_client_module.OdooClient(
            url="http://odoo.example.test",
            db="db",
            username="u",
            password="p",
        )


# ----- JSON-2 connection / config errors ---------------------------------


def test_json2_init_requires_api_key_or_password(monkeypatch, odoo_client_module):
    """JSON-2 transport must reject empty bearer config."""
    with pytest.raises(ValueError, match="JSON-2 transport requires"):
        odoo_client_module.OdooClient(
            url="https://odoo.example.test",
            db="db",
            username="u",
            password="",  # falsy
            transport="json2",
            api_key=None,
        )


def test_json2_connection_error_during_validation_propagates(
    monkeypatch, odoo_client_module
):
    def fake_urlopen(request, timeout=None, context=None):
        raise ConnectionError("dns broke")

    monkeypatch.setattr(odoo_client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ConnectionError, match="Failed to connect to Odoo server"):
        odoo_client_module.OdooClient(
            url="https://odoo.example.test",
            db="db",
            username="u",
            password="p",
            transport="json2",
            api_key="k",
        )


def test_json2_unexpected_authentication_error(monkeypatch, odoo_client_module):
    def fake_urlopen(request, timeout=None, context=None):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(odoo_client_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ValueError, match="Failed to authenticate with Odoo JSON-2"):
        odoo_client_module.OdooClient(
            url="https://odoo.example.test",
            db="db",
            username="u",
            password="p",
            transport="json2",
            api_key="k",
        )


# ----- _build_json2_payload error branches --------------------------------


def test_json2_too_many_positional_args_raises(monkeypatch, odoo_client_module):
    client, _ = build_json2_client(
        monkeypatch, odoo_client_module, responses=[{"lang": "en_US"}]
    )
    with pytest.raises(ValueError, match="too many positional"):
        client.execute_method(
            "res.partner",
            "search_read",
            "dom",
            "fields",
            0,
            1,
            "asc",
            "extra",  # > 5 mapped names
        )


def test_json2_duplicate_positional_and_keyword_raises(monkeypatch, odoo_client_module):
    client, _ = build_json2_client(
        monkeypatch, odoo_client_module, responses=[{"lang": "en_US"}]
    )
    with pytest.raises(ValueError, match="both positionally and as a keyword"):
        client.execute_method(
            "res.partner",
            "search_read",
            [["id", ">", 0]],
            domain="dup",
        )


def test_json2_call_without_api_key_raises_after_init(monkeypatch, odoo_client_module):
    client, _ = build_json2_client(
        monkeypatch, odoo_client_module, responses=[{"lang": "en_US"}]
    )
    # Wipe the api_key after init to hit the runtime guard in _json2_call
    client.api_key = None
    with pytest.raises(ValueError, match="JSON-2 API key is not configured"):
        client._json2_call("res.partner", "search_read", {})


# ----- _json2_call response error branches --------------------------------


def test_json2_call_url_error_translated_to_connection_error(
    monkeypatch, odoo_client_module
):
    client, _ = build_json2_client(
        monkeypatch, odoo_client_module, responses=[{"lang": "en_US"}]
    )

    def boom(request, timeout=None, context=None):
        raise odoo_client_module.urllib.error.URLError("dns timeout")

    monkeypatch.setattr(odoo_client_module.urllib.request, "urlopen", boom)
    with pytest.raises(ConnectionError, match="dns timeout"):
        client.execute_method("res.partner", "search_read", [])


def test_json2_call_returns_none_for_empty_response_body(
    monkeypatch, odoo_client_module
):
    client, _ = build_json2_client(
        monkeypatch, odoo_client_module, responses=[{"lang": "en_US"}]
    )

    class EmptyResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b""

    monkeypatch.setattr(
        odoo_client_module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: EmptyResp(),
    )
    assert client.execute_method("res.partner", "search_read", []) is None


def test_json2_call_invalid_json_response_raises_value_error(
    monkeypatch, odoo_client_module
):
    client, _ = build_json2_client(
        monkeypatch, odoo_client_module, responses=[{"lang": "en_US"}]
    )

    class BrokenResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"not-json"

    monkeypatch.setattr(
        odoo_client_module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: BrokenResp(),
    )
    with pytest.raises(ValueError, match="returned invalid JSON"):
        client.execute_method("res.partner", "search_read", [])


# ----- public client API error paths --------------------------------------


def test_get_server_version_swallows_exception_and_returns_error(
    monkeypatch, odoo_client_module
):
    client, _ = build_client(monkeypatch, odoo_client_module)

    class BoomCommon:
        def version(self):
            raise RuntimeError("server down")

    client._common = BoomCommon()
    result = client.get_server_version()
    assert "error" in result and "server down" in result["error"]


def test_get_user_context_swallows_exception(monkeypatch, odoo_client_module):
    client, _ = build_client(monkeypatch, odoo_client_module)

    def boom(*args, **kwargs):
        raise RuntimeError("rpc fail")

    client._models.execute_kw = boom  # type: ignore[attr-defined]
    result = client.get_user_context()
    assert "error" in result


def test_get_installed_modules_returns_empty_list_on_error(
    monkeypatch, odoo_client_module
):
    client, _ = build_client(monkeypatch, odoo_client_module)

    def boom(*args, **kwargs):
        raise RuntimeError("rpc")

    client._models.execute_kw = boom  # type: ignore[attr-defined]
    assert client.get_installed_modules() == []


def test_http_get_json_rejects_non_dict_response(monkeypatch, odoo_client_module):
    client, _ = build_client(monkeypatch, odoo_client_module)

    class JsonListResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'["a","b"]'

    monkeypatch.setattr(
        odoo_client_module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: JsonListResp(),
    )
    with pytest.raises(ValueError, match="did not return a JSON object"):
        client._http_get_json("/web/version")


def test_get_models_returns_empty_list_when_search_returns_empty(
    monkeypatch, odoo_client_module
):
    client, _ = build_client(monkeypatch, odoo_client_module)

    def fake_execute_kw(*args):
        method = args[4]
        if method == "search":
            return []
        return []

    client._models.execute_kw = fake_execute_kw  # type: ignore[attr-defined]
    result = client.get_models()
    assert result["model_names"] == []
    assert "error" in result


def test_get_models_returns_full_payload_with_model_details(
    monkeypatch, odoo_client_module
):
    client, _ = build_client(monkeypatch, odoo_client_module)

    def fake_execute_kw(*args):
        method = args[4]
        if method == "search":
            return [1, 2]
        if method == "read":
            return [
                {"model": "res.partner", "name": "Contact"},
                {"model": "res.users", "name": "User"},
            ]
        raise AssertionError("unexpected method")

    client._models.execute_kw = fake_execute_kw  # type: ignore[attr-defined]
    result = client.get_models()
    assert result["model_names"] == ["res.partner", "res.users"]
    assert result["models_details"]["res.users"]["name"] == "User"


def test_get_models_handles_execute_method_failure(monkeypatch, odoo_client_module):
    client, _ = build_client(monkeypatch, odoo_client_module)

    def boom(*args):
        raise RuntimeError("fail")

    client._models.execute_kw = boom  # type: ignore[attr-defined]
    result = client.get_models()
    assert result["model_names"] == []
    assert "error" in result


def test_get_model_info_returns_error_when_model_not_found(
    monkeypatch, odoo_client_module
):
    client, _ = build_client(monkeypatch, odoo_client_module)
    client._models.execute_kw = lambda *args: []  # type: ignore[attr-defined]
    result = client.get_model_info("res.ghost")
    assert "not found" in result["error"]


def test_get_model_info_handles_execute_failure(monkeypatch, odoo_client_module):
    client, _ = build_client(monkeypatch, odoo_client_module)

    def boom(*args):
        raise RuntimeError("rpc")

    client._models.execute_kw = boom  # type: ignore[attr-defined]
    result = client.get_model_info("res.partner")
    assert "rpc" in result["error"]


def test_get_model_fields_handles_execute_failure(monkeypatch, odoo_client_module):
    client, _ = build_client(monkeypatch, odoo_client_module)

    def boom(*args):
        raise RuntimeError("rpc")

    client._models.execute_kw = boom  # type: ignore[attr-defined]
    result = client.get_model_fields("res.partner")
    assert "rpc" in result["error"]


def test_search_read_handles_execute_failure_returns_empty_list(
    monkeypatch, odoo_client_module
):
    client, _ = build_client(monkeypatch, odoo_client_module)

    def boom(*args):
        raise RuntimeError("rpc")

    client._models.execute_kw = boom  # type: ignore[attr-defined]
    assert client.search_read("res.partner", [], offset=1) == []


def test_read_records_handles_execute_failure_returns_empty_list(
    monkeypatch, odoo_client_module
):
    client, _ = build_client(monkeypatch, odoo_client_module)

    def boom(*args):
        raise RuntimeError("rpc")

    client._models.execute_kw = boom  # type: ignore[attr-defined]
    assert client.read_records("res.partner", [1]) == []


def test_json2_get_server_version_falls_back_to_web_version_on_error(
    monkeypatch, odoo_client_module
):
    """get_server_version uses _http_get_json on JSON-2 transport; if it
    fails, the helper returns {error: ...} rather than raising."""
    calls = []

    def fake_urlopen(request, timeout=None, context=None):
        calls.append(request.full_url)
        if request.full_url.endswith("/json/2/res.users/context_get"):
            return FakeJsonResponse({"lang": "en_US"})
        raise odoo_client_module.urllib.error.URLError("nope")

    monkeypatch.setattr(odoo_client_module.urllib.request, "urlopen", fake_urlopen)
    client = odoo_client_module.OdooClient(
        url="https://odoo.example.test",
        db="db",
        username="u",
        password="p",
        transport="json2",
        api_key="k",
    )
    info = client.get_server_version()
    assert "error" in info


# ----- RedirectTransport coverage ----------------------------------------


def test_redirect_transport_make_connection_uses_https_with_verify(
    odoo_client_module,
):
    transport = odoo_client_module.RedirectTransport(
        timeout=3, use_https=True, verify_ssl=True
    )
    conn = transport.make_connection("odoo.example.test")
    import http.client as _httpc

    assert isinstance(conn, _httpc.HTTPSConnection)


def test_redirect_transport_make_connection_uses_http_when_not_https(
    odoo_client_module,
):
    transport = odoo_client_module.RedirectTransport(
        timeout=3, use_https=False, verify_ssl=True
    )
    conn = transport.make_connection("odoo.example.test")
    import http.client as _httpc

    assert isinstance(conn, _httpc.HTTPConnection)
    assert not isinstance(conn, _httpc.HTTPSConnection)


def test_redirect_transport_make_connection_uses_https_unverified(
    odoo_client_module,
):
    transport = odoo_client_module.RedirectTransport(
        timeout=3, use_https=True, verify_ssl=False
    )
    conn = transport.make_connection("odoo.example.test")
    import http.client as _httpc

    assert isinstance(conn, _httpc.HTTPSConnection)


def test_redirect_transport_uses_proxy_when_configured(odoo_client_module):
    transport = odoo_client_module.RedirectTransport(
        timeout=3,
        use_https=False,
        verify_ssl=True,
        proxy="http://proxy.example.test:3128",
    )
    conn = transport.make_connection("odoo.example.test")
    import http.client as _httpc

    assert isinstance(conn, _httpc.HTTPConnection)


def test_redirect_transport_rejects_invalid_proxy(odoo_client_module):
    transport = odoo_client_module.RedirectTransport(
        timeout=3,
        use_https=False,
        verify_ssl=True,
        proxy="not-a-valid-url",
    )
    with pytest.raises(ValueError, match="Invalid HTTP_PROXY"):
        transport.make_connection("odoo.example.test")


def test_redirect_transport_make_connection_handles_tuple_host(odoo_client_module):
    transport = odoo_client_module.RedirectTransport(
        timeout=3, use_https=False, verify_ssl=True
    )
    # XML-RPC sometimes hands a (host, x509) tuple
    conn = transport.make_connection(("odoo.example.test", {}))
    import http.client as _httpc

    assert isinstance(conn, _httpc.HTTPConnection)


def test_redirect_transport_request_follows_redirect_with_full_url(
    monkeypatch, odoo_client_module
):
    transport = odoo_client_module.RedirectTransport(timeout=3, use_https=False)
    calls = []

    class FakeProtocolError(Exception):
        pass

    state = {"redirect_yielded": False}

    def fake_request(self, host, handler, request_body, verbose=False):
        calls.append((host, handler))
        if not state["redirect_yielded"]:
            state["redirect_yielded"] = True
            err = odoo_client_module.xmlrpc.client.ProtocolError(
                f"{host}{handler}",
                302,
                "Found",
                {"location": "http://new.example.test/new/path?x=1"},
            )
            raise err
        return {"ok": True}

    # Patch the parent class request method
    monkeypatch.setattr(
        odoo_client_module.xmlrpc.client.Transport, "request", fake_request
    )
    result = transport.request("odoo.example.test", "/old", b"<body/>")
    assert result == {"ok": True}
    # Second iteration uses redirected host/path
    assert calls[1][0] == "new.example.test"
    assert calls[1][1] == "/new/path?x=1"


def test_redirect_transport_request_re_raises_non_redirect_protocol_error(
    monkeypatch, odoo_client_module
):
    transport = odoo_client_module.RedirectTransport(timeout=3, use_https=False)

    def fake_request(self, host, handler, request_body, verbose=False):
        raise odoo_client_module.xmlrpc.client.ProtocolError(
            f"{host}{handler}",
            500,
            "Server Error",
            {},
        )

    monkeypatch.setattr(
        odoo_client_module.xmlrpc.client.Transport, "request", fake_request
    )
    with pytest.raises(odoo_client_module.xmlrpc.client.ProtocolError):
        transport.request("odoo.example.test", "/path", b"<body/>")


def test_redirect_transport_request_re_raises_unexpected_exceptions(
    monkeypatch, odoo_client_module, capsys
):
    transport = odoo_client_module.RedirectTransport(timeout=3, use_https=False)

    def fake_request(self, host, handler, request_body, verbose=False):
        raise RuntimeError("fatal")

    monkeypatch.setattr(
        odoo_client_module.xmlrpc.client.Transport, "request", fake_request
    )
    with pytest.raises(RuntimeError, match="fatal"):
        transport.request("odoo.example.test", "/path", b"<body/>")
    err = capsys.readouterr().err
    assert "Error during request" in err


def test_redirect_transport_request_aborts_after_max_redirects(
    monkeypatch, odoo_client_module
):
    transport = odoo_client_module.RedirectTransport(
        timeout=3, use_https=False, max_redirects=2
    )

    def fake_request(self, host, handler, request_body, verbose=False):
        raise odoo_client_module.xmlrpc.client.ProtocolError(
            f"{host}{handler}",
            301,
            "Moved",
            {"location": "http://other.example.test/x"},
        )

    monkeypatch.setattr(
        odoo_client_module.xmlrpc.client.Transport, "request", fake_request
    )
    with pytest.raises(odoo_client_module.xmlrpc.client.ProtocolError) as exc_info:
        transport.request("odoo.example.test", "/start", b"<body/>")
    assert "Too many redirects" in str(exc_info.value)


def test_json2_call_with_no_positional_args_returns_kwargs_payload(
    monkeypatch, odoo_client_module
):
    """When no positional args are passed, _build_json2_payload returns kwargs unchanged."""
    client, calls = build_json2_client(
        monkeypatch,
        odoo_client_module,
        responses=[{"lang": "en_US"}, {"value": True}],
    )
    # Pure kwargs: no positional args. Hits the early-return at line 210.
    client.execute_method("res.partner", "search_read", domain=[], limit=5)
    assert calls[-1]["body"] == {"domain": [], "limit": 5}


def test_get_model_fields_returns_full_metadata_dict(monkeypatch, odoo_client_module):
    client, calls = build_client(monkeypatch, odoo_client_module)

    def fake_execute_kw(*args):
        method = args[4]
        assert method == "fields_get"
        return {"name": {"type": "char"}, "active": {"type": "boolean"}}

    client._models.execute_kw = fake_execute_kw  # type: ignore[attr-defined]
    fields = client.get_model_fields("res.partner")
    assert fields == {"name": {"type": "char"}, "active": {"type": "boolean"}}


def test_redirect_transport_request_decodes_bytes_location_header(
    monkeypatch, odoo_client_module
):
    transport = odoo_client_module.RedirectTransport(timeout=3, use_https=False)
    state = {"redirected": False}

    def fake_request(self, host, handler, request_body, verbose=False):
        if not state["redirected"]:
            state["redirected"] = True
            raise odoo_client_module.xmlrpc.client.ProtocolError(
                f"{host}{handler}",
                301,
                "Moved",
                {"location": b"/relative/path"},  # bytes header value
            )
        return {"ok": True}

    monkeypatch.setattr(
        odoo_client_module.xmlrpc.client.Transport, "request", fake_request
    )
    assert transport.request("odoo.example.test", "/start", b"<body/>") == {"ok": True}
