"""Build the shared Qwasm WebAssembly runtime for local Dux play pages."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import gzip
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
QWASM_URL = "https://github.com/quinteger2/qwasm.git"
QWASM_COMMIT = "f56b5e71e4be8effede29bae1785a5306dcc0249"
OUTPUT = ROOT / "qwasm-runtime"

sys.path.insert(0, str(ROOT))
import server  # noqa: E402


def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def curated_pak(source: Path) -> bytes:
    entries = server.pak_entries(source.read_bytes())
    entries = {
        name: content
        for name, content in entries.items()
        if not name.endswith(".lit")
        and not name.endswith(".dem")
    }
    return server.make_pak(entries)


def curated_game_assets(source: Path) -> dict[str, bytes]:
    """Keep only source BSPs that Dux modifies and the palette used for conversion."""
    entries = server.pak_entries(source.read_bytes())
    names = (server.TARGET_MAP, *server.CAMPAIGN_TEXTURES_BY_MAP, "gfx/palette.lmp")
    return {name: entries[name] for name in names}


def main() -> None:
    if not shutil.which("emcc"):
        raise SystemExit("Emscripten is required. Activate emsdk so `emcc` is on PATH.")
    for asset in (server.PAK0, server.PAK1):
        if not asset.is_file():
            raise SystemExit(f"Missing LibreQuake asset: {asset}")

    with tempfile.TemporaryDirectory(prefix="dux-qwasm-build-") as temp_name:
        temp = Path(temp_name)
        source_override = os.environ.get("QWASM_SOURCE")
        if source_override:
            source_root = Path(source_override).resolve()
            if not (source_root / "WinQuake" / "Makefile.emscripten").is_file():
                raise SystemExit("QWASM_SOURCE must point to the root of a Qwasm checkout.")
        else:
            source_root = temp / "qwasm"
            run(["git", "clone", QWASM_URL, str(source_root)])
            run(["git", "checkout", QWASM_COMMIT], cwd=source_root)

        actual_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_root, text=True
        ).strip()
        if actual_commit != QWASM_COMMIT:
            raise SystemExit(f"Expected Qwasm {QWASM_COMMIT}, got {actual_commit}")

        build_root = temp / "WinQuake"
        shutil.copytree(
            source_root / "WinQuake",
            build_root,
            ignore=shutil.ignore_patterns("*.o", "index.*", "*.exe", "*.obj", "*.dll", "*.lib"),
        )
        id1 = build_root / "id1"
        (id1 / "pak0.pak").write_bytes(curated_pak(server.PAK0))
        (id1 / "pak1.pak").write_bytes(curated_pak(server.PAK1))
        shutil.copy2(ROOT / "engine" / "shell.html", build_root / "shell.html")

        run(["make", "-f", "Makefile.emscripten", "clean"], cwd=build_root)
        run(["make", "-f", "Makefile.emscripten", "-j4"], cwd=build_root)

        OUTPUT.mkdir(exist_ok=True)
        for filename in ("index.html", "index.js", "index.wasm", "index.data"):
            shutil.copy2(build_root / filename, OUTPUT / filename)
            print(f"{filename}: {(OUTPUT / filename).stat().st_size:,} bytes")
        data_file = OUTPUT / "index.data"
        with data_file.open("rb") as source_data, (OUTPUT / "index.data.gz").open("wb") as compressed:
            with gzip.GzipFile(fileobj=compressed, mode="wb", compresslevel=6, mtime=0) as gzip_file:
                shutil.copyfileobj(source_data, gzip_file)
        print(f"index.data.gz: {(OUTPUT / 'index.data.gz').stat().st_size:,} bytes")

        game_assets = OUTPUT / "game-assets"
        shutil.rmtree(game_assets, ignore_errors=True)
        for name, content in curated_game_assets(server.PAK0).items():
            destination = game_assets / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        print(f"game-assets: {sum(path.stat().st_size for path in game_assets.rglob('*') if path.is_file()):,} bytes")


if __name__ == "__main__":
    main()
