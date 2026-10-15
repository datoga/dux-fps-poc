import base64
from email.message import Message
import gzip
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
from zipfile import ZipFile
from tools.package_chatgpt_plugin import build_plugin_manifest, build_plugin_package, normalize_mcp_url, verify_live_deployment

import server
from engine.build_runtime import curated_game_assets, curated_pak, texture_override_header


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

    def test_runtime_source_assets_include_only_the_palette(self):
        with fixture_paks() as (pak0, _, _):
            assets = curated_game_assets(pak0)
        self.assertEqual(set(assets), {"gfx/palette.lmp"})

    def test_engine_override_targets_cover_start_and_campaign_textures(self):
        header = texture_override_header()
        targets = {server.TARGET_TEXTURE, *(name for names in server.CAMPAIGN_TEXTURES_BY_MAP.values() for name in names)}
        self.assertTrue(all(f'"{name}"' in header for name in targets))
        self.assertIn('"gfx/dux_texture.lmp"', header)
        self.assertIn("MIPLEVELS", header)

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

    def test_create_game_writes_a_small_texture_package_and_preview(self):
        with fixture_paks() as (pak0, pak1, _), tempfile.TemporaryDirectory() as temp, \
                patch.object(server, "PAK0", pak0), patch.object(server, "PAK1", pak1), \
                patch.object(server, "DATA", Path(temp)), \
                patch.object(server, "GAME_ASSETS", Path(temp) / "not-bundled"):
            width, height = server.TEXTURE_WIDTH, server.TEXTURE_HEIGHT
            rgba = bytes([20, 180, 220, 255]) * (width * height)
            game_id, expires = server.make_game(rgba, width, height)
            game_dir = Path(temp) / game_id
            manifest = json.loads((game_dir / "manifest.json").read_text())
            pack = server.pak_entries((game_dir / "pak2.pak").read_bytes())
            indexed = pack["gfx/dux_texture.lmp"]
            self.assertEqual(set(pack), {"gfx/dux_texture.lmp"})
            self.assertEqual(len(indexed), 128 * 128)
            self.assertLess((game_dir / "pak2.pak").stat().st_size, 17_000)
            self.assertEqual(manifest["gameId"], game_id)
            self.assertEqual(manifest["expiresAt"], expires)
            self.assertEqual((manifest["width"], manifest["height"]), (width, height))
            self.assertEqual((game_dir / "texture.rgba").read_bytes(), rgba)

    def test_create_game_works_from_palette_bundle_without_source_paks(self):
        with fixture_paks() as (pak0, _, _), tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            assets = root / "game-assets"
            for name, content in curated_game_assets(pak0).items():
                destination = assets / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            data = root / "games"
            missing_pak0, missing_pak1 = root / "missing-pak0", root / "missing-pak1"
            rgba = bytes([80, 120, 160, 255]) * (server.TEXTURE_WIDTH * server.TEXTURE_HEIGHT)
            with patch.object(server, "PAK0", missing_pak0), \
                    patch.object(server, "PAK1", missing_pak1), \
                    patch.object(server, "GAME_ASSETS", assets), \
                    patch.object(server, "DATA", data):
                self.assertTrue(server.game_assets_ready())
                game_id, _ = server.make_game(rgba, server.TEXTURE_WIDTH, server.TEXTURE_HEIGHT)
            package = server.pak_entries((data / game_id / "pak2.pak").read_bytes())
            self.assertEqual(set(package), {"gfx/dux_texture.lmp"})
            self.assertEqual(len(package["gfx/dux_texture.lmp"]), 128 * 128)

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

    def test_compressed_only_engine_runtime_serves_gzip_and_raw_clients(self):
        with tempfile.TemporaryDirectory() as temp:
            engine = Path(temp)
            for filename in ("index.html", "index.js", "index.wasm"):
                (engine / filename).write_bytes(b"runtime")
            runtime_data = b"shared Quake game assets"
            (engine / "index.data.gz").write_bytes(gzip.compress(runtime_data))
            with patch.object(server, "ENGINE", engine):
                self.assertTrue(server.engine_ready())
                compressed_client = self.handler("/engine/index.data")
                compressed_client.headers["Accept-Encoding"] = "gzip"
                compressed_client.do_GET()
                self.assertEqual(compressed_client.response_status, 200)
                self.assertEqual(compressed_client.response_headers["Content-Encoding"], "gzip")
                self.assertEqual(gzip.decompress(compressed_client.wfile.getvalue()), runtime_data)

                plain_client = self.handler("/engine/index.data")
                plain_client.do_GET()
                self.assertEqual(plain_client.response_status, 200)
                self.assertEqual(plain_client.wfile.getvalue(), runtime_data)

    def test_expired_game_api_returns_gone(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server, "DATA", Path(temp)):
            game_dir = Path(temp) / "a1b2c3d4e5f6"
            game_dir.mkdir()
            (game_dir / "manifest.json").write_text(json.dumps({"expiresAt": 100}))
            handler = self.handler("/v1/games/a1b2c3d4e5f6")
            with patch.object(server.time, "time", return_value=100):
                handler.do_GET()
        self.assertEqual(handler.response_status, 410)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"error": "game_expired"})

    def test_cleanup_deletes_expired_game_only(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(server, "DATA", Path(temp)):
            expired = Path(temp) / "expired"
            active = Path(temp) / "active"
            staging = Path(temp) / ".unfinished.tmp"
            for path, expires in ((expired, 199), (active, 201)):
                path.mkdir()
                (path / "manifest.json").write_text(json.dumps({"expiresAt": expires}))
                (path / "pak2.pak").write_bytes(b"test")
            staging.mkdir()
            self.assertEqual(server.cleanup_expired_games_once(now=200), 1)
            self.assertFalse(expired.exists())
            self.assertTrue(active.exists())
            self.assertTrue(staging.exists())

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

    def test_live_release_check_verifies_deployment_before_packaging(self):
        policy_body = [b"Production policy and operator contact details."]

        class FakeResponse:
            def __init__(self, body, status=200):
                self.body, self.status = body, status
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, limit=-1): return self.body if limit < 0 else self.body[:limit]

        def fake_urlopen(request, timeout):
            url = request.full_url if hasattr(request, "full_url") else request
            if url.endswith("/v1/health"):
                body = json.dumps({"ok": True, "assetsReady": True, "engineReady": True}).encode()
            elif url.endswith("/mcp"):
                method = json.loads(request.data)["method"]
                result = (
                    {"serverInfo": {"name": "dux-game-mcp"}}
                    if method == "initialize" else
                    {"tools": [{"name": name} for name in ("open_creator", "create_game", "get_game")]}
                    if method == "tools/list" else
                    {"contents": [{"uri": "ui://widget/dux-create.html", "text": "window.openai.selectFiles Crear partida"}]}
                )
                body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}).encode()
            elif url.endswith("/privacy") or url.endswith("/terms"):
                body = policy_body[0]
            elif url.endswith("/dux-demo.mp4"):
                body = b"x"
                return FakeResponse(body, 206)
            else:
                body = b"ok"
            return FakeResponse(body)

        with patch("tools.package_chatgpt_plugin.urlopen", side_effect=fake_urlopen):
            verify_live_deployment("https://dux.com", "https://media.com/dux-demo.mp4")
            policy_body[0] = b"Draft terms: operator contact must be added before public launch."
            with self.assertRaisesRegex(ValueError, "draft operator or policy placeholders"):
                verify_live_deployment("https://dux.com", "https://media.com/dux-demo.mp4")

    def test_plugin_zip_is_not_written_when_live_verification_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dux.zip"
            with patch("tools.package_chatgpt_plugin.verify_live_deployment", side_effect=ValueError("not ready")):
                with self.assertRaisesRegex(ValueError, "not ready"):
                    build_plugin_package(output, "https://dux.com", "https://media.com/dux-demo.mp4", verify_live=True)
            self.assertFalse(output.exists())

    def test_live_release_check_rejects_example_domains_and_missing_demo(self):
        with self.assertRaisesRegex(ValueError, "reserved example"):
            verify_live_deployment("https://play.example.com", "https://media.com/dux-demo.mp4")
        with self.assertRaisesRegex(ValueError, "requires a reviewer-accessible demo"):
            verify_live_deployment("https://dux.com", None)

    def test_plugin_zip_bundles_skill_mcp_and_public_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "dux.zip"
            mcp_url = build_plugin_package(output, "https://play.example", "https://video.example/dux-demo")
            with ZipFile(output) as archive:
                self.assertEqual(mcp_url, "https://play.example/mcp")
                self.assertEqual(set(archive.namelist()), {
                    "plugin.json", "mcp.json", "assets/dux-logo.png", "skills/dux-create/SKILL.md",
                })
                plugin = json.loads(archive.read("plugin.json"))
                mcp = json.loads(archive.read("mcp.json"))
                skill = archive.read("skills/dux-create/SKILL.md").decode()
            openai = plugin["extensions"]["com.openai"]
            interface = openai["interface"]
            self.assertEqual(plugin["description"], "Turn an image into a shareable LibreQuake game with a custom pixel-art texture.")
            self.assertLessEqual(len(interface["displayName"]), 30)
            self.assertLessEqual(len(interface["shortDescription"]), 30)
            self.assertTrue(interface["longDescription"].startswith("Choose an image"))
            self.assertLessEqual(len(interface["defaultPrompt"]), 3)
            self.assertTrue(all(len(prompt) <= 128 for prompt in interface["defaultPrompt"]))
            self.assertEqual(openai["publication"]["translations"]["es-ES"]["subtitle"], "Convierte una foto en un juego")
            self.assertEqual(mcp["mcpServers"]["dux"]["url"], mcp_url)
            self.assertIn("name: dux-create", skill)
            self.assertEqual(len(openai["review"]["test_cases"]["positive"]), 5)
            self.assertEqual(len(openai["review"]["test_cases"]["negative"]), 3)
            self.assertTrue(all(case.get("expected_behavior") for case in openai["review"]["test_cases"]["negative"]))


if __name__ == "__main__":
    unittest.main()
