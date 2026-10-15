import base64
from email.message import Message
from io import BytesIO
import json
import struct
import tempfile
from types import MethodType
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from tools.package_chatgpt_plugin import build_plugin_manifest, normalize_mcp_url

import server
from engine.build_runtime import curated_pak


def make_test_bsp(textures: tuple[tuple[str, int], ...]) -> bytes:
    """Build a tiny BSP v29 with a texture table for the requested records."""
    records = []
    for name, size in textures:
        encoded = name.encode("ascii")[:15].ljust(16, b"\0")
        offsets = (40, 40 + size * size, 40 + size * size * 5 // 4,
                   40 + size * size * 21 // 16)
        record = encoded + struct.pack("<II4I", size, size, *offsets)
        record += bytes(size * size * 21 // 16)
        records.append(record)
    texture_lump = bytearray(struct.pack("<i", len(records)) + b"\0" * (4 * len(records)))
    for index, record in enumerate(records):
        struct.pack_into("<i", texture_lump, 4 + index * 4, len(texture_lump))
        texture_lump.extend(record)

    bsp = bytearray(124)
    struct.pack_into("<i", bsp, 0, 29)
    bsp.extend(texture_lump)
    struct.pack_into("<II", bsp, 4 + 2 * 8, 124, len(texture_lump))
    return bytes(bsp)


@contextmanager
def fixture_paks():
    bsp = make_test_bsp(((server.TARGET_TEXTURE, 16),))
    campaign_bsps = {
        name: make_test_bsp(tuple((texture, 16) for texture in textures))
        for name, textures in server.CAMPAIGN_TEXTURES_BY_MAP.items()
    }
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        pak0 = root / "pak0.pak"
        pak1 = root / "pak1.pak"
        pak0.write_bytes(server.make_pak({
            server.TARGET_MAP: bsp,
            **campaign_bsps,
            "gfx/palette.lmp": bytes(range(256)) * 3,
        }))
        pak1.write_bytes(server.make_pak({"gfx/pop.lmp": b"test"}))
        yield pak0, pak1, bsp


class PakTests(unittest.TestCase):
    def test_pak_round_trip(self):
        contents = server.make_pak({"maps/test.bsp": b"map", "gfx/test.lmp": b"pixels"})
        self.assertEqual(server.pak_entries(contents), {"maps/test.bsp": b"map", "gfx/test.lmp": b"pixels"})

    def test_runtime_keeps_level_maps_but_drops_optional_lit_and_demo_files(self):
        source = server.make_pak({
            "maps/start.bsp": b"start",
            "maps/lq_e1m1.bsp": b"episode",
            "maps/unused.lit": b"lightmap",
            "demos/intro.dem": b"demo",
        })
        with tempfile.TemporaryDirectory() as temp:
            pak_path = Path(temp) / "pak0.pak"
            pak_path.write_bytes(source)
            self.assertEqual(curated_pak(pak_path), server.make_pak({
                "maps/start.bsp": b"start",
                "maps/lq_e1m1.bsp": b"episode",
            }))

    def test_replacement_map_has_valid_lumps_and_custom_texture(self):
        with fixture_paks() as (pak0, _, bsp), patch.object(server, "PAK0", pak0):
            palette = server.extract_entry(pak0, "gfx/palette.lmp")
            width, height = server.TEXTURE_WIDTH, server.TEXTURE_HEIGHT
            rgba = bytes([255, 0, 255, 255]) * (width * height)
            modified = server.build_miptex(bsp, server.TARGET_TEXTURE, rgba, width, height, palette)
            self.assertEqual(struct.unpack_from("<i", modified)[0], 29)
            tex_offset, tex_length = server.get_lump(modified, 2)
            self.assertLessEqual(tex_offset + tex_length, len(modified))
            info = server.texture_info(modified, server.TARGET_TEXTURE)
            self.assertEqual((info[3], info[4][0]), (16, 16))
            entries = server.make_pak({server.TARGET_MAP: modified})
            self.assertEqual(server.pak_entries(entries)[server.TARGET_MAP], modified)

    def test_dimensions_rejected_before_packaging(self):
        with self.assertRaisesRegex(ValueError, "must be exactly"):
            server.make_game(bytes([0, 0, 0, 255]) * 64, 8, 8)

    def test_create_game_writes_customized_maps_and_preview(self):
        with fixture_paks() as (pak0, pak1, _), tempfile.TemporaryDirectory() as temp, \
                patch.object(server, "PAK0", pak0), patch.object(server, "PAK1", pak1), \
                patch.object(server, "DATA", Path(temp)):
            width, height = server.TEXTURE_WIDTH, server.TEXTURE_HEIGHT
            rgba = bytes([20, 180, 220, 255]) * (width * height)
            game_id, expires = server.make_game(rgba, width, height)
            game_dir = Path(temp) / game_id
            manifest = json.loads((game_dir / "manifest.json").read_text())
            pack = server.pak_entries((game_dir / "pak2.pak").read_bytes())
            modified = pack[server.TARGET_MAP]
            self.assertEqual(set(pack), {server.TARGET_MAP, *server.CAMPAIGN_TEXTURES_BY_MAP})
            self.assertEqual(manifest["gameId"], game_id)
            self.assertEqual(manifest["expiresAt"], expires)
            self.assertEqual((manifest["width"], manifest["height"]), (width, height))
            self.assertEqual((server.texture_info(modified, server.TARGET_TEXTURE)[3], server.texture_info(modified, server.TARGET_TEXTURE)[4][0]), (16, 16))
            for map_name, texture_names in server.CAMPAIGN_TEXTURES_BY_MAP.items():
                campaign = pack[map_name]
                for name in texture_names:
                    info = server.texture_info(campaign, name)
                    self.assertEqual((info[3], info[4][0]), (16, 16))
            self.assertEqual((game_dir / "texture.rgba").read_bytes(), rgba)

    def test_mcp_exposes_creator_and_tool_schema(self):
        handler = object.__new__(server.Handler)
        handler.server = SimpleNamespace(server_port=8000)
        result = handler._mcp_dispatch("tools/list", {})
        by_name = {tool["name"]: tool for tool in result["tools"]}
        self.assertEqual(set(by_name), {"open_creator", "create_game", "get_game"})
        self.assertEqual(by_name["create_game"]["inputSchema"]["properties"]["width"]["const"], server.TEXTURE_WIDTH)
        self.assertTrue(by_name["create_game"]["annotations"]["openWorldHint"])
        self.assertIn("playable", by_name["create_game"]["outputSchema"]["properties"])
        self.assertEqual(by_name["create_game"]["_meta"]["ui"]["resourceUri"], "ui://widget/dux-create.html")

    def test_mcp_resource_contains_widget_file_picker_and_bridge(self):
        handler = object.__new__(server.Handler)
        handler.server = SimpleNamespace(server_port=8000)
        result = handler._mcp_dispatch("resources/read", {"uri": "ui://widget/dux-create.html"})
        resource = result["contents"][0]
        self.assertEqual(resource["mimeType"], "text/html;profile=mcp-app")
        self.assertIn("window.openai.selectFiles", resource["text"])
        self.assertIn("ui/initialize", resource["text"])

    def test_mcp_create_game_receives_only_final_rgba_and_returns_share_url(self):
        handler = object.__new__(server.Handler)
        handler.server = SimpleNamespace(server_port=8123)
        width, height = server.TEXTURE_WIDTH, server.TEXTURE_HEIGHT
        rgba = bytes([17, 34, 51, 255]) * (width * height)
        with patch.object(server, "make_game", return_value=("a1b2c3d4e5f6", 2_000_000_000)) as make_game:
            result = handler._mcp_dispatch("tools/call", {"name": "create_game", "arguments": {
                "width": width, "height": height, "rgba": base64.b64encode(rgba).decode("ascii")
            }})
        make_game.assert_called_once_with(rgba, width, height)
        self.assertEqual(result["structuredContent"]["shareUrl"], "http://127.0.0.1:8123/play/a1b2c3d4e5f6")
        self.assertIsInstance(result["structuredContent"]["playable"], bool)
        self.assertNotIn("sourceImage", result["structuredContent"])

    def test_mcp_reports_invalid_base64_as_tool_error(self):
        handler = object.__new__(server.Handler)
        handler.server = SimpleNamespace(server_port=8000)
        result = handler._mcp_dispatch("tools/call", {"name": "create_game", "arguments": {
            "width": server.TEXTURE_WIDTH, "height": server.TEXTURE_HEIGHT, "rgba": "not base64!"
        }})
        self.assertTrue(result["isError"])


class MCPHttpTests(unittest.TestCase):
    def handler(self, path, body=b"", *, content_type="application/json"):
        handler = object.__new__(server.Handler)
        handler.path = path
        handler.headers = Message()
        handler.headers["Content-Type"] = content_type
        handler.headers["Accept"] = "application/json, text/event-stream"
        handler.headers["Content-Length"] = str(len(body))
        handler.rfile = BytesIO(body)
        handler.wfile = BytesIO()
        handler.server = SimpleNamespace(server_port=8000)
        handler.response_status = None
        handler.response_headers = {}
        handler.send_response = MethodType(lambda instance, status, *_: setattr(instance, "response_status", status), handler)
        handler.send_header = MethodType(lambda instance, name, value: instance.response_headers.__setitem__(name, value), handler)
        handler.end_headers = MethodType(lambda instance: None, handler)
        handler.log_error = MethodType(lambda *_: None, handler)
        return handler

    def post_mcp(self, request):
        handler = self.handler("/mcp", json.dumps(request).encode())
        handler.do_POST()
        return handler.response_status, handler.response_headers.get("Content-Type"), handler.wfile.getvalue()

    def test_streamable_http_initialize_and_discover_mcp_app_tools(self):
        status, content_type, body = self.post_mcp({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2026-01-26", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}},
        })
        self.assertEqual(status, 200)
        self.assertTrue(content_type.startswith("application/json"))
        initialized = json.loads(body)["result"]
        self.assertEqual(initialized["protocolVersion"], "2026-01-26")
        self.assertIn("tools", initialized["capabilities"])
        self.assertIn("No inventes píxeles RGBA", initialized["instructions"])

        status, _, body = self.post_mcp({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertEqual(status, 200)
        tools = json.loads(body)["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, {"open_creator", "create_game", "get_game"})
        self.assertEqual(tools[0]["outputSchema"]["properties"]["ready"]["type"], "boolean")
        self.assertEqual(tools[1]["_meta"]["ui"]["resourceUri"], "ui://widget/dux-create.html")

    def test_mcp_notifications_are_accepted_without_json_body(self):
        status, _, body = self.post_mcp({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        self.assertEqual(status, 202)
        self.assertEqual(body, b"")

    def test_mcp_get_is_rejected_for_stateless_post_transport(self):
        handler = self.handler("/mcp")
        handler.do_GET()
        self.assertEqual(handler.response_status, 405)
        self.assertIn("POST", handler.response_headers["Allow"])

    def test_domain_challenge_is_served_only_when_configured(self):
        with patch.dict("os.environ", {"DUX_OPENAI_APPS_CHALLENGE": "verify-this-domain"}):
            handler = self.handler("/.well-known/openai-apps-challenge")
            handler.do_GET()
        self.assertEqual(handler.response_status, 200)
        self.assertEqual(handler.wfile.getvalue(), b"verify-this-domain")


class PluginPackageTests(unittest.TestCase):
    def test_plugin_package_requires_public_https_and_adds_mcp_path(self):
        self.assertEqual(normalize_mcp_url("https://play.example"), "https://play.example/mcp")
        self.assertEqual(normalize_mcp_url("https://play.example/mcp"), "https://play.example/mcp")
        with self.assertRaisesRegex(ValueError, "public HTTPS"):
            normalize_mcp_url("http://play.example")
        with self.assertRaisesRegex(ValueError, "only the origin"):
            normalize_mcp_url("https://play.example/api")

    def test_plugin_manifest_uses_deployed_policy_urls_and_complete_review_cases(self):
        plugin = build_plugin_manifest("https://play.example/mcp", "https://video.example/dux-demo")
        openai = plugin["extensions"]["com.openai"]
        interface = openai["interface"]
        self.assertEqual(interface["websiteURL"], "https://play.example")
        self.assertEqual(interface["privacyPolicyURL"], "https://play.example/privacy")
        self.assertEqual(interface["termsOfServiceURL"], "https://play.example/terms")
        self.assertEqual(interface["supportURL"], "https://github.com/datoga/dux-fps-poc/issues")
        self.assertEqual(interface["category"], "Entertainment")
        self.assertEqual(len(openai["review"]["test_cases"]["positive"]), 5)
        self.assertEqual(len(openai["review"]["test_cases"]["negative"]), 3)
        self.assertEqual(openai["review"]["demo_recording_url"], "https://video.example/dux-demo")

    def test_plugin_manifest_rejects_non_https_demo_recording(self):
        with self.assertRaisesRegex(ValueError, "public HTTPS"):
            build_plugin_manifest("https://play.example", "http://video.example/dux-demo")


if __name__ == "__main__":
    unittest.main()
