# Paquete de Dux para ChatGPT

Este paquete conecta ChatGPT con el servidor MCP de Dux. El paquete se genera cuando el servidor ya tiene un dominio HTTPS estable; no lleva claves ni imágenes del usuario.

Desde la raíz del proyecto:

```sh
DUX_PUBLIC_URL=https://tu-dominio.example python3 tools/package_chatgpt_plugin.py
```

El ZIP aparecerá en `dist/dux-chatgpt-plugin.zip`. Súbelo en el portal de plugins de OpenAI y completa la verificación de identidad, escaneo de herramientas y revisión. El directorio público requiere un endpoint remoto HTTPS estable, además de revisión y aprobación; un túnel local sirve para probar una conexión personalizada, pero no para publicar.

La configuración de conexión se crea dentro del ZIP como `mcp.json` con el endpoint `/mcp`. Revisa en el portal los nombres, prompts, políticas y la declaración de que el servidor recibe la textura final, no la imagen original. El dominio de privacidad y términos del manifiesto usa los documentos publicados en GitHub.
