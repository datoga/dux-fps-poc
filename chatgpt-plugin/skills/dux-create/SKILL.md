---
name: dux-create
description: Crea y comparte una partida retro de LibreQuake con una imagen elegida por el usuario.
---

# Crear una partida Dux

Usa este flujo cuando el usuario quiera personalizar y compartir una partida retro de LibreQuake.

1. Llama a open_creator para abrir el creador visual. Pide al usuario que seleccione una imagen y revise la textura pixelada en la vista previa.
2. Deja que el usuario pulse Crear partida en el widget. No inventes ni envíes píxeles RGBA y no llames a create_game directamente: el widget prepara y envía la textura final solo después de esa acción.
3. Cuando la herramienta devuelva el resultado, comparte el enlace de juego. Las partidas gratuitas están disponibles durante siete días.
4. Si el resultado indica playable: false, explica que el servidor creó el enlace pero todavía no ofrece el juego jugable. No afirmes que se puede jugar.

Antes de publicar, deja claro que el enlace puede abrirlo cualquiera que lo reciba y que la textura se conserva hasta siete días. No publiques información privada o sensible de otra persona sin permiso. Si el usuario no ha elegido una imagen, una textura o una confirmación, pide que complete esos pasos en el widget.

El widget recibe la textura final de 128 × 128; el servidor no recibe la foto original por la API de creación.
