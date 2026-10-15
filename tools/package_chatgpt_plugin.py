#!/usr/bin/env python3
"""Package Dux's portable ChatGPT plugin for a deployed HTTPS MCP endpoint."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "chatgpt-plugin"
OUTPUT = ROOT / "dist" / "dux-chatgpt-plugin.zip"
REQUIRED_TOOLS = {"open_creator", "create_game", "get_game"}
PLACEHOLDER_POLICY_MARKERS = (
    "before public launch",
    "must be added before public launch",
    "draft terms",
)


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


def _require_public_host(value: str, label: str) -> None:
    host = urlparse(value).hostname
    if not host:
        raise ValueError(f"{label} must have a public hostname")
    host = host.lower().rstrip(".")
    reserved_suffixes = (".localhost", ".local", ".test", ".example", ".invalid")
    reserved_domains = ("example.com", "example.net", "example.org")
    if host == "localhost" or host.endswith(reserved_suffixes) or any(
        host == domain or host.endswith("." + domain) for domain in reserved_domains
    ):
        raise ValueError(f"{label} must not use a local or reserved example hostname")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError(f"{label} must use a globally reachable IP address")


def _read_url(url: str, request: Request | None = None, *, read_limit: int | None = None) -> tuple[int, bytes]:
    try:
        with urlopen(request or url, timeout=20) as response:
            return getattr(response, "status", 200), response.read() if read_limit is None else response.read(read_limit)
    except (OSError, URLError) as error:
        raise ValueError(f"Could not reach {url}: {error}") from error


def _mcp_request(endpoint: str, request_id: int, method: str, params: dict | None = None) -> dict:
    payload = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}).encode()
    request = Request(endpoint, data=payload, headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    status, body = _read_url(endpoint, request)
    if status != 200:
        raise ValueError(f"MCP {method} returned HTTP {status}")
    try:
        result = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError(f"MCP {method} did not return JSON") from error
    if "error" in result or "result" not in result:
        raise ValueError(f"MCP {method} failed: {result.get('error', 'missing result')}")
    return result["result"]


def verify_live_deployment(server_url: str, demo_recording_url: str | None) -> None:
    """Fail closed unless the public site, MCP tools, policies, and demo are reachable."""
    if not demo_recording_url:
        raise ValueError("Live release verification requires a reviewer-accessible demo recording URL")
    endpoint = normalize_mcp_url(server_url)
    origin = endpoint.removesuffix("/mcp")
    _require_public_host(endpoint, "MCP endpoint")
    _require_public_host(demo_recording_url, "Demo recording URL")

    status, body = _read_url(origin + "/v1/health")
    if status != 200:
        raise ValueError(f"Health endpoint returned HTTP {status}")
    try:
        health = json.loads(body)
    except json.JSONDecodeError as error:
        raise ValueError("Health endpoint did not return JSON") from error
    if health.get("ok") is not True or health.get("assetsReady") is not True or health.get("engineReady") is not True:
        raise ValueError("Live server is not ready: health, LibreQuake assets, and browser runtime must all be ready")

    for path in ("/", "/engine/index.js", "/engine/index.wasm", "/privacy", "/terms"):
        status, body = _read_url(origin + path)
        if status != 200 or not body:
            raise ValueError(f"Live endpoint {path} returned HTTP {status} or an empty response")
        if path in ("/privacy", "/terms"):
            text = body.decode("utf-8", "replace").lower()
            if any(marker in text for marker in PLACEHOLDER_POLICY_MARKERS):
                raise ValueError(f"{path} still contains draft operator or policy placeholders")

    challenge = os.environ.get("DUX_OPENAI_APPS_CHALLENGE")
    if challenge:
        status, body = _read_url(origin + "/.well-known/openai-apps-challenge")
        if status != 200 or body.decode("utf-8", "replace") != challenge:
            raise ValueError("The OpenAI domain-verification challenge is not served with the configured token")

    initialized = _mcp_request(endpoint, 1, "initialize", {
        "protocolVersion": "2026-01-26", "capabilities": {},
        "clientInfo": {"name": "dux-release-check", "version": "1"},
    })
    if initialized.get("serverInfo", {}).get("name") != "dux-game-mcp":
        raise ValueError("MCP initialize returned an unexpected server identity")
    tools = _mcp_request(endpoint, 2, "tools/list").get("tools", [])
    if not REQUIRED_TOOLS.issubset({tool.get("name") for tool in tools}):
        raise ValueError("MCP endpoint is missing one or more required Dux tools")
    resources = _mcp_request(endpoint, 3, "resources/read", {"uri": "ui://widget/dux-create.html"}).get("contents", [])
    widget = next((item.get("text", "") for item in resources if item.get("uri") == "ui://widget/dux-create.html"), "")
    if not widget or "window.openai.selectFiles" not in widget or "Crear partida" not in widget:
        raise ValueError("Live MCP widget is missing the image picker or publish flow")

    demo_request = Request(demo_recording_url, headers={"Range": "bytes=0-0"})
    status, body = _read_url(demo_recording_url, demo_request, read_limit=1)
    if status not in (200, 206) or not body:
        raise ValueError(f"Demo recording URL returned HTTP {status} or an empty response")


def build_plugin_package(output: Path, server_url: str, demo_recording_url: str | None = None,
                         *, verify_live: bool = False) -> str:
    """Create the portable plugin ZIP, including its skill and MCP configuration."""
    mcp_url = normalize_mcp_url(server_url)
    plugin = build_plugin_manifest(server_url, demo_recording_url)
    if verify_live:
        verify_live_deployment(server_url, demo_recording_url)
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
    parser.add_argument("--verify-live", action="store_true", help="Check the deployed web, runtime, privacy/terms pages, MCP tools, widget, and demo URL before packaging")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="ZIP output path")
    args = parser.parse_args()
    if not args.server_url:
        parser.error("Set DUX_PUBLIC_URL or pass --server-url after deploying the server")
    try:
        mcp_url = build_plugin_package(args.output, args.server_url, args.demo_recording_url, verify_live=args.verify_live)
    except ValueError as error:
        parser.error(str(error))
    print(f"Created {args.output} for {mcp_url}")
    if args.verify_live:
        print("Live release checks passed for the public web, MCP server, policies, runtime, and demo recording.")
    else:
        print("DRAFT ONLY: package was not live-verified. Run again with --verify-live before submitting for public review.")
    print("Before submission, replace the draft privacy and terms pages with reviewed production policies and complete publisher verification in OpenAI.")


if __name__ == "__main__":
    main()
