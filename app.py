import subprocess
import sys
import importlib

modules = [
    "os",
    "time",
    "requests",
    "bs4",
    "json",
    "flask",
]

def install_missing_modules():
    for module in modules:
        try:
            importlib.import_module(module.split('==')[0])
        except ImportError:
            print(f"Installation du module manquant : {module}")
            subprocess.check_call([sys.executable, "-m", "pip", "install", module])

install_missing_modules()

import os
import time
from flask import Flask, Response, jsonify, request
import requests
from bs4 import BeautifulSoup
import json
from threading import Lock, Thread
from functools import lru_cache
import logging

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

DEFAULT_ICON = "https://tr.rbxcdn.com/180DAY-9babd76e0a0b581e7f689f06cac80194/150/150/Image/Webp/noFilter"

# -----------------------------
# Configuration optimisée
# -----------------------------
CACHE_DURATION = 10 * 60  # 10 minutes
REQUEST_TIMEOUT = 15  # Timeout augmenté pour plus de fiabilité
MAX_RETRIES = 3  # Nombre de tentatives en cas d'échec
RETRY_DELAY = 0.5  # Délai entre les tentatives (secondes)
RATE_LIMIT_DELAY = 1.0  # Délai entre les requêtes pour éviter le rate limit

# Keep-alive configuration
KEEP_ALIVE_ENABLED = os.environ.get("KEEP_ALIVE", "true").lower() == "true"
KEEP_ALIVE_INTERVAL = 14 * 60  # Ping toutes les 14 minutes (Render met en veille après 15 min)
KEEP_ALIVE_URL = os.environ.get("RENDER_EXTERNAL_URL")  # URL externe Render

# Cache thread-safe avec timestamps
cache = {}
cache_lock = Lock()
last_request_time = 0
request_lock = Lock()
is_shutting_down = False

# Session réutilisable avec connection pooling
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive"
})

# Adapter pour retry automatique
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

retry_strategy = Retry(
    total=MAX_RETRIES,
    backoff_factor=1,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"]
)
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=20)
session.mount("http://", adapter)
session.mount("https://", adapter)

# -----------------------------
# Keep-Alive System pour Render
# -----------------------------
def keep_alive_ping():
    """Thread qui ping le serveur pour éviter la mise en veille"""
    global is_shutting_down
    
    if not KEEP_ALIVE_ENABLED:
        logger.info("Keep-alive disabled")
        return
    
    # Attendre que le serveur démarre
    time.sleep(60)
    
    while not is_shutting_down:
        try:
            # Utiliser l'URL externe Render si disponible, sinon localhost
            if KEEP_ALIVE_URL:
                url = f"{KEEP_ALIVE_URL}/ping"
            else:
                port = int(os.environ.get("PORT", 8080))
                url = f"http://localhost:{port}/ping"
            
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                logger.info(f"Keep-alive ping successful at {time.strftime('%H:%M:%S')}")
            else:
                logger.warning(f"Keep-alive ping returned status {response.status_code}")
                
        except Exception as e:
            logger.error(f"Keep-alive ping failed: {e}")
        
        # Attendre avant le prochain ping
        time.sleep(KEEP_ALIVE_INTERVAL)

# Démarrer le thread keep-alive
if KEEP_ALIVE_ENABLED:
    keep_alive_thread = Thread(target=keep_alive_ping, daemon=True)
    keep_alive_thread.start()
    logger.info(f"Keep-alive thread started (interval: {KEEP_ALIVE_INTERVAL}s)")

# -----------------------------
# Gestion du rate limiting
# -----------------------------
def wait_for_rate_limit():
    """Attend si nécessaire pour respecter le rate limit"""
    global last_request_time
    with request_lock:
        current_time = time.time()
        time_since_last = current_time - last_request_time
        if time_since_last < RATE_LIMIT_DELAY:
            time.sleep(RATE_LIMIT_DELAY - time_since_last)
        last_request_time = time.time()

# -----------------------------
# Routes de test
# -----------------------------
@app.route("/")
def index():
    """Page d'accueil avec info API"""
    return jsonify({
        "service": "GamePass API",
        "status": "running",
        "endpoints": {
            "/ping": "Health check",
            "/health": "Detailed health status",
            "/gamepasses/<place_id>": "Get gamepasses for a place",
            "/cache/clear": "Clear cache"
        },
        "keep_alive": KEEP_ALIVE_ENABLED,
        "timestamp": time.time()
    })

@app.route("/ping")
def ping():
    """Endpoint pour keep-alive"""
    return jsonify({"status": "ok", "timestamp": time.time()})

@app.route("/health")
def health():
    """Status détaillé du service"""
    with cache_lock:
        cache_size = len(cache)
        
        # Calculer l'âge moyen du cache
        if cache_size > 0:
            avg_age = sum(time.time() - ts for ts, _ in cache.values()) / cache_size
        else:
            avg_age = 0
    
    return jsonify({
        "status": "healthy",
        "cache_size": cache_size,
        "cache_avg_age_seconds": round(avg_age, 2),
        "keep_alive_enabled": KEEP_ALIVE_ENABLED,
        "keep_alive_interval": KEEP_ALIVE_INTERVAL,
        "uptime_seconds": time.time(),
        "timestamp": time.time()
    })

# -----------------------------
# Cache avec LRU pour rootPlaceId
# -----------------------------
@lru_cache(maxsize=1000)
def get_root_place_id_from_universe(universe_id):
    """Récupère le rootPlaceId avec cache LRU"""
    url = f"https://games.roproxy.com/v1/games?universeIds={universe_id}"
    
    wait_for_rate_limit()
    
    try:
        r = session.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        
        if "data" in data and len(data["data"]) > 0:
            root_id = data["data"][0].get("rootPlaceId")
            logger.info(f"Got rootPlaceId {root_id} for universe {universe_id}")
            return root_id
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get rootPlaceId from universe {universe_id}: {e}")
    except (KeyError, IndexError, ValueError) as e:
        logger.error(f"Invalid response structure for universe {universe_id}: {e}")
    
    return None

# -----------------------------
# Fetch HTML avec retry intelligent
# -----------------------------
def fetch_html(place_id):
    """Récupère le HTML avec gestion d'erreurs robuste"""
    url = f"https://www.roproxy.com/games/getgamepassesinnerpartial?startIndex=0&maxRows=50&placeId={place_id}"
    
    wait_for_rate_limit()
    
    for attempt in range(MAX_RETRIES):
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            
            if r.status_code == 429:
                # Rate limited, attendre plus longtemps
                wait_time = RETRY_DELAY * (2 ** attempt)
                logger.warning(f"Rate limited on attempt {attempt + 1}, waiting {wait_time}s")
                time.sleep(wait_time)
                continue
            
            r.raise_for_status()
            
            if "real-game-pass" in r.text or len(r.text) > 100:
                logger.info(f"Successfully fetched HTML for placeId {place_id}")
                return r.text
            else:
                logger.warning(f"No gamepasses found in HTML for placeId {place_id}")
                return r.text  # Retourner quand même pour ne pas retry inutilement
                
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout on attempt {attempt + 1} for placeId {place_id}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed on attempt {attempt + 1} for placeId {place_id}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_DELAY * (attempt + 1))
    
    logger.error(f"All retry attempts failed for placeId {place_id}")
    return None

# -----------------------------
# Parser optimisé
# -----------------------------
def parse_gamepasses(html, place_id):
    """Parse le HTML de manière robuste et optimisée"""
    if not html:
        return []
    
    gamepasses = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        pass_elements = soup.select("li.real-game-pass")
        
        logger.info(f"Found {len(pass_elements)} gamepasses for placeId {place_id}")
        
        for li in pass_elements:
            try:
                # Extraction sécurisée des données
                name_tag = li.select_one(".store-card-name")
                price_tag = li.select_one(".text-robux")
                img_tag = li.select_one("img")
                link_tag = li.select_one("a.gear-passes-asset")
                
                # Parse du prix
                price = 0
                if price_tag:
                    price_text = price_tag.text.strip().replace(',', '')
                    try:
                        price = int(price_text)
                    except ValueError:
                        pass
                
                # Parse du passId
                pass_id = 0
                if link_tag and link_tag.has_attr("href"):
                    try:
                        href_parts = link_tag["href"].split("/")
                        if len(href_parts) > 2:
                            pass_id = int(href_parts[2])
                    except (ValueError, IndexError):
                        pass
                
                gamepass = {
                    "name": name_tag.get("title", "").strip() if name_tag and name_tag.has_attr("title") else "Unknown",
                    "price": price,
                    "expectedPrice": price,
                    "icon": img_tag.get("src", DEFAULT_ICON) if img_tag else DEFAULT_ICON,
                    "passId": pass_id,
                    "productId": 0,
                    "sellerId": 0,
                    "status": "Buy"
                }
                
                gamepasses.append(gamepass)
                
            except Exception as e:
                logger.error(f"Failed to parse individual gamepass for placeId {place_id}: {e}")
                continue
                
    except Exception as e:
        logger.error(f"Failed to parse HTML for placeId {place_id}: {e}")
    
    return gamepasses

# -----------------------------
# Fetch avec fallback intelligent
# -----------------------------
def fetch_gamepasses(place_id, universe_id=None):
    """Récupère les gamepasses avec fallback sur rootPlaceId"""
    # Essayer d'abord avec le placeId fourni
    html = fetch_html(place_id)
    passes = parse_gamepasses(html, place_id) if html else []
    
    # Si vide et universeId fourni, essayer avec rootPlaceId
    if not passes and universe_id:
        logger.info(f"No passes found for {place_id}, trying rootPlaceId from universe {universe_id}")
        root_place_id = get_root_place_id_from_universe(universe_id)
        
        if root_place_id and root_place_id != place_id:
            html = fetch_html(root_place_id)
            passes = parse_gamepasses(html, root_place_id) if html else []
            
            if passes:
                logger.info(f"Found {len(passes)} passes using rootPlaceId {root_place_id}")
    
    return passes

# -----------------------------
# Gestion du cache thread-safe
# -----------------------------
def get_from_cache(place_id):
    """Récupère depuis le cache de manière thread-safe"""
    with cache_lock:
        if place_id in cache:
            ts, data = cache[place_id]
            if time.time() - ts < CACHE_DURATION:
                logger.info(f"Cache hit for placeId {place_id}")
                return data
            else:
                # Nettoyer le cache expiré
                del cache[place_id]
    return None

def save_to_cache(place_id, data):
    """Sauvegarde dans le cache de manière thread-safe"""
    with cache_lock:
        cache[place_id] = (time.time(), data)
        logger.info(f"Cached {len(data)} passes for placeId {place_id}")

# -----------------------------
# Route principale optimisée
# -----------------------------
@app.route("/gamepasses/<int:place_id>")
def get_gamepasses(place_id):
    """Route principale avec cache et fallback"""
    start_time = time.time()
    
    # Récupérer universeId si fourni
    universe_id = None
    try:
        if "universeId" in request.args:
            universe_id = int(request.args.get("universeId"))
    except (ValueError, TypeError):
        logger.warning(f"Invalid universeId parameter: {request.args.get('universeId')}")
    
    # Vérifier le cache
    cached_data = get_from_cache(place_id)
    if cached_data is not None:
        response_time = (time.time() - start_time) * 1000
        logger.info(f"Response time: {response_time:.2f}ms (cached)")
        return Response(
            json.dumps(cached_data, indent=2),
            mimetype="application/json",
            headers={"X-Cache": "HIT", "X-Response-Time": f"{response_time:.2f}ms"}
        )
    
    # Récupérer les gamepasses
    passes = fetch_gamepasses(place_id, universe_id)
    
    # Sauvegarder dans le cache
    save_to_cache(place_id, passes)
    
    response_time = (time.time() - start_time) * 1000
    logger.info(f"Response time: {response_time:.2f}ms (fresh)")
    
    return Response(
        json.dumps(passes, indent=2),
        mimetype="application/json",
        headers={"X-Cache": "MISS", "X-Response-Time": f"{response_time:.2f}ms"}
    )

# -----------------------------
# Nettoyage du cache périodique
# -----------------------------
@app.route("/cache/clear")
def clear_cache():
    """Nettoie le cache manuellement"""
    with cache_lock:
        size = len(cache)
        cache.clear()
    get_root_place_id_from_universe.cache_clear()
    return jsonify({"status": "cache cleared", "items_removed": size})

@app.route("/cache/stats")
def cache_stats():
    """Statistiques détaillées du cache"""
    with cache_lock:
        stats = {
            "total_entries": len(cache),
            "entries": []
        }
        
        for place_id, (ts, data) in cache.items():
            age = time.time() - ts
            stats["entries"].append({
                "place_id": place_id,
                "age_seconds": round(age, 2),
                "expires_in_seconds": round(CACHE_DURATION - age, 2),
                "num_gamepasses": len(data)
            })
        
        # Trier par âge
        stats["entries"].sort(key=lambda x: x["age_seconds"], reverse=True)
    
    return jsonify(stats)

# -----------------------------
# Cleanup gracieux
# -----------------------------
import atexit
import signal

def cleanup():
    """Nettoyage avant l'arrêt"""
    global is_shutting_down
    is_shutting_down = True
    logger.info("Shutting down gracefully...")
    session.close()

atexit.register(cleanup)
signal.signal(signal.SIGTERM, lambda s, f: cleanup())
signal.signal(signal.SIGINT, lambda s, f: cleanup())

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting GamePass API on port {port}")
    logger.info(f"Keep-alive: {'enabled' if KEEP_ALIVE_ENABLED else 'disabled'}")
    if KEEP_ALIVE_URL:
        logger.info(f"Keep-alive URL: {KEEP_ALIVE_URL}")
    
    app.run(host="0.0.0.0", port=port, threaded=True)
