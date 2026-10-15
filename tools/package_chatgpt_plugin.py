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


def build_plugin_manifest(server_url: str, demo_recording_url: str | None = None) -> dict:
    """Fill public listing URLs from the deployed Dux origin."""
    mcp_url = normalize_mcp_url(server_url)
    origin = mcp_url.removesuffix("/mcp")
    plugin = json.loads((PACKAGE / "plugin.json").read_text(encoding="utf-8"))
    interface = plugin["extensions"]["com.openai"]["interface"]
    interface.update({
        "websiteURL": origin,
        "supportURL": "https://github.com/datoga/dux-fps-poc/issues",
        "privacyPolicyURL": f"{origin}/privacy",
        "termsOfServiceURL": f"{origin}/terms",
    })
    review = plugin["extensions"]["com.openai"].setdefault("review", {})
    review["commerce"] = False
    review["commerce_description"] = "Dux no vende productos ni procesa pagos."
    if demo_recording_url:
        demo = urlparse(demo_recording_url.strip())
        if demo.scheme != "https" or not demo.hostname or demo.username or demo.password:
            raise ValueError("The demo recording URL must be a public HTTPS URL")
        review["demo_recording_url"] = demo_recording_url.strip()
    return plugin


def build_plugin_package(output: Path, server_url: str, demo_recording_url: str | None = None) -> str:
    """Create the portable plugin ZIP, including its skill and MCP configuration."""
    mcp_url = normalize_mcp_url(server_url)
    plugin = build_plugin_manifest(server_url, demo_recording_url)
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
    skill_root = PACKAGE / "skills"
    if not any(skill_root.rglob("SKILL.md")):
        raise ValueError("The portable plugin must include at least one valid skill")

    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr("plugin.json", json.dumps(plugin, ensure_ascii=False, indent=2) + "\n")
        archive.writestr("mcp.json", json.dumps(mcp, ensure_ascii=False, indent=2) + "\n")
        for subtree in ("assets", "skills"):
            for path in sorted((PACKAGE / subtree).rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(PACKAGE).as_posix())
    return mcp_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default=os.environ.get("DUX_PUBLIC_URL"), help="Public HTTPS origin (or its /mcp endpoint)")
    parser.add_argument("--demo-recording-url", default=os.environ.get("DUX_DEMO_RECORDING_URL"), help="Reviewer-accessible HTTPS walkthrough video; required before submitting for public review")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="ZIP output path")
    args = parser.parse_args()
    if not args.server_url:
        parser.error("Set DUX_PUBLIC_URL or pass --server-url after deploying the server")
    mcp_url = build_plugin_package(args.output, args.server_url, args.demo_recording_url)
    print(f"Created {args.output} for {mcp_url}")
    if not args.demo_recording_url:
        print("DRAFT ONLY: add a reviewer-accessible DUX_DEMO_RECORDING_URL before submitting for public review.")
    print("Before submission, replace the draft privacy and terms pages with reviewed production policies and complete publisher verification in OpenAI.")


if __name__ == "__main__":
    main()
