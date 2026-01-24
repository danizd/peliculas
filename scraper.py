#!/usr/bin/env python3
"""
Script para obtener películas/series de MejorTorrent,
buscar sus notas en FilmAffinity y notificar por Telegram
si la nota es superior a 7.
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

# Cargar variables de entorno desde .env (si existe)
load_dotenv()

# Configuración
MEJORTORRENT_URL = "https://www40.mejortorrent.eu/torrents"
FILMAFFINITY_SEARCH_URL = "https://www.filmaffinity.com/es/search.php?stext="
HISTORIAL_FILE = "historial.json"
MIN_RATING = 7.0
MAX_PELICULAS_POR_EJECUCION = 20  # Limitar para evitar rate limiting

# Headers para simular navegador (más completos para evitar 403)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Crear sesión persistente para MejorTorrent
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


def init_filmaffinity_session() -> bool:
    """Inicializa la sesión de FilmAffinity (ya no es necesario con cloudscraper)."""
    print("[OK] Scraper de FilmAffinity listo (cloudscraper)")
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


def get_mejortorrent_titles() -> list[dict]:
    """Obtiene los títulos de películas y series de MejorTorrent."""
    titles = []
    
    try:
        response = session.get(MEJORTORRENT_URL, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Buscar enlaces de películas y series (estructura actual de MejorTorrent)
        # Los enlaces tienen formato: /pelicula/ID/nombre o /serie/ID/ID/nombre
        links = soup.select("a[href*='/pelicula/'], a[href*='/serie/']")
        
        seen = set()
        for link in links:
            href = str(link.get("href", ""))
            
            # Filtrar enlaces de navegación (los que no tienen ID numérico)
            if not re.search(r'/(pelicula|serie)/\d+', href):
                continue
            
            title = link.get_text(strip=True)
            
            # Filtrar títulos válidos (evitar vacíos o muy cortos)
            if title and len(title) > 3 and title not in seen:
                # Limpiar título (quitar año, calidad, etc. para búsqueda)
                clean_title = clean_title_for_search(title)
                if clean_title:
                    seen.add(title)
                    titles.append({
                        "original_title": title,
                        "clean_title": clean_title,
                        "url": href
                    })
        
        print(f"[+] Encontrados {len(titles)} titulos en MejorTorrent")
        
    except requests.RequestException as e:
        print(f"Error accediendo a MejorTorrent: {e}")
    
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
            
            response = filmaffinity_scraper.get(search_url, timeout=15)
            
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
        
        response = filmaffinity_scraper.get(url, timeout=15)
        
        # Manejar rate limiting también aquí
        if response.status_code == 429:
            print(f"  [!] Rate limit en detalles, esperando 30s...")
            time.sleep(30)
            response = filmaffinity_scraper.get(url, timeout=15)
        
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
    genre_elem = soup.select_one('[itemprop="genre"], .genres span, dd:contains("Género")')
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
📥 Encontrada en MejorTorrent
"""
    return message


def main():
    """Función principal del script."""
    print(f"[*] Iniciando scraper - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)
    
    # Obtener configuración de Telegram
    telegram_token, telegram_chat_id = get_telegram_config()
    
    # Cargar historial de películas ya analizadas
    historial = cargar_historial()
    print(f"[+] Historial cargado: {len(historial['peliculas'])} peliculas previas")
    
    # Inicializar sesión de FilmAffinity
    print("\n[*] Conectando a FilmAffinity...")
    if not init_filmaffinity_session():
        print("[X] No se pudo conectar a FilmAffinity. Abortando.")
        return
    
    # Obtener títulos de MejorTorrent
    titles = get_mejortorrent_titles()
    
    if not titles:
        print("[X] No se encontraron titulos en MejorTorrent")
        return
    
    # Buscar cada título en FilmAffinity
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
        
        print(f"[{i}/{len(titulos_a_procesar)}] Buscando: {clean_title}")
        
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
    
    if good_movies and telegram_token and telegram_chat_id:
        print("\n[*] Enviando notificaciones por Telegram...")
        for movie in good_movies:
            message = format_telegram_message(movie["info"], movie["original"])
            if send_telegram_message(telegram_token, telegram_chat_id, message):
                print(f"  [OK] Notificacion enviada: {movie['info'].get('title')}")
            time.sleep(0.5)  # Evitar rate limiting de Telegram
    elif good_movies:
        print("\n[!] No se pueden enviar notificaciones (Telegram no configurado)")
        for movie in good_movies:
            print(f"  - {movie['info'].get('title')} ({movie['info'].get('rating')})")
    
    print("\n[OK] Script finalizado")


if __name__ == "__main__":
    main()
