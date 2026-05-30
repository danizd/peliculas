# Notificador de películas - MejorTorrent + FilmAffinity

Este repositorio contiene un script (`scraper.py`) que:
- Obtiene títulos de películas/series desde MejorTorrent.
- Busca la nota y detalles en FilmAffinity.
- Envía notificaciones por Telegram cuando la nota es >= 7.
- Guarda un historial (`historial.json`) para no procesar duplicados.

Ejecución local
- Activar el entorno virtual:
  & .\env-peliculas\Scripts\Activate.ps1
- Instalar dependencias (si no están):
  pip install -r requirements.txt
- Establecer variables de entorno (opcional):
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID y SCRAPER_METHOD
- Ejecutar el scraper:
  python -u scraper.py

Configuración del método de scraping
- `SCRAPER_METHOD=cloudscraper`: mantiene el comportamiento actual con `cloudscraper`.
- `SCRAPER_METHOD=playwright`: usa un navegador real headless para resolver mejor bloqueos antibot.
- Si usas Playwright por primera vez, instala también los navegadores:
  `python -m playwright install chromium`
- `SCRAPER_DIAGNOSTIC=true`: imprime pistas para distinguir challenge, bloqueo o respuesta vacía en MejorTorrent.

Ejecución automática (GitHub Actions)
El proyecto se ejecutaba mediante un workflow de GitHub Actions. Un "workflow" es una automatización configurada en el repositorio que puede ejecutar scripts en servidores de GitHub (runners) según un trigger (por ejemplo: programación cron, push, release). Esto permitía comprobar periódicamente nuevos torrents y enviar notificaciones sin mantener un servidor propio.

Por qué funcionó antes y ahora falla
- Actualmente las peticiones a MejorTorrent responden con `403 Forbidden` y headers indicando `Cf-Mitigated: challenge`. Eso significa que Cloudflare está aplicando un challenge (JavaScript/Client Hints) que requiere ejecución de código en el navegador para resolverlo.
- Antes el site aceptaba peticiones simuladas por `requests`/`cloudscraper` o el challenge no estaba activo; ahora se exige una sesión de navegador real (o cookies válidas/headers específicos). En resumen: el bloqueo proviene de medidas anti-bot/WAF y no de un bug en el script.

Soluciones y opciones
- Usar un navegador real headless (Playwright o Selenium) para que se ejecute el JavaScript y se resuelva el challenge. Recomendado y más fiable.
- Reutilizar cookies y cabeceras del navegador (frágil: caducan y se rompen con frecuencia).
- Usar proxies/rotación de IPs si el bloqueo es por IP.
- Copiar la lógica de acceso del navegador (client hints, Sec-CH-*), pero esto suele ser temporal y poco robusto.

Siguientes pasos sugeridos
- Integrar Playwright para obtener el HTML de la página de torrents y pasar ese HTML al parser actual.
- O bien, si prefieres, exporto instrucciones para copiar cookies del navegador y probar con `cloudscraper`.

Archivo principal: `scraper.py`

Si quieres, puedo:
- Añadir un script de ejemplo con Playwright y actualizar `scraper.py` para usarlo, o
- Generar instrucciones para reproducir las cabeceras/cookies del navegador.

---
Fecha: 2026-05-30
