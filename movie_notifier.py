import os
import json
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

try:
    import cloudscraper
    HAS_CLOUDSCRAPER = True
except ImportError:
    HAS_CLOUDSCRAPER = False

# Load environment variables
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
RATING_THRESHOLD = float(os.getenv("RATING_THRESHOLD", 7.0))
PROCESSED_FILE = os.getenv("PROCESSED_FILE", "processed_torrents.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def get_session():
    if HAS_CLOUDSCRAPER:
        return cloudscraper.create_scraper()
    return requests.Session()

def load_processed():
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_processed(processed):
    with open(PROCESSED_FILE, "w") as f:
        json.dump(list(processed), f)

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

def clean_title(title):
    # Remove common tags like (DVDRip), [720p], etc.
    tags = ["(DVDRip)", "(HDRip)", "(BluRay-1080p)", "[720p]", "[1080p]", "(HDTV)", "(4K)", "(HDTV-720p)", "(HDTV-1080p)"]
    clean = title
    for tag in tags:
        clean = clean.replace(tag, "")
    
    # Remove extra spaces and common artifacts
    clean = clean.split("-")[0].strip() # Take first part for series often formatted as "Name - Xa Temporada"
    return clean

def get_filmaffinity_info(movie_title):
    search_url = f"https://www.filmaffinity.com/es/search.php?stext={movie_title.replace(' ', '+')}"
    try:
        session = get_session()
        response = session.get(search_url, headers=HEADERS)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.content, "html.parser")
        
        # Check if it redirected directly to a movie page or a list
        if "movie.php" in response.url:
            return parse_movie_page(soup, response.url)
        
        # Search results page
        results = soup.select(".se-it .mc-title a")
        if not results:
            return None
        
        # Take the first result
        first_movie_url = f"https://www.filmaffinity.com{results[0]['href']}"
        movie_response = session.get(first_movie_url, headers=HEADERS)
        return parse_movie_page(BeautifulSoup(movie_response.content, "html.parser"), first_movie_url)
        
    except Exception as e:
        print(f"Error searching FilmAffinity for {movie_title}: {e}")
        return None

def parse_movie_page(soup, url):
    try:
        title = soup.find("h1", id="main-title").text.strip() if soup.find("h1", id="main-title") else "Unknown"
        rating_el = soup.find("div", id="movie-rat-avg")
        rating = float(rating_el.text.strip().replace(",", ".")) if rating_el else 0.0
        
        # Genres
        genres = []
        genre_tags = soup.select(".movie-info span[itemprop='genre'] a")
        for tag in genre_tags:
            genres.append(tag.text.strip())
        
        # Available on
        available_on = []
        providers = soup.select(".vwine-p .vwine-p-item img")
        for prov in providers:
            available_on.append(prov.get("alt", "Unknown"))
        
        return {
            "title": title,
            "rating": rating,
            "genres": ", ".join(genres) if genres else "N/A",
            "available": ", ".join(set(available_on)) if available_on else "N/A",
            "url": url
        }
    except Exception as e:
        print(f"Error parsing movie page: {e}")
        return None

def scrape_torrents():
    url = "https://www40.mejortorrent.eu/torrents"
    try:
        session = get_session()
        response = session.get(url, headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        
        torrents = []
        # Based on previous analysis, links are often in a list
        links = soup.select("a[href*='/pelicula/'], a[href*='/serie/']")
        for link in links:
            title = link.text.strip()
            if title:
                torrents.append({
                    "title": title,
                    "url": f"https://www40.mejortorrent.eu{link['href']}" if link['href'].startswith('/') else link['href']
                })
        return torrents
    except Exception as e:
        print(f"Error scraping torrents: {e}")
        return []

def main():
    print("Starting checking for new torrents...")
    processed = load_processed()
    new_torrents = scrape_torrents()
    
    for torrent in new_torrents:
        if torrent['url'] not in processed:
            print(f"New torrent found: {torrent['title']}")
            clean = clean_title(torrent['title'])
            print(f"Searching for: {clean}")
            
            info = get_filmaffinity_info(clean)
            if info and info['rating'] >= RATING_THRESHOLD:
                message = (
                    f"🎬 *¡Recomendación Nueva!*\n\n"
                    f"*Título:* {info['title']}\n"
                    f"*Nota:* ⭐ {info['rating']}\n"
                    f"*Género:* {info['genres']}\n"
                    f"*Disponible en:* {info['available']}\n\n"
                    f"[Ficha FilmAffinity]({info['url']})\n"
                    f"[Link Torrent]({torrent['url']})"
                )
                send_telegram_message(message)
                print(f"Notification sent for {info['title']} (Rating: {info['rating']})")
            
            processed.add(torrent['url'])
            # Save progress as we go to avoid re-sending if it crashes
            save_processed(processed)
            time.sleep(1) # Be nice to FilmAffinity

    print("Finished checking.")

if __name__ == "__main__":
    main()
