{
    "name": "MCP Smoke Access",
    "summary": "Packaged addon used by mcp-odoo Docker smoke tests.",
    "version": "1.0.0",
    "license": "LGPL-3",
    "author": "mcp-odoo smoke",
    "depends": ["base"],
    "data": [
        "security/mcp_smoke_access_security.xml",
        "data/mcp_smoke_partners.xml",
    ],
    "installable": True,
    "application": False,
}
