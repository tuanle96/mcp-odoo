import json
from io import BytesIO

import pytest


class FakeCommonProxy:
    def __init__(self, calls):
        self.calls = calls

    def authenticate(self, db, username, password, context):
        self.calls.append(("authenticate", db, username, password, context))
        return 42


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
