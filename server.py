"""Local Dux texture-to-Quake-PAK proof of concept; Python standard library only."""
from __future__ import annotations

import base64
import gzip
import json
import mimetypes
import os
import re
import struct
import time
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("DUX_DATA_DIR", str(ROOT / "data" / "games"))).expanduser()
PAK0 = ROOT / "librequake-assets" / "pak0.pak"
PAK1 = ROOT / "librequake-assets" / "pak1.pak"
ENGINE = ROOT / "qwasm-runtime"
GAME_ASSETS = ENGINE / "game-assets"
# The dev test map has no player spawn and crashes in the hosted Qwasm build.
# LibreQuake's compact start map has a valid spawn point and loads reliably in browser.
TARGET_MAP = "maps/start.bsp"
TARGET_TEXTURE = "med_ebrick9"
# Add the uploaded image to visible, existing surfaces across every campaign map.
# Include nearby walls as well as screens/signs so the image is easy to find.
# Animated monitor frames are paired.
CAMPAIGN_TEXTURES_BY_MAP = {
    "maps/lq_e0m1.bsp": ("t_wall2a", "t_wall3a"),
    "maps/lq_e0m2.bsp": ("med_csl_brk10b",),
    "maps/lq_e0m3.bsp": ("med_dbrick6b", "med_csl_brk8_1c"),
    "maps/lq_e0m4.bsp": ("med_csl_brk14b",),
    "maps/lq_e0m5.bsp": ("may_brk1_1",),
    "maps/lq_e0m6.bsp": ("grk_mural1", "grk_brk17", "grk_wall3"),
    "maps/lq_e0m7.bsp": ("met_blu_flat",),
    "maps/lq_e0m8.bsp": ("may_wall1_2", "may_blok1_1"),
    "maps/lq_e1m1.bsp": (
        "t_blok05", "t_blok02a", "uwall1_2", "med_dbrick6b", "t_wall1ba",
        "+0_tscrn0", "+a_tscrn0", "+0_tscrn1", "+a_tscrn1", "+a_tscrn2",
    ),
    "maps/lq_e1m2.bsp": ("met_brn_signs", "med_csl_brk10b", "med_tanwall7"),
    "maps/lq_e1m3.bsp": ("met_brn_signs", "med_ebrick9"),
    "maps/lq_e1m4.bsp": ("med_tanwall4", "med_tanwall4_f", "med_csl_brk12"),
    "maps/lq_e1m5.bsp": ("med_tanwall8", "med_csl_brk18_t"),
    "maps/lq_e1m6.bsp": ("med_ebrick9",),
    "maps/lq_e1m7.bsp": ("med_tanwall7", "med_csl_brk9_f", "med_brk9_ceil1a"),
    "maps/lq_e1m8.bsp": ("med_csl_brk10b", "med_csl_brk18_f"),
}
MAX_BODY = 512 * 1024
TEXTURE_WIDTH = 128
TEXTURE_HEIGHT = 128
MAX_PIXELS = TEXTURE_WIDTH * TEXTURE_HEIGHT
FREE_TTL_SECONDS = 7 * 24 * 60 * 60
ENGINE_FILES = ("index.html", "index.js", "index.wasm")


def engine_ready() -> bool:
    return (all((ENGINE / name).is_file() for name in ENGINE_FILES)
            and any((ENGINE / name).is_file() for name in ("index.data", "index.data.gz")))


def game_assets_ready() -> bool:
    return (GAME_ASSETS / "gfx/palette.lmp").is_file() or (PAK0.is_file() and PAK1.is_file())


def load_game_asset(name: str) -> bytes:
    bundled = GAME_ASSETS / name
    if bundled.is_file():
        return bundled.read_bytes()
    return extract_entry(PAK0, name)


def pak_entries(data: bytes) -> dict[str, bytes]:
    if len(data) < 12 or data[:4] != b"PACK":
        raise ValueError("Invalid PAK header")
    directory_offset, directory_length = struct.unpack_from("<II", data, 4)
    if directory_offset + directory_length > len(data) or directory_length % 64:
        raise ValueError("Invalid PAK directory")
    result: dict[str, bytes] = {}
    for offset in range(directory_offset, directory_offset + directory_length, 64):
        raw_name = data[offset:offset + 56].split(b"\0", 1)[0]
        name = raw_name.decode("ascii")
        file_offset, file_length = struct.unpack_from("<II", data, offset + 56)
        if file_offset + file_length > len(data):
            raise ValueError(f"Invalid PAK entry: {name}")
        result[name] = data[file_offset:file_offset + file_length]
    return result


def make_pak(entries: dict[str, bytes]) -> bytes:
    output = bytearray(b"\0" * 12)
    directory = bytearray()
    for name, content in sorted(entries.items()):
        encoded = name.encode("ascii")
        if len(encoded) > 55:
            raise ValueError("PAK path too long")
        file_offset = len(output)
        output.extend(content)
        directory.extend(encoded.ljust(56, b"\0"))
        directory.extend(struct.pack("<II", file_offset, len(content)))
    directory_offset = len(output)
    output.extend(directory)
    output[:12] = b"PACK" + struct.pack("<II", directory_offset, len(directory))
    return bytes(output)


def extract_entry(pak_path: Path, name: str) -> bytes:
    with pak_path.open("rb") as pak:
        header = pak.read(12)
        if len(header) != 12 or header[:4] != b"PACK":
            raise ValueError("Invalid PAK header")
        directory_offset, directory_length = struct.unpack_from("<II", header, 4)
        if directory_length % 64 or directory_offset + directory_length > pak_path.stat().st_size:
            raise ValueError("Invalid PAK directory")
        pak.seek(directory_offset)
        directory = pak.read(directory_length)
        for offset in range(0, directory_length, 64):
            raw_name = directory[offset:offset + 56].split(b"\0", 1)[0]
            if raw_name.decode("ascii") != name:
                continue
            file_offset, file_length = struct.unpack_from("<II", directory, offset + 56)
            if file_offset + file_length > pak_path.stat().st_size:
                raise ValueError(f"Invalid PAK entry: {name}")
            pak.seek(file_offset)
            return pak.read(file_length)
    raise KeyError(name)


def get_lump(bsp: bytes, lump_index: int) -> tuple[int, int]:
    if len(bsp) < 124:
        raise ValueError("BSP is truncated")
    version = struct.unpack_from("<i", bsp)[0]
    if version != 29:
        raise ValueError(f"Expected Quake BSP v29, got {version}")
    offset, length = struct.unpack_from("<II", bsp, 4 + lump_index * 8)
    if offset + length > len(bsp):
        raise ValueError("BSP lump outside file")
    return offset, length


def texture_info(bsp: bytes, texture_name: str) -> tuple[int, int, int, int, list[int]]:
    tex_offset, tex_length = get_lump(bsp, 2)
    tex_lump = bsp[tex_offset:tex_offset + tex_length]
    if len(tex_lump) < 4:
        raise ValueError("BSP has no texture lump")
    count, = struct.unpack_from("<i", tex_lump)
    if count < 1 or count > 8192 or len(tex_lump) < 4 + count * 4:
        raise ValueError("Invalid BSP texture table")
    for index in range(count):
        rel_offset, = struct.unpack_from("<i", tex_lump, 4 + index * 4)
        if rel_offset < 0 or rel_offset + 40 > len(tex_lump):
            continue
        name = tex_lump[rel_offset:rel_offset + 16].split(b"\0", 1)[0].decode("ascii", "replace")
        if name != texture_name:
            continue
        width, height = struct.unpack_from("<II", tex_lump, rel_offset + 16)
        mip_offsets = list(struct.unpack_from("<4I", tex_lump, rel_offset + 24))
        return tex_offset, tex_length, rel_offset, width, [height, *mip_offsets]
    raise ValueError(f"Texture {texture_name!r} not found in BSP")


def target_size(bsp: bytes, texture_name: str) -> tuple[int, int]:
    _, _, _, width, rest = texture_info(bsp, texture_name)
    return width, rest[0]


def nearest_palette_indices(rgba: bytes, palette: bytes, width: int, height: int) -> bytes:
    if len(palette) != 768:
        raise ValueError("Quake palette must contain 256 RGB colors")
    if len(rgba) != width * height * 4:
        raise ValueError("RGBA payload length does not match dimensions")
    colors = [tuple(palette[i:i + 3]) for i in range(0, 768, 3)]
    lookup: dict[tuple[int, int, int], int] = {}
    out = bytearray(width * height)
    for pixel in range(width * height):
        at = pixel * 4
        rgb = tuple(rgba[at:at + 3])
        if rgb not in lookup:
            lookup[rgb] = min(
                range(256),
                key=lambda i: sum((rgb[channel] - colors[i][channel]) ** 2 for channel in range(3)),
            )
        out[pixel] = lookup[rgb]
    return bytes(out)


def resize_rgba_nearest(rgba: bytes, source_width: int, source_height: int,
                        target_width: int, target_height: int) -> bytes:
    if len(rgba) != source_width * source_height * 4:
        raise ValueError("RGBA payload length does not match dimensions")
    if (source_width, source_height) == (target_width, target_height):
        return rgba
    resized = bytearray(target_width * target_height * 4)
    for y in range(target_height):
        source_y = min(source_height - 1, y * source_height // target_height)
        for x in range(target_width):
            source_x = min(source_width - 1, x * source_width // target_width)
            source = (source_y * source_width + source_x) * 4
            target = (y * target_width + x) * 4
            resized[target:target + 4] = rgba[source:source + 4]
    return bytes(resized)


def build_miptex(bsp: bytes, texture_name: str, rgba: bytes, width: int, height: int, palette: bytes) -> bytes:
    tex_offset, tex_length, rel_offset, expected_width, rest = texture_info(bsp, texture_name)
    expected_height, *old_mips = rest
    if len(rgba) != width * height * 4:
        raise ValueError("RGBA payload length does not match dimensions")
    target_rgba = resize_rgba_nearest(rgba, width, height, expected_width, expected_height)
    base = nearest_palette_indices(target_rgba, palette, expected_width, expected_height)
    mip_levels = [base]
    w, h = expected_width, expected_height
    while w > 1 or h > 1:
        nw, nh = max(1, w // 2), max(1, h // 2)
        prev = mip_levels[-1]
        level = bytearray(nw * nh)
        for y in range(nh):
            for x in range(nw):
                # Nearest-neighbor downsampling keeps the classic pixel-art look.
                level[y * nw + x] = prev[min(h - 1, y * 2) * w + min(w - 1, x * 2)]
        mip_levels.append(bytes(level))
        w, h = nw, nh
    # BSP miptex offsets are relative to the beginning of the miptex record.
    header_size = 40
    offsets = []
    cursor = header_size
    for level in mip_levels[:4]:
        offsets.append(cursor)
        cursor += len(level)
    tex_name = texture_name.encode("ascii")[:15].ljust(16, b"\0")
    record = bytearray(tex_name + struct.pack("<II4I", expected_width, expected_height, *offsets))
    for level in mip_levels[:4]:
        record.extend(level)

    # Replace only this texture record in the BSP texture lump, rebuilding its offset table.
    tex_lump = bytearray(bsp[tex_offset:tex_offset + tex_length])
    old_offsets = []
    count, = struct.unpack_from("<i", tex_lump)
    for i in range(count):
        value, = struct.unpack_from("<i", tex_lump, 4 + i * 4)
        old_offsets.append(value)
    records = []
    for i, offset in enumerate(old_offsets):
        if offset < 0:
            records.append(None)
            continue
        end = len(tex_lump)
        for other in old_offsets:
            if other > offset:
                end = min(end, other)
        records.append(bytes(record) if offset == rel_offset else bytes(tex_lump[offset:end]))
    rebuilt = bytearray(struct.pack("<i", count) + b"\0" * (4 * count))
    new_offsets = []
    for item in records:
        if item is None:
            new_offsets.append(-1)
        else:
            new_offsets.append(len(rebuilt))
            rebuilt.extend(item)
    for i, offset in enumerate(new_offsets):
        struct.pack_into("<i", rebuilt, 4 + i * 4, offset)

    # Rebuild the full BSP because the texture lump size can change.
    new_bsp = bytearray(bsp[:124])
    lumps = [get_lump(bsp, i) for i in range(15)]
    new_lump_headers = []
    for i, (offset, length) in enumerate(lumps):
        payload = bytes(rebuilt) if i == 2 else bsp[offset:offset + length]
        new_offset = len(new_bsp)
        new_bsp.extend(payload)
        new_lump_headers.append((new_offset, len(payload)))
    for i, (offset, length) in enumerate(new_lump_headers):
        struct.pack_into("<II", new_bsp, 4 + i * 8, offset, length)
    return bytes(new_bsp)


def make_game(texture_rgba: bytes, width: int, height: int) -> tuple[str, int]:
    if (width, height) != (TEXTURE_WIDTH, TEXTURE_HEIGHT):
        raise ValueError(f"Texture must be exactly {TEXTURE_WIDTH}x{TEXTURE_HEIGHT}")
    if not game_assets_ready():
        raise RuntimeError("LibreQuake game assets are missing")
    palette = load_game_asset("gfx/palette.lmp")
    indexed_texture = nearest_palette_indices(texture_rgba, palette, width, height)
    game_id = uuid.uuid4().hex[:12]
    created_at = int(time.time())
    expires = created_at + FREE_TTL_SECONDS
    pak_bytes = make_pak({"gfx/dux_texture.lmp": indexed_texture})
    DATA.mkdir(parents=True, exist_ok=True)
    game_dir = DATA / game_id
    staging_dir = DATA / f".{game_id}.tmp"
    staging_dir.mkdir(exist_ok=False)
    manifest = {
        "gameId": game_id,
        "target": TARGET_TEXTURE,
        "map": TARGET_MAP,
        "width": width,
        "height": height,
        "createdAt": created_at,
        "expiresAt": expires,
        "slug": None,
    }
    try:
        (staging_dir / "pak2.pak").write_bytes(pak_bytes)
        (staging_dir / "texture.rgba").write_bytes(texture_rgba)
        (staging_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        staging_dir.rename(game_dir)
    except Exception:
        for child in staging_dir.iterdir():
            child.unlink()
        staging_dir.rmdir()
        raise
    return game_id, expires


class Handler(BaseHTTPRequestHandler):
    server_version = "Dux/0.2"

    def _json(self, status: int, value: dict) -> None:
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", "0" if status in (202, 204) else str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Mcp-Session-Id, Mcp-Protocol-Version")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        if status not in (202, 204):
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self._json(204, {})

    def do_POST(self) -> None:
        if self.path == "/mcp":
            self._handle_mcp()
            return
        if self.path != "/v1/games":
            self._json(404, {"error": "not_found"})
            return
        try:
            if self.headers.get_content_type() != "application/json":
                self._json(415, {"error": "content_type_must_be_application_json"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                self._json(413, {"error": "invalid_request_size", "maxBytes": MAX_BODY})
                return
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
            width, height = payload.get("width"), payload.get("height")
            if not isinstance(width, int) or not isinstance(height, int) or width * height > MAX_PIXELS:
                raise ValueError("Invalid texture dimensions")
            pixels = base64.b64decode(payload.get("rgba", ""), validate=True)
            if len(pixels) != width * height * 4:
                raise ValueError("RGBA pixel data has an invalid length")
            game_id, expires = make_game(pixels, width, height)
            self._json(201, {
                "gameId": game_id,
                "shareUrl": f"/play/{game_id}",
                "expiresAt": expires,
                "pakUrl": f"/v1/games/{game_id}/pak",
                "playable": engine_ready(),
            })
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid_json"})
        except (ValueError, TypeError, KeyError) as error:
            self._json(400, {"error": "invalid_texture", "message": str(error)})
        except RuntimeError as error:
            self.log_error("Dux is not ready to create a game: %s", error)
            self._json(503, {"error": "game_creation_unavailable", "message": "El juego todavía no está preparado en este servidor."})
        except Exception as error:
            self.log_error("Game creation failed: %s", error)
            self._json(500, {"error": "creation_failed", "message": "No se pudo crear la partida. Inténtalo de nuevo."})

    def _handle_mcp(self) -> None:
        try:
            if self.headers.get_content_type() != "application/json":
                self._json(415, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Content-Type must be application/json"}})
                return
            accepted = self.headers.get("Accept", "application/json, text/event-stream")
            if not any(media_type in accepted for media_type in ("application/json", "text/event-stream", "*/*")):
                self._json(406, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Accept must include application/json or text/event-stream"}})
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                self._json(413, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request size"}})
                return
            request = json.loads(self.rfile.read(length))
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
                self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid JSON-RPC request"}})
                return
            method = request.get("method")
            request_id = request.get("id")
            params = request.get("params") or {}
            if method.startswith("notifications/"):
                self._json(202, {})
                return
            result = self._mcp_dispatch(method, params)
            self._json(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
        except json.JSONDecodeError:
            self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
        except KeyError as error:
            request_id = locals().get("request", {}).get("id")
            method = locals().get("request", {}).get("method", "")
            known_methods = {"initialize", "ping", "tools/list", "tools/call", "resources/list", "resources/read"}
            code = -32601 if method not in known_methods else -32602
            self._json(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": str(error)}})
        except Exception as error:
            self.log_error("MCP request failed: %s", error)
            request_id = locals().get("request", {}).get("id")
            self._json(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "Internal server error"}})

    def _mcp_dispatch(self, method: str, params: dict) -> dict:
        if method == "initialize":
            requested = params.get("protocolVersion", "2025-06-18")
            supported = {"2024-11-05", "2025-03-26", "2025-06-18", "2026-01-26"}
            return {
                "protocolVersion": requested if requested in supported else "2025-06-18",
                "capabilities": {"tools": {}, "resources": {"listChanged": False}},
                "serverInfo": {"name": "dux-game-mcp", "version": "0.1.0"},
                "instructions": (
                    "Cuando el usuario quiera crear una partida, llama a open_creator para mostrar el creador. "
                    "El usuario debe elegir una imagen, revisar su textura y pulsar Crear partida en el widget. "
                    "No inventes píxeles RGBA ni llames directamente a create_game: el widget prepara y envía "
                    "solo la textura final después de la acción del usuario. Comparte el enlace devuelto; "
                    "las partidas gratuitas caducan a los siete días."
                ),
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            resource_meta = {"ui": {"resourceUri": "ui://widget/dux-create.html"}, "openai/outputTemplate": "ui://widget/dux-create.html"}
            return {"tools": [
                {
                    "name": "open_creator",
                    "title": "Crear partida Dux",
                    "description": "Abre el creador de Dux para seleccionar la textura final y publicar una partida retro compartible.",
                    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                    "outputSchema": {"type": "object", "properties": {"ready": {"type": "boolean"}}, "required": ["ready"]},
                    "_meta": resource_meta,
                    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                },
                {
                    "name": "create_game",
                    "title": "Publicar partida Dux",
                    "description": "Crea una partida gratuita de LibreQuake con la textura final ya preparada a 128x128. Solo recibe píxeles de la textura, nunca la foto de origen.",
                    "inputSchema": {"type": "object", "properties": {
                        "width": {"type": "integer", "const": TEXTURE_WIDTH},
                        "height": {"type": "integer", "const": TEXTURE_HEIGHT},
                        "rgba": {"type": "string", "maxLength": 120000, "description": "Píxeles RGBA en Base64 de la textura final reducida a 128x128."},
                    }, "required": ["width", "height", "rgba"], "additionalProperties": False},
                    "outputSchema": {"type": "object", "properties": {
                        "gameId": {"type": "string"}, "shareUrl": {"type": "string"}, "expiresAt": {"type": "integer"}, "target": {"type": "string"}, "playable": {"type": "boolean"}
                    }, "required": ["gameId", "shareUrl", "expiresAt", "target", "playable"]},
                    "_meta": {**resource_meta, "openai/toolInvocation/invoking": "Creando la partida…", "openai/toolInvocation/invoked": "Partida publicada"},
                    "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
                },
                {
                    "name": "get_game",
                    "title": "Consultar partida Dux",
                    "description": "Consulta si una partida Dux sigue disponible y devuelve su enlace compartible.",
                    "inputSchema": {"type": "object", "properties": {"gameId": {"type": "string", "pattern": "^[a-f0-9]{12}$"}}, "required": ["gameId"], "additionalProperties": False},
                    "outputSchema": {"type": "object", "properties": {"gameId": {"type": "string"}, "shareUrl": {"type": "string"}, "expiresAt": {"type": "integer"}, "target": {"type": "string"}}},
                    "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
                },
            ]}
        if method == "resources/list":
            return {"resources": [{"uri": "ui://widget/dux-create.html", "name": "Dux game creator", "mimeType": "text/html;profile=mcp-app"}]}
        if method == "resources/read":
            if params.get("uri") != "ui://widget/dux-create.html":
                raise KeyError("Unknown resource URI")
            html = (ROOT / "index.html").read_text(encoding="utf-8")
            api_base = os.environ.get("DUX_PUBLIC_URL", "").rstrip("/")
            if api_base:
                html = html.replace("<head>", f'<head><script>window.DUX_API_BASE={json.dumps(api_base)};</script>', 1)
            origin = api_base or f"http://127.0.0.1:{getattr(self.server, 'server_port', 8000)}"
            return {"contents": [{
                "uri": "ui://widget/dux-create.html",
                "mimeType": "text/html;profile=mcp-app",
                "text": html,
                "_meta": {
                    "ui": {"domain": origin, "prefersBorder": True, "csp": {"connectDomains": [origin, "https://files.oaiusercontent.com"], "resourceDomains": [origin, "https://files.oaiusercontent.com"]}},
                    "openai/widgetDomain": origin,
                    "openai/widgetCSP": {"connect_domains": [origin, "https://files.oaiusercontent.com"], "resource_domains": [origin, "https://files.oaiusercontent.com"]},
                    "openai/widgetDescription": "Selecciona una textura final, previsualízala y publícala en una partida compartible de Dux.",
                    "openai/widgetPrefersBorder": True,
                },
            }]}
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "open_creator":
                return {"content": [{"type": "text", "text": "Selecciona o sube la textura final desde el creador Dux."}], "structuredContent": {"ready": True}}
            if name == "create_game":
                if not isinstance(args, dict):
                    return {"isError": True, "content": [{"type": "text", "text": "Los argumentos no son válidos."}]}
                width, height = args.get("width"), args.get("height")
                if (width, height) != (TEXTURE_WIDTH, TEXTURE_HEIGHT):
                    return {"isError": True, "content": [{"type": "text", "text": f"La textura debe medir {TEXTURE_WIDTH}x{TEXTURE_HEIGHT}."}]}
                try:
                    rgba = base64.b64decode(args.get("rgba", ""), validate=True)
                except (ValueError, TypeError):
                    return {"isError": True, "content": [{"type": "text", "text": "La textura no contiene píxeles Base64 válidos."}]}
                if len(rgba) != width * height * 4:
                    return {"isError": True, "content": [{"type": "text", "text": "Los píxeles RGBA no tienen el tamaño esperado."}]}
                game_id, expires = make_game(rgba, width, height)
                base = os.environ.get("DUX_PUBLIC_URL", f"http://127.0.0.1:{getattr(self.server, 'server_port', 8000)}").rstrip("/")
                share_url = f"{base}/play/{game_id}"
                structured = {"gameId": game_id, "shareUrl": share_url, "expiresAt": expires, "target": TARGET_TEXTURE, "playable": engine_ready()}
                return {"content": [{"type": "text", "text": f"Partida lista: {share_url}"}], "structuredContent": structured}
            if name == "get_game":
                game_id = args.get("gameId", "")
                if not re.fullmatch(r"[a-f0-9]{12}", game_id):
                    return {"isError": True, "content": [{"type": "text", "text": "ID de partida inválido."}]}
                manifest_path = DATA / game_id / "manifest.json"
                if not manifest_path.is_file():
                    return {"isError": True, "content": [{"type": "text", "text": "No se encontró esta partida."}]}
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if int(manifest["expiresAt"]) <= int(time.time()):
                    return {"isError": True, "content": [{"type": "text", "text": "Esta partida ha caducado."}]}
                base = os.environ.get("DUX_PUBLIC_URL", f"http://127.0.0.1:{getattr(self.server, 'server_port', 8000)}").rstrip("/")
                structured = {"gameId": game_id, "shareUrl": f"{base}/play/{game_id}", "expiresAt": manifest["expiresAt"], "target": manifest["target"]}
                return {"content": [{"type": "text", "text": structured["shareUrl"]}], "structuredContent": structured}
            return {"isError": True, "content": [{"type": "text", "text": f"Unknown tool: {name}"}]}
        raise KeyError(f"Unsupported MCP method: {method}")

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/mcp":
            self.send_response(405)
            self.send_header("Allow", "POST, OPTIONS")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        request_path = unquote(self.path.split("?", 1)[0])
        if request_path == "/.well-known/openai-apps-challenge":
            challenge = os.environ.get("DUX_OPENAI_APPS_CHALLENGE", "")
            if not challenge:
                self._json(404, {"error": "not_found"})
                return
            content = challenge.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)
            return
        if request_path == "/privacy":
            self._file(ROOT / "PRIVACY.md", "text/markdown; charset=utf-8")
            return
        if request_path == "/terms":
            self._file(ROOT / "TERMS.md", "text/markdown; charset=utf-8")
            return
        if request_path == "/v1/health":
            self._json(200, {
                "ok": True,
                "assetsReady": game_assets_ready(),
                "engineReady": engine_ready(),
            })
            return
        path = request_path
        match = re.fullmatch(r"/v1/games/([a-f0-9]{12})(?:/(pak|texture))?", path)
        if match:
            game_id, resource = match.groups()
            game_dir = DATA / game_id
            manifest_path = game_dir / "manifest.json"
            if not manifest_path.is_file():
                self._json(404, {"error": "game_not_found"})
                return
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if int(manifest["expiresAt"]) <= int(time.time()):
                self._json(410, {"error": "game_expired"})
                return
            if resource is None:
                self._json(200, manifest)
                return
            file = game_dir / ("pak2.pak" if resource == "pak" else "texture.rgba")
            content_type = "application/octet-stream"
            data = file.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        if path.startswith("/play/"):
            game_id = path.rsplit("/", 1)[-1]
            if not re.fullmatch(r"[a-f0-9]{12}", game_id):
                self._html(404, "No se encontró esta partida")
                return
            if not (DATA / game_id / "manifest.json").is_file():
                self._html(404, "Esta partida ya no está disponible")
                return
            self._file(ROOT / "play.html", "text/html; charset=utf-8")
            return
        if path.startswith("/engine/"):
            engine_file = (ENGINE / path.removeprefix("/engine/")).resolve()
            if not engine_file.is_relative_to(ENGINE.resolve()):
                self._json(404, {"error": "not_found"})
                return
            compressed = ENGINE / "index.data.gz"
            if engine_file.name == "index.data" and "gzip" in self.headers.get("Accept-Encoding", "") and compressed.is_file():
                self._file(compressed, "application/octet-stream", content_encoding="gzip")
            elif engine_file.name == "index.data" and not engine_file.is_file() and compressed.is_file():
                self._gzip_file(compressed, "application/octet-stream")
            else:
                self._file(engine_file)
            return
        if path == "/" or path == "/index.html":
            self._file(ROOT / "index.html", "text/html; charset=utf-8")
            return
        self._json(404, {"error": "not_found"})

    def _html(self, status: int, text: str) -> None:
        content = f"<!doctype html><meta charset=utf-8><title>Dux</title><h1>{text}</h1>".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _file(self, path: Path, content_type: str | None = None, *, content_encoding: str | None = None) -> None:
        if not path.is_file():
            self._json(404, {"error": "file_not_found"})
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if content_encoding:
            self.send_header("Content-Encoding", content_encoding)
            self.send_header("Vary", "Accept-Encoding")
        if path.parent == ENGINE.resolve() and path.name != "index.html":
            self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def _gzip_file(self, path: Path, content_type: str) -> None:
        """Stream a compressed runtime asset decoded for clients without gzip support."""
        if not path.is_file() or path.stat().st_size < 4:
            self._json(404, {"error": "file_not_found"})
            return
        with path.open("rb") as source:
            source.seek(-4, os.SEEK_END)
            content_length, = struct.unpack("<I", source.read(4))
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        with gzip.open(path, "rb") as source:
            while chunk := source.read(1024 * 1024):
                self.wfile.write(chunk)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def cleanup_expired_games_once(now: int | None = None) -> int:
    """Remove expired game files and return how many game directories were deleted."""
    current_time = int(time.time()) if now is None else now
    removed = 0
    if not DATA.is_dir():
        return removed
    for game_dir in DATA.iterdir():
        if not game_dir.is_dir() or game_dir.name.startswith("."):
            continue
        manifest_path = game_dir / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if int(manifest.get("expiresAt", 0)) > current_time:
                continue
            children = list(game_dir.iterdir())
            if any(not child.is_file() and not child.is_symlink() for child in children):
                continue
            for child in children:
                child.unlink()
            game_dir.rmdir()
            removed += 1
        except (OSError, ValueError, TypeError):
            continue
    return removed


def cleanup_expired_games() -> None:
    """Delete expired free games periodically; API checks deny access immediately."""
    while True:
        cleanup_expired_games_once()
        time.sleep(60)


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("DUX_PORT", "8000"))
    threading.Thread(target=cleanup_expired_games, daemon=True).start()
    host = os.environ.get("DUX_HOST", "127.0.0.1")
    try:
        server = ThreadingHTTPServer((host, port), Handler)
    except OSError:
        if "DUX_PORT" in os.environ:
            raise
        port += 1
        server = ThreadingHTTPServer((host, port), Handler)
    print(f"Dux POC: http://127.0.0.1:{port}")
    server.serve_forever()
