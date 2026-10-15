FROM emscripten/emsdk:3.1.74 AS engine-build

WORKDIR /build
COPY server.py ./server.py
COPY engine ./engine
COPY librequake-assets ./librequake-assets
RUN python3 engine/build_runtime.py

FROM python:3.12-slim AS app

WORKDIR /app
COPY server.py index.html play.html ./
COPY engine/NOTICE.md engine/GPL-2.0.txt engine/SDL2-LICENSE.txt ./engine/
RUN mkdir -p /app/qwasm-runtime
COPY --from=engine-build /build/qwasm-runtime/game-assets ./qwasm-runtime/game-assets
COPY --from=engine-build /build/qwasm-runtime/index.html ./qwasm-runtime/index.html
COPY --from=engine-build /build/qwasm-runtime/index.js ./qwasm-runtime/index.js
COPY --from=engine-build /build/qwasm-runtime/index.wasm ./qwasm-runtime/index.wasm
COPY --from=engine-build /build/qwasm-runtime/index.data.gz ./qwasm-runtime/index.data.gz
RUN mkdir -p /app/data/games

ENV DUX_HOST=0.0.0.0 \
    DUX_PORT=8000 \
    DUX_DATA_DIR=/app/data/games
EXPOSE 8000
CMD ["python3", "server.py"]
