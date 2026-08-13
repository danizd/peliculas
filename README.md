# Notificador de películas - FilmAffinity

Este repositorio contiene un script (`scraper.py`) que:
- Obtiene títulos de películas y series de las secciones de novedades de FilmAffinity (cartelera de cines de España y novedades de Netflix).
- Extrae la nota, género y plataformas de cada ficha en FilmAffinity.
- Envía notificaciones por Telegram cuando la nota es >= 7.
- Guarda un historial (`historial.json`) para no procesar duplicados.

> Nota: antes la fuente de títulos era MejorTorrent, pero su WAF de Cloudflare
> (challenge anti-bot) bloquea todas las peticiones automatizadas, incluso con
> Playwright. FilmAffinity es accesible sin bloqueos y además aporta la nota
> directamente, así que se usa como fuente única.

## Ejecución local

- Activar el entorno virtual:
  `& .\env-peliculas\Scripts\Activate.ps1`
- Instalar dependencias (si no están):
  `pip install -r requirements.txt`
- Establecer variables de entorno (opcional):
  `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`
- Ejecutar el scraper:
  `python -u scraper.py`

## Configuración del método de scraping

- `SCRAPER_METHOD=cloudscraper` (por defecto): peticiones HTTP con `cloudscraper`.
- `SCRAPER_METHOD=playwright`: usa un navegador real headless, útil si FilmAffinity empieza a bloquear.
- Si usas Playwright por primera vez, instala también los navegadores:
  `python -m playwright install chromium`

## Fuente de títulos

Las secciones de FilmAffinity que se consultan están en `FILMAFFINITY_NEW_SOURCES` dentro de `scraper.py`:

- `https://www.filmaffinity.com/es/cat_new_th_es.html` — Cartelera cines España
- `https://www.filmaffinity.com/es/cat_new_netflix.html` — Novedades Netflix

Puedes añadir más secciones (p. ej. `cat_new_amazon_es.html`, `cat_new_hbo_es.html`) añadiendo la URL a esa lista.

## Ejecución automática (GitHub Actions)

El proyecto se ejecuta a diario mediante un workflow de GitHub Actions (`.github/workflows/scraper.yml`):
1. Instala dependencias y el navegador de Playwright.
2. Ejecuta `scraper.py` con `SCRAPER_METHOD=cloudscraper`.
3. Hace commit de `historial.json` actualizado.

Los secretos `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` deben estar configurados en Settings → Secrets and variables → Actions del repositorio.

## Estructura del historial

`historial.json` guarda una entrada por título ya analizado (clave = título normalizado) con su nota, fecha y si fue notificado. Así el scraper no vuelve a notificar películas ya vistas.
