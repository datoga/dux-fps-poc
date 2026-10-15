# Paquete de Dux para ChatGPT

Este paquete conecta ChatGPT con el servidor MCP de Dux. El paquete se genera cuando el servidor ya tiene un dominio HTTPS estable; no lleva claves ni imágenes del usuario.

Desde la raíz del proyecto:

```sh
DUX_PUBLIC_URL=https://tu-dominio.example \
DUX_DEMO_RECORDING_URL=https://tu-dominio.example/dux-demo.mp4 \
python3 tools/package_chatgpt_plugin.py
```

El ZIP aparecerá en `dist/dux-chatgpt-plugin.zip`. Sin el vídeo, el paquete se marca como borrador. Antes de enviarlo, reemplaza las políticas en borrador por políticas revisadas con los datos reales del operador. Después, completa en OpenAI la verificación de identidad y dominio, conecta y escanea el endpoint MCP, y envía el paquete a revisión. El directorio público requiere aprobación; un túnel local sirve para probar una conexión personalizada, pero no para publicar.

La configuración de conexión se crea dentro del ZIP como `mcp.json` con el endpoint `/mcp`. El paquete incluye también `skills/dux-create/SKILL.md`, que guía a ChatGPT por el flujo de selección, confirmación y publicación de la partida. El manifiesto incluye los casos de prueba de revisión inicial y apunta las políticas al dominio desplegado. Revisa el resultado del escaneo y completa los campos de publisher en el portal.
