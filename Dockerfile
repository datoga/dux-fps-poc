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
COPY --from=engine-build /build/qwasm-runtime ./qwasm-runtime
RUN mkdir -p /app/data/games

ENV DUX_HOST=0.0.0.0 \
    DUX_PORT=8000 \
    DUX_DATA_DIR=/app/data/games
EXPOSE 8000
CMD ["python3", "server.py"]
