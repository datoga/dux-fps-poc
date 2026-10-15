# Dux

Dux turns an image into a shareable LibreQuake game. The image is cropped and resized in the browser to a **128 × 128 RGBA texture**; Dux receives that final texture, converts it to the game's palette, and builds one small `pak2.pak` for the game. The original image is not sent to Dux. One shared Qwasm runtime and the common game assets serve all games; each share link adds its own texture package.

The repository contains a local website, a JSON API, a stateless Streamable HTTP MCP server with an Apps SDK widget, and a Docker deployment for a host with persistent storage. The ChatGPT plugin metadata and package builder are in `chatgpt-plugin/` and `tools/`.

## Run the website locally

Requirements: Python 3.10+, Emscripten, and the LibreQuake asset files. No Python or JavaScript dependencies are required.

1. Download a LibreQuake release from the [official project](https://github.com/lavenderdotpet/LibreQuake/releases) and put `pak0.pak` and `pak1.pak` in `librequake-assets/`. These files are intentionally excluded from Git.
2. Install and activate the [Emscripten SDK](https://emscripten.org/docs/getting_started/downloads.html), then build the common browser runtime:

   ```sh
   source /path/to/emsdk/emsdk_env.sh
   python3 engine/build_runtime.py
   ```

   The build fetches the Qwasm source at a pinned revision and creates the ignored `qwasm-runtime/` files.
3. Start the local site and API:

   ```sh
   python3 server.py
   ```

   Open `http://127.0.0.1:8000`. Set `DUX_PORT` to choose another port. Game files are saved under `data/games/` by default; set `DUX_DATA_DIR` to move that directory.

Choose or upload a PNG, JPEG, or WebP up to 10 MB. Dux crops the center square and shows the texture preview before publishing. A free game link works for seven days. The share page runs the game in the browser with keyboard/mouse controls and fullscreen support.

## Test the ChatGPT app

ChatGPT needs to reach an HTTPS endpoint; it cannot connect to `localhost`. For local testing, expose the server through a trusted HTTPS tunnel and use that origin when starting Dux:

```sh
DUX_PUBLIC_URL=https://your-tunnel.example DUX_PORT=8000 python3 server.py
```

Connect `https://your-tunnel.example/mcp` as a custom MCP server in ChatGPT. The `open_creator` tool opens the widget. It checks for ChatGPT's file-library picker and temporary-download APIs, and tries `uploadFile()` for a device-selected image when available; the ordinary browser file upload remains the fallback. The widget prepares the final texture locally and calls `create_game` through the MCP Apps bridge. Dux does not call an image-generation API or consume ChatGPT plan tokens.

To prepare a public plugin package after deploying Dux to a **stable public HTTPS domain**:

```sh
DUX_PUBLIC_URL=https://play.example.com \
DUX_DEMO_RECORDING_URL=https://example.com/dux-demo.mp4 \
python3 tools/package_chatgpt_plugin.py
```

This creates `dist/dux-chatgpt-plugin.zip` with the portable manifest, remote Streamable HTTP endpoint, public listing URLs, and five positive plus three negative review cases. The package builder labels output **draft only** until `--verify-live` passes. That preflight checks the deployed health state, browser runtime, web and policy pages, MCP initialization/tools/widget, and reviewer video URL; it rejects reserved example domains and policy placeholders. If `DUX_OPENAI_APPS_CHALLENGE` is set, it also checks the live domain-verification response. The package points its website, privacy, and terms URLs at the deployed Dux origin and its support URL at the repository's issue tracker.

After deployment and policy review, use the same command with `--verify-live` to prepare a submission candidate. The checks require a public HTTPS domain and a reviewer-accessible recording; they cannot replace publisher identity verification or OpenAI's review.

Before submission, replace the draft policy pages with reviewed production policies and real operator/contact details. Then upload the ZIP through the [OpenAI plugin submission portal](https://platform.openai.com/plugins), verify the publisher identity, complete domain verification, connect and scan the live MCP endpoint, and submit for review. Public release happens only after OpenAI approves the submission. A local address or temporary tunnel is for testing only.

For OpenAI domain verification, set `DUX_OPENAI_APPS_CHALLENGE` to the exact token from the portal. Dux then serves that token at `/.well-known/openai-apps-challenge`.

## Docker deployment

The included Docker image builds Qwasm and the shared LibreQuake runtime, extracts only the palette used to convert each image, then runs the standard-library web/API server. The final image keeps the compressed runtime data and serves it with HTTP gzip, avoiding a second 140 MB uncompressed copy. Maps remain in the shared runtime, while each game stores only its indexed 128 × 128 texture (about 16 KB) in its own PAK. Build context needs the two `librequake-assets/*.pak` files described above; the full source PAKs are not copied into the final image. Persistent storage is configured through the `dux-games` Docker volume.

```sh
DUX_PUBLIC_URL=https://play.example.com docker compose up --build
```

OpenAI and share links must use the same stable public HTTPS origin. A host with a persistent disk is required for this filesystem-backed implementation. Vercel's ephemeral function filesystem is not suitable for game storage as-is; deploy only after adding a durable object-store adapter.

## API and MCP

- `GET /v1/health` — API, LibreQuake asset, and browser-runtime readiness.
- `POST /v1/games` — JSON `{ "width": 128, "height": 128, "rgba": "<base64 RGBA>" }`; returns the game ID, share URL, expiry, PAK URL, and whether this server can play it.
- `GET /v1/games/{id}` — public game manifest until expiry.
- `GET /v1/games/{id}/texture` and `/pak` — final texture pixels and the game's small indexed-texture package.
- `POST /mcp` — stateless Streamable HTTP MCP endpoint with `open_creator`, `create_game`, and `get_game`, plus the `ui://widget/dux-create.html` resource.
- `GET /play/{id}` — share page; `GET /privacy` and `/terms` serve the draft policy pages.

The shared engine applies the game's image texture to selected surfaces on the start map and across the 16 campaign maps. A typical runtime data package is about 140 MB uncompressed (about 62 MB gzip); the server serves a deterministic gzip sidecar when accepted by the browser. The runtime files are generated locally and are not committed.

## Tests

```sh
python3 -m unittest -v test_server.py
```

The tests use synthetic BSP/PAK fixtures, exercise palette conversion, compact game packaging and engine texture-override targets, and check MCP HTTP initialization, tool discovery, notifications, and stateless GET behavior. They do not need the LibreQuake files. Run `python3 tools/package_chatgpt_plugin.py --help` to inspect plugin-package options.

## Licensing

Original Dux application code is MIT-licensed. The Qwasm-based runtime is GPL-2.0; SDL2 is zlib-licensed; LibreQuake artwork is BSD-licensed. Source PAKs and generated browser runtime are excluded from Git. Follow `engine/NOTICE.md`, preserve third-party notices when hosting the runtime, and never copy commercial Quake game data into the repository or deployment.
