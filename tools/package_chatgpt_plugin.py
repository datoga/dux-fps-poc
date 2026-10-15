#!/usr/bin/env python3
"""Package Dux's portable ChatGPT plugin for a deployed HTTPS MCP endpoint."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "chatgpt-plugin"
OUTPUT = ROOT / "dist" / "dux-chatgpt-plugin.zip"


def normalize_mcp_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Provide a public HTTPS origin, for example https://play.example.com")
    if parsed.path not in ("", "/", "/mcp"):
        raise ValueError("Use only the origin or the /mcp endpoint")
    return value.rstrip("/") if parsed.path == "/mcp" else value.rstrip("/") + "/mcp"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default=os.environ.get("DUX_PUBLIC_URL"), help="Public HTTPS origin (or its /mcp endpoint)")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="ZIP output path")
    args = parser.parse_args()
    if not args.server_url:
        parser.error("Set DUX_PUBLIC_URL or pass --server-url after deploying the server")
    mcp_url = normalize_mcp_url(args.server_url)
    plugin = json.loads((PACKAGE / "plugin.json").read_text(encoding="utf-8"))
    mcp = {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "dux": {
                "type": "streamable-http",
                "url": mcp_url,
                "extensions": {"com.openai": {"auth": {"type": "none"}}},
            }
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.output, "w", ZIP_DEFLATED) as archive:
        archive.write(PACKAGE / "plugin.json", "plugin.json")
        archive.write(PACKAGE / "assets" / "dux-logo.png", "assets/dux-logo.png")
        archive.writestr("mcp.json", json.dumps(mcp, ensure_ascii=False, indent=2) + "\n")
    print(f"Created {args.output} for {mcp_url}")


if __name__ == "__main__":
    main()
