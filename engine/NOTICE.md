# Engine and game-data notices

The browser runtime is built from [Qwasm](https://github.com/quinteger2/qwasm), revision [`f56b5e71e4be8effede29bae1785a5306dcc0249`](https://github.com/quinteger2/qwasm/tree/f56b5e71e4be8effede29bae1785a5306dcc0249). Qwasm and its Quake engine source are licensed under GPL-2.0; the full license is in [GPL-2.0.txt](GPL-2.0.txt). `shell.html` is based on Qwasm's `WinQuake/shell.html` and adds loading of the per-game Dux PAK before the engine starts.

The build uses SDL2 2.32.10 from the Emscripten SDL2 port. Its license is included in [SDL2-LICENSE.txt](SDL2-LICENSE.txt).

The generated `qwasm-runtime/index.data` contains a curated subset of LibreQuake's `pak0.pak` and `pak1.pak` files. LibreQuake describes its artwork as BSD-licensed: [LibreQuake source](https://github.com/lavenderdotpet/LibreQuake). The source PAKs and generated runtime are excluded from this repository. Keep this notice with any deployed runtime and verify the source asset package's copyright notices before distribution.
