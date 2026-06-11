import base64
import importlib

from tests.test_batch_write import FakeCtx


class AttachmentClient:
    def __init__(self, file_size=10, attachment_type="binary", datas=None):
        self.file_size = file_size
        self.attachment_type = attachment_type
        self.datas = (
            datas
            if datas is not None
            else base64.b64encode(b"hello pdf!").decode("ascii")
        )
        self.read_calls = []

    def execute_method(self, model, method, *args, **kwargs):
        assert model == "ir.attachment" and method == "read"
        fields = kwargs.get("fields", [])
        self.read_calls.append(fields)
        if "datas" in fields:
            return [{"id": 9, "datas": self.datas}]
        return [
            {
                "id": 9,
                "name": "invoice.pdf",
                "mimetype": "application/pdf",
                "file_size": self.file_size,
                "type": self.attachment_type,
                "url": False,
                "res_model": "account.move",
                "res_id": 4,
                "checksum": "abc",
                "create_date": "2026-06-10",
            }
        ]


def test_read_attachment_returns_metadata_and_content():
    server = importlib.import_module("odoo_mcp.server")
    client = AttachmentClient()

    report = server.read_attachment(FakeCtx(client), 9)

    assert report["success"] is True
    assert report["attachment"]["name"] == "invoice.pdf"
    assert report["data_included"] is True
    assert base64.b64decode(report["data_base64"]) == b"hello pdf!"
    # Metadata read first, then a separate datas read.
    assert len(client.read_calls) == 2


def test_read_attachment_omits_content_over_cap(monkeypatch):
    server = importlib.import_module("odoo_mcp.server")
    monkeypatch.setenv("ODOO_MCP_MAX_ATTACHMENT_BYTES", "5")
    client = AttachmentClient(file_size=10)

    report = server.read_attachment(FakeCtx(client), 9)

    assert report["success"] is True
    assert report["data_included"] is False
    assert report["data_base64"] is None
    assert any("cap is 5" in warning for warning in report["warnings"])
    # The datas field was never fetched.
    assert len(client.read_calls) == 1


def test_read_attachment_rechecks_fetched_size(monkeypatch):
    server = importlib.import_module("odoo_mcp.server")
    monkeypatch.setenv("ODOO_MCP_MAX_ATTACHMENT_BYTES", "8")
    # file_size lies (under cap) but the actual payload is bigger.
    big = base64.b64encode(b"x" * 100).decode("ascii")
    client = AttachmentClient(file_size=4, datas=big)

    report = server.read_attachment(FakeCtx(client), 9)

    assert report["data_included"] is False
    assert any("exceeded the cap" in warning for warning in report["warnings"])


def test_read_attachment_url_type_and_not_found():
    server = importlib.import_module("odoo_mcp.server")
    url_client = AttachmentClient(attachment_type="url")
    report = server.read_attachment(FakeCtx(url_client), 9)
    assert report["success"] is True
    assert report["data_included"] is False
    assert any("URL-type" in warning for warning in report["warnings"])

    class EmptyClient:
        def execute_method(self, *args, **kwargs):
            return []

    missing = server.read_attachment(FakeCtx(EmptyClient()), 9)
    assert missing["success"] is False
    assert "not found" in missing["error"]

    invalid = server.read_attachment(FakeCtx(url_client), 0)
    assert invalid["success"] is False


def test_max_attachment_bytes_clamped(monkeypatch):
    server = importlib.import_module("odoo_mcp.server")
    monkeypatch.setenv("ODOO_MCP_MAX_ATTACHMENT_BYTES", "999999999999")
    assert server.max_attachment_bytes() == server.ATTACHMENT_BYTES_HARD_CAP
    monkeypatch.setenv("ODOO_MCP_MAX_ATTACHMENT_BYTES", "junk")
    assert server.max_attachment_bytes() == server.DEFAULT_MAX_ATTACHMENT_BYTES
