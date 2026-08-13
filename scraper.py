#!/usr/bin/env python3
"""
Script para obtener películas/series de las secciones de novedades
de FilmAffinity (cartelera de cines y catálogos de streaming),
buscar sus notas y notificar por Telegram si la nota es superior a 7.
"""

import os
import re
import time
import random
import json
import requests
import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
from datetime import datetime
from typing import Any
from dotenv import load_dotenv

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

# Cargar variables de entorno desde .env (si existe)
load_dotenv()

# Configuración
# Secciones de novedades de FilmAffinity usadas como fuente de títulos
# (cartelera de cines y catálogos de streaming con títulos recientes)
FILMAFFINITY_NEW_SOURCES = [
    "https://www.filmaffinity.com/es/cat_new_th_es.html",  # Cartelera cines España
    "https://www.filmaffinity.com/es/cat_new_netflix.html",  # Novedades Netflix
]
FILMAFFINITY_SEARCH_URL = "https://www.filmaffinity.com/es/search.php?stext="
HISTORIAL_FILE = "historial.json"
MIN_RATING = 7.0
MAX_PELICULAS_POR_EJECUCION = 20  # Limitar para evitar rate limiting
SCRAPER_METHOD = os.getenv("SCRAPER_METHOD", "cloudscraper").strip().lower()
PLAYWRIGHT_BROWSER = os.getenv("PLAYWRIGHT_BROWSER", "chromium").strip().lower()
PLAYWRIGHT_HEADLESS = os.getenv("PLAYWRIGHT_HEADLESS", "true").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
PLAYWRIGHT_TIMEOUT_MS = int(os.getenv("PLAYWRIGHT_TIMEOUT_MS", "45000"))

# Headers para simular navegador (más completos para evitar 403)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.7922.137 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.google.com/",
    "DNT": "1",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "TE": "trailers",
}

# Crear sesión persistente
session = requests.Session()
session.headers.update(HEADERS)

# Crear scraper para FilmAffinity (bypass Cloudflare)
filmaffinity_scraper = cloudscraper.create_scraper(
    browser={
        'browser': 'chrome',
        'platform': 'windows',
        'mobile': False
    }
)


class BrowserFetchResponse:
    """Respuesta mínima compatible con requests para el modo Playwright."""

    def __init__(self, status_code: int, text: str, url: str, headers: dict | None = None):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Error for url: {self.url}",
                response=None,
            )


_playwright_manager = None
_playwright_browser = None
_playwright_context = None


def get_scraper_method() -> str:
    """Devuelve el método de scraping configurado."""
    if SCRAPER_METHOD in {"cloudscraper", "playwright"}:
        return SCRAPER_METHOD
    print(f"[!] SCRAPER_METHOD='{SCRAPER_METHOD}' no es válido, usando 'cloudscraper'")
    return "cloudscraper"


def _get_playwright_browser_launcher():
    """Obtiene el lanzador del navegador según la configuración."""
    if sync_playwright is None:
        raise RuntimeError(
            "Playwright no está instalado. Instala 'playwright' y ejecuta 'python -m playwright install chromium'."
        )

    manager = sync_playwright().start()

    if PLAYWRIGHT_BROWSER == "chromium":
        browser_launcher = manager.chromium
    elif PLAYWRIGHT_BROWSER == "firefox":
        browser_launcher = manager.firefox
    elif PLAYWRIGHT_BROWSER == "webkit":
        browser_launcher = manager.webkit
    else:
        manager.stop()
        raise RuntimeError(
            f"Navegador Playwright no soportado: {PLAYWRIGHT_BROWSER}. Usa chromium, firefox o webkit."
        )

    return manager, browser_launcher


def get_playwright_context():
    """Crea o reutiliza un contexto de navegador para Playwright."""
    global _playwright_manager, _playwright_browser, _playwright_context

    if _playwright_context is not None:
        return _playwright_context

    manager, browser_launcher = _get_playwright_browser_launcher()
    safe_headers = {
        key: value
        for key, value in HEADERS.items()
        if key not in {"Connection", "Accept-Encoding", "TE"}
    }

    _playwright_manager = manager
    _playwright_browser = browser_launcher.launch(headless=PLAYWRIGHT_HEADLESS)
    _playwright_context = _playwright_browser.new_context(
        user_agent=HEADERS["User-Agent"],
        locale="es-ES",
        timezone_id="Europe/Madrid",
        viewport={"width": 1365, "height": 768},
        ignore_https_errors=True,
        extra_http_headers=safe_headers,
    )

    return _playwright_context


def close_scraper_resources() -> None:
    """Cierra recursos del navegador si se está usando Playwright."""
    global _playwright_manager, _playwright_browser, _playwright_context

    if _playwright_context is not None:
        _playwright_context.close()
        _playwright_context = None

    if _playwright_browser is not None:
        _playwright_browser.close()
        _playwright_browser = None

    if _playwright_manager is not None:
        _playwright_manager.stop()
        _playwright_manager = None


def fetch_page_with_method(client: Any, url: str, timeout: int, method: str):
    """Obtiene una página con un método concreto de scraping."""
    if method == "playwright":
        try:
            context = get_playwright_context()
            page = context.new_page()
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)
                status_code = response.status if response else 200

                if status_code in {403, 503}:
                    wait_time = random.randint(4, 8) * 1000
                    page.wait_for_timeout(wait_time)

                    parsed_url = requests.utils.urlparse(url)
                    origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
                    page.goto(origin, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT_MS)
                    page.wait_for_timeout(wait_time)

                    response = page.goto(url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT_MS)
                    status_code = response.status if response else 200

                response_headers = response.headers if response else {}
                return BrowserFetchResponse(status_code, page.content(), page.url, response_headers)
            finally:
                page.close()
        except Exception as exc:
            raise requests.RequestException(str(exc)) from exc

    return client.get(url, timeout=timeout)


def fetch_page(client: Any, url: str, timeout: int = 15):
    """Obtiene una página con el método configurado y hace fallback automático al alternativo."""
    preferred_method = get_scraper_method()
    fallback_method = "cloudscraper" if preferred_method == "playwright" else "playwright"
    last_response = None
    last_error = None

    for method in (preferred_method, fallback_method):
        try:
            response = fetch_page_with_method(client, url, timeout, method)
            last_response = response

            if response.status_code not in {403, 503}:
                if method != preferred_method:
                    print(f"  [*] Fallback aplicado: {preferred_method} -> {method}")
                return response

            print(f"  [!] Método {method} devolvió {response.status_code} para {url}")
        except Exception as exc:
            last_error = exc
            print(f"  [!] Método {method} falló para {url}: {exc}")

    if last_response is not None:
        return last_response

    if last_error is not None:
        raise requests.RequestException(str(last_error)) from last_error

    raise requests.RequestException(f"No se pudo obtener la página: {url}")


def init_filmaffinity_session() -> bool:
    """Inicializa el método de scraping configurado."""
    method = get_scraper_method()

    if method == "playwright":
        try:
            get_playwright_context()
            print("[OK] Scraper listo (Playwright)")
            return True
        except Exception as exc:
            print(f"[!] No se pudo iniciar Playwright: {exc}")
            return False

    print("[OK] Scraper listo (cloudscraper)")
    return True


def get_telegram_config():
    """Obtiene la configuración de Telegram desde variables de entorno."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        print("[!] Configuracion de Telegram no encontrada.")
        print("Configura TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID")
        return None, None
    
    return token, chat_id


def send_telegram_message(token: str, chat_id: str, message: str) -> bool:
    """Envía un mensaje a Telegram."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except requests.RequestException as e:
        print(f"Error enviando mensaje Telegram: {e}")
        return False


def get_filmaffinity_new_titles() -> list[dict]:
    """Obtiene títulos de películas y series desde las secciones de novedades de FilmAffinity."""
    titles = []
    seen_urls = set()

    for source_url in FILMAFFINITY_NEW_SOURCES:
        try:
            print(f"[*] Obteniendo novedades de FilmAffinity: {source_url}")
            response = fetch_page(filmaffinity_scraper, source_url, timeout=20)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            cards = soup.select("div[data-movie-id]")
            encontrados = 0
            for card in cards:
                link = card.select_one("a[href*='/es/film'], a[href*='/es/series']")
                if not link:
                    continue
                url = str(link.get("href", "")).strip()
                title = (link.get("title") or link.get_text(strip=True) or "").strip()
                if not url or not title:
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                clean_title = clean_title_for_search(title)
                if not clean_title:
                    continue
                titles.append({
                    "original_title": title,
                    "clean_title": clean_title,
                    "url": url,
                })
                encontrados += 1

            print(f"  [OK] {encontrados} títulos desde {source_url}")
        except requests.RequestException as e:
            print(f"  [!] Error accediendo a {source_url}: {e}")
            continue

    print(f"[+] Encontrados {len(titles)} títulos en FilmAffinity")
    return titles


def clean_title_for_search(title: str) -> str:
    """Limpia el título para búsqueda en FilmAffinity."""
    # Eliminar patrones comunes: año, calidad, idioma, etc.
    patterns = [
        r'\(?\d{4}\)?',  # Año entre paréntesis o solo
        r'\b(720p|1080p|2160p|4K|HDRip|BDRip|WEB-DL|HDTV|DVDRip)\b',
        r'\b(Castellano|Latino|VOSE|Spanish|English)\b',
        r'\b(Temporada|Cap[ií]tulo|S\d{1,2}E\d{1,2}|T\d{1,2})\b',
        r'\[.*?\]',  # Contenido entre corchetes
        r'\(.*?\)',  # Contenido entre paréntesis
        r'MicroHD|x264|x265|HEVC|AC3|DTS|BluRay|Blu-Ray',
        r'\d+[ªº]',  # Números ordinales (1ª, 2º, etc.)
        r'\bHD\b|\bSD\b',  # Calidades
        r'www\.\S+',  # URLs
    ]
    
    clean = title
    for pattern in patterns:
        clean = re.sub(pattern, '', clean, flags=re.IGNORECASE)
    
    # Limpiar espacios múltiples y caracteres especiales
    clean = re.sub(r'[_\-\.]+', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    
    # Eliminar palabras sueltas cortas al final (residuos de limpieza)
    clean = re.sub(r'\s+[a-z]{1,2}$', '', clean, flags=re.IGNORECASE)
    
    return clean if len(clean) > 2 else ""


def normalizar_titulo(titulo: str) -> str:
    """Normaliza un título para usarlo como clave única en el historial."""
    titulo = titulo.lower().strip()
    # Reemplazar acentos
    reemplazos = {'á': 'a', 'é': 'e', 'í': 'i', 'ó': 'o', 'ú': 'u', 'ñ': 'n',
                  'à': 'a', 'è': 'e', 'ì': 'i', 'ò': 'o', 'ù': 'u', 'ü': 'u'}
    for acento, letra in reemplazos.items():
        titulo = titulo.replace(acento, letra)
    # Solo alfanuméricos y espacios
    titulo = re.sub(r'[^a-z0-9\s]', '', titulo)
    titulo = re.sub(r'\s+', ' ', titulo).strip()
    return titulo


def cargar_historial() -> dict:
    """Carga el historial desde el archivo JSON."""
    if os.path.exists(HISTORIAL_FILE):
        try:
            with open(HISTORIAL_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"[!] Error cargando historial: {e}")
    return {"peliculas": {}}


def guardar_historial(historial: dict) -> bool:
    """Guarda el historial en el archivo JSON."""
    try:
        with open(HISTORIAL_FILE, 'w', encoding='utf-8') as f:
            json.dump(historial, f, ensure_ascii=False, indent=2)
        return True
    except IOError as e:
        print(f"[!] Error guardando historial: {e}")
        return False


def agregar_al_historial(historial: dict, titulo_limpio: str, nota: float, notificado: bool) -> None:
    """Añade una película al historial."""
    clave = normalizar_titulo(titulo_limpio)
    historial["peliculas"][clave] = {
        "titulo": titulo_limpio,
        "nota": nota,
        "fecha": datetime.now().strftime("%Y-%m-%d"),
        "notificado": notificado
    }


def ya_analizada(historial: dict, titulo_limpio: str) -> bool:
    """Comprueba si un título ya está en el historial."""
    clave = normalizar_titulo(titulo_limpio)
    return clave in historial["peliculas"]


def search_filmaffinity(title: str, retries: int = 3) -> dict | None:
    """Busca una película/serie en FilmAffinity y obtiene su información."""
    search_url = FILMAFFINITY_SEARCH_URL + quote_plus(title)
    
    for attempt in range(retries):
        try:
            # Delay más largo para evitar rate limiting (3-6 segundos)
            delay = random.uniform(3, 6) if attempt == 0 else random.uniform(10, 20)
            time.sleep(delay)
            
            response = fetch_page(filmaffinity_scraper, search_url, timeout=15)
            
            # Si es 429 (Too Many Requests), esperar mucho más
            if response.status_code == 429:
                if attempt < retries - 1:
                    wait_time = (attempt + 1) * 30  # 30, 60, 90 segundos
                    print(f"  [!] Error 429 (rate limit), esperando {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"  [X] Rate limit excedido para '{title}'")
                    return None
            
            # Si es 403, esperar y reintentar
            if response.status_code == 403:
                if attempt < retries - 1:
                    wait_time = (attempt + 1) * 15
                    print(f"  [!] Error 403, esperando {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    print(f"  [X] Acceso denegado para '{title}'")
                    return None
            
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            
            # Si hay resultados, puede redirigir directamente o mostrar lista
            # Buscar el primer resultado
            movie_card = soup.select_one(".se-it, .movie-card, [data-movie-id]")
            
            if movie_card:
                # Obtener enlace a la página de la película
                movie_link = movie_card.select_one("a[href*='/film']")
                if movie_link:
                    movie_url = str(movie_link.get("href", ""))
                    if movie_url and not movie_url.startswith("http"):
                        movie_url = "https://www.filmaffinity.com" + movie_url
                    if movie_url:
                        return get_filmaffinity_details(movie_url)
            
            # Buscar directamente en la página si es resultado único
            rating = extract_rating(soup)
            if rating:
                return extract_movie_info(soup, search_url)
            
            # Intentar con el primer enlace de película encontrado
            first_movie = soup.select_one("a[href*='/es/film']")
            if first_movie:
                movie_url = str(first_movie.get("href", ""))
                if movie_url and not movie_url.startswith("http"):
                    movie_url = "https://www.filmaffinity.com" + movie_url
                if movie_url:
                    return get_filmaffinity_details(movie_url)
            
            # Si llegamos aquí sin encontrar nada, no reintentar
            return None
                
        except Exception as e:
            if attempt < retries - 1:
                print(f"  [!] Error de conexion, reintentando...")
                continue
            print(f"Error buscando '{title}' en FilmAffinity: {e}")
    
    return None


def get_filmaffinity_details(url: str) -> dict | None:
    """Obtiene los detalles de una película desde su página de FilmAffinity."""
    try:
        # Delay antes de cada petición de detalles
        time.sleep(random.uniform(2, 4))
        
        response = fetch_page(filmaffinity_scraper, url, timeout=15)
        
        # Manejar rate limiting también aquí
        if response.status_code == 429:
            print(f"  [!] Rate limit en detalles, esperando 30s...")
            time.sleep(30)
            response = fetch_page(filmaffinity_scraper, url, timeout=15)
        
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        return extract_movie_info(soup, url)
        
    except Exception as e:
        print(f"Error obteniendo detalles de {url}: {e}")
    
    return None


def extract_rating(soup: BeautifulSoup) -> float | None:
    """Extrae la nota de FilmAffinity."""
    # Buscar diferentes selectores para la nota
    rating_selectors = [
        "#movie-rat-avg",
        ".avg-rating",
        '[itemprop="ratingValue"]',
        ".avgrat-box",
        ".rat-avg"
    ]
    
    for selector in rating_selectors:
        element = soup.select_one(selector)
        if element:
            rating_text = element.get_text(strip=True).replace(",", ".")
            try:
                return float(rating_text)
            except ValueError:
                continue
    
    return None


def extract_movie_info(soup: BeautifulSoup, url: str) -> dict[str, Any] | None:
    """Extrae toda la información de la película."""
    info: dict[str, Any] = {"url": url}
    
    # Título
    title_elem = soup.select_one("#main-title span, .movie-title, h1[itemprop='name']")
    info["title"] = title_elem.get_text(strip=True) if title_elem else "Desconocido"
    
    # Nota
    info["rating"] = extract_rating(soup)
    
    # Género
    genre_elem = soup.select_one('[itemprop="genre"], .genres span, dd:-soup-contains("Género")')
    if not genre_elem:
        # Buscar en la estructura de FilmAffinity
        for dt in soup.select("dt"):
            if "Género" in dt.get_text():
                genre_elem = dt.find_next("dd")
                break
    
    info["genre"] = genre_elem.get_text(strip=True) if genre_elem else "No especificado"
    
    # Disponible en (plataformas de streaming)
    platforms = []
    platform_section = soup.select(".just-watch-prov img, .streaming-providers img, [alt*='disponible']")
    for img in platform_section:
        alt = img.get("alt", "")
        if alt:
            platforms.append(alt)
    
    # También buscar enlaces de JustWatch o similar
    jw_links = soup.select("a[href*='justwatch'], .wtp-links a")
    for link in jw_links:
        platform_name = link.get_text(strip=True)
        if platform_name and platform_name not in platforms:
            platforms.append(platform_name)
    
    info["platforms"] = platforms if platforms else ["No disponible en streaming"]
    
    return info if info.get("rating") else None


def format_telegram_message(movie_info: dict, original_title: str) -> str:
    """Formatea el mensaje para Telegram."""
    platforms_str = ", ".join(movie_info.get("platforms", ["No disponible"]))
    
    message = f"""🎬 <b>Nueva película/serie con buena nota!</b>

<b>Título:</b> {movie_info.get('title', original_title)}
<b>Nota FilmAffinity:</b> ⭐ {movie_info.get('rating', 'N/A')}
<b>Género:</b> {movie_info.get('genre', 'No especificado')}
<b>Disponible en:</b> {platforms_str}

🔗 <a href="{movie_info.get('url', '')}">Ver en FilmAffinity</a>
📥 Nueva en FilmAffinity
"""
    return message


def format_summary_message(saltadas: int, nuevas_analizadas: int, good_movies_count: int, min_rating: float) -> str:
    """Formatea el mensaje de resumen para Telegram."""
    message = f"""📊 <b>Resumen de búsqueda de películas</b>

<b>Películas saltadas:</b> {saltadas}
<b>Películas analizadas:</b> {nuevas_analizadas}
<b>Películas con nota ⭐>{min_rating}:</b> {good_movies_count}

<i>Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>
"""
    return message


def format_no_results_message() -> str:
    """Formatea el mensaje cuando no hay resultados."""
    message = f"""❌ <b>Sin resultados en la búsqueda</b>

No se encontraron películas o series nuevas que cumplan los criterios de búsqueda.

<i>Última actualización: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>
"""
    return message


def format_error_message(error: str) -> str:
    """Formatea el mensaje de error para Telegram."""
    message = f"""⚠️ <b>Error durante la ejecución del scraper</b>

<b>Error:</b> <code>{error}</code>

<i>Hora del error: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>
"""
    return message


def main():
    """Función principal del script."""
    print(f"[*] Iniciando scraper - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    # Obtener configuración de Telegram
    telegram_token, telegram_chat_id = get_telegram_config()
    
    try:
        # Cargar historial de películas ya analizadas
        historial = cargar_historial()
        print(f"[+] Historial cargado: {len(historial['peliculas'])} peliculas previas")
        
        # Inicializar sesión de FilmAffinity
        print("\n[*] Conectando a FilmAffinity...")
        if not init_filmaffinity_session():
            print("[X] No se pudo conectar a FilmAffinity. Abortando.")
            error_msg = "No se pudo conectar a FilmAffinity"
            if telegram_token and telegram_chat_id:
                send_telegram_message(telegram_token, telegram_chat_id, format_error_message(error_msg))
            return
        
        # Obtener títulos de las secciones de novedades de FilmAffinity
        titles = get_filmaffinity_new_titles()
        
        if not titles:
            print("[X] No se encontraron titulos en FilmAffinity")
            error_msg = "No se encontraron títulos en FilmAffinity"
            if telegram_token and telegram_chat_id:
                send_telegram_message(telegram_token, telegram_chat_id, format_error_message(error_msg))
            return
        
        # Analizar cada título en FilmAffinity
        good_movies = []
        nuevas_analizadas = 0
        saltadas = 0
        
        # Filtrar primero las que ya están en historial
        titulos_nuevos = []
        for title_info in titles:
            if not ya_analizada(historial, title_info['clean_title']):
                titulos_nuevos.append(title_info)
            else:
                saltadas += 1
        
        print(f"[+] Titulos nuevos a analizar: {len(titulos_nuevos)} (saltadas: {saltadas})")
        
        # Limitar a MAX_PELICULAS_POR_EJECUCION para evitar rate limiting
        titulos_a_procesar = titulos_nuevos[:MAX_PELICULAS_POR_EJECUCION]
        
        if not titulos_a_procesar:
            print("[*] No hay titulos nuevos que analizar")
        else:
            print(f"[*] Procesando {len(titulos_a_procesar)} titulos (max: {MAX_PELICULAS_POR_EJECUCION})\n")
        
        for i, title_info in enumerate(titulos_a_procesar, 1):
            clean_title = title_info['clean_title']
            url = title_info.get('url', '')
            
            print(f"[{i}/{len(titulos_a_procesar)}] Buscando: {clean_title}")
            
            # Como ya tenemos la URL exacta desde el listado, evitamos la búsqueda
            if url:
                movie_info = get_filmaffinity_details(url)
            else:
                movie_info = search_filmaffinity(clean_title)
            
            if movie_info and movie_info.get("rating"):
                rating = movie_info["rating"]
                print(f"  [OK] Encontrada: {movie_info.get('title')} - Nota: {rating}")
                
                notificado = rating >= MIN_RATING
                agregar_al_historial(historial, clean_title, rating, notificado)
                nuevas_analizadas += 1
                
                if notificado:
                    print(f"  [***] Nota superior a {MIN_RATING}!")
                    good_movies.append({
                        "original": title_info["original_title"],
                        "info": movie_info
                    })
            else:
                print(f"  [--] No encontrada o sin nota")
                # Guardar también las no encontradas para no reintentar
                agregar_al_historial(historial, clean_title, 0, False)
                nuevas_analizadas += 1
            
            # Guardar historial después de cada película (por si se interrumpe)
            guardar_historial(historial)
        
        # Guardar historial actualizado (resumen final)
        print(f"\n[+] Historial actualizado: {len(historial['peliculas'])} peliculas totales")
        
        # Enviar notificaciones por Telegram
        print("\n" + "=" * 50)
        print(f"[RESUMEN] Saltadas: {saltadas} | Nuevas: {nuevas_analizadas} | Con nota >{MIN_RATING}: {len(good_movies)}")
        
        if telegram_token and telegram_chat_id:
            print("\n[*] Enviando notificaciones por Telegram...")
            
            # Enviar películas con buena nota (si las hay)
            if good_movies:
                for movie in good_movies:
                    message = format_telegram_message(movie["info"], movie["original"])
                    if send_telegram_message(telegram_token, telegram_chat_id, message):
                        print(f"  [OK] Notificacion enviada: {movie['info'].get('title')}")
                    time.sleep(0.5)  # Evitar rate limiting de Telegram
            else:
                # Si no hay películas, enviar resumen
                print("  [*] No hay películas con nota superior a {}".format(MIN_RATING))
                message = format_no_results_message()
                if send_telegram_message(telegram_token, telegram_chat_id, message):
                    print("  [OK] Mensaje de sin resultados enviado")
            
            # Enviar resumen general
            time.sleep(0.5)
            summary_message = format_summary_message(saltadas, nuevas_analizadas, len(good_movies), MIN_RATING)
            if send_telegram_message(telegram_token, telegram_chat_id, summary_message):
                print("  [OK] Resumen enviado")
        elif good_movies:
            print("\n[!] No se pueden enviar notificaciones (Telegram no configurado)")
            for movie in good_movies:
                print(f"  - {movie['info'].get('title')} ({movie['info'].get('rating')})")
        
        print("\n[OK] Script finalizado")
    
    except Exception as e:
        """Capturar cualquier error y notificar por Telegram."""
        error_message = f"{type(e).__name__}: {str(e)}"
        print(f"\n[ERROR] {error_message}")
        
        if telegram_token and telegram_chat_id:
            print("[*] Enviando notificación de error por Telegram...")
            if send_telegram_message(telegram_token, telegram_chat_id, format_error_message(error_message)):
                print("[OK] Notificación de error enviada")
        
        raise
    finally:
        close_scraper_resources()


if __name__ == "__main__":
    main()
