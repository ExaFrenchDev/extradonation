import subprocess
import sys
import importlib
import platform

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
            print("Installation du module manquant : {}".format(module))
            subprocess.check_call([sys.executable, "-m", "pip", "install", module])

install_missing_modules()


import os
import time
from flask import Flask, Response, jsonify, request
import requests
from bs4 import BeautifulSoup
import json

app = Flask(__name__)

DEFAULT_ICON = "https://tr.rbxcdn.com/180DAY-9babd76e0a0b581e7f689f06cac80194/150/150/Image/Webp/noFilter"

# -----------------------------
# Configuration retry
# -----------------------------
MAX_RETRIES = 5  # Nombre maximum de tentatives
RETRY_DELAY = 2  # Délai entre chaque tentative (en secondes)

# -----------------------------
# Cache interne
# -----------------------------
CACHE_DURATION = 10 * 60  # 10 minutes
cache = {}  # placeId -> (timestamp, data)

# -----------------------------
# Route test / ping
# -----------------------------
@app.route("/ping")
def ping():
    return jsonify({"status": "ok"})

# -----------------------------
# Helper : récupérer le rootPlaceId depuis l'univers
# -----------------------------
def get_root_place_id_from_universe(universe_id):
    url = f"https://games.roproxy.com/v1/games?universeIds={universe_id}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0]["rootPlaceId"]
    except Exception as e:
        print(f"[API] Failed to get rootPlaceId from universe {universe_id}: {e}")
    return None

# -----------------------------
# Récupère HTML depuis roproxy
# -----------------------------
def fetch_html(place_id, timeout=10):
    url = f"https://www.roproxy.com/games/getgamepassesinnerpartial?startIndex=0&maxRows=50&placeId={place_id}"
    headers = {
        "User-Agent": "Mozilla/5.0"
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200 and "real-game-pass" in r.text:
            return r.text
    except Exception as e:
        print(f"[GamePassAPI] Failed to fetch {url}: {e}")
    return None

# -----------------------------
# Parser HTML en JSON
# -----------------------------
def parse_gamepasses(html, place_id):
    gamepasses = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        for li in soup.select("li.real-game-pass"):
            try:
                name_tag = li.select_one(".store-card-name")
                price_tag = li.select_one(".text-robux")
                img_tag = li.select_one("img")
                link_tag = li.select_one("a.gear-passes-asset")

                gamepasses.append({
                    "name": name_tag["title"].strip() if name_tag and name_tag.has_attr("title") else "Unknown",
                    "price": int(price_tag.text.strip()) if price_tag else 0,
                    "expectedPrice": int(price_tag.text.strip()) if price_tag else 0,
                    "icon": img_tag["src"] if img_tag and img_tag.has_attr("src") else DEFAULT_ICON,
                    "passId": int(link_tag["href"].split("/")[2]) if link_tag and link_tag.has_attr("href") else 0,
                    "productId": 0,
                    "sellerId": 0,
                    "status": "Buy"
                })
            except Exception as e:
                print(f"[GamePassAPI] Failed to parse a pass for placeId {place_id}: {e}")
    except Exception as e:
        print(f"[GamePassAPI] Failed to parse HTML for placeId {place_id}: {e}")
    return gamepasses

# -----------------------------
# Fetch avec retry automatique
# -----------------------------
def fetch_gamepasses_with_retry(place_id, universe_id=None):
    attempt = 0
    
    while attempt < MAX_RETRIES:
        attempt += 1
        print(f"[GamePassAPI] Tentative {attempt}/{MAX_RETRIES} pour placeId {place_id}")
        
        # 1️⃣ Essayer le placeId
        html = fetch_html(place_id)
        passes = parse_gamepasses(html, place_id) if html else []

        # 2️⃣ Si vide et qu'on a un universeId, fallback avec rootPlaceId
        if not passes and universe_id:
            print(f"[GamePassAPI] Aucun gamepass trouvé, essai avec rootPlaceId de l'univers {universe_id}")
            root_place_id = get_root_place_id_from_universe(universe_id)
            if root_place_id and root_place_id != place_id:
                html = fetch_html(root_place_id)
                passes = parse_gamepasses(html, root_place_id) if html else []

        # ✅ Si on a trouvé des gamepasses, on retourne
        if passes:
            print(f"[GamePassAPI] ✅ {len(passes)} gamepass(es) trouvé(s) à la tentative {attempt}")
            return passes
        
        # ⚠️ Si aucun gamepass et qu'on n'a pas atteint le max de tentatives
        if attempt < MAX_RETRIES:
            print(f"[GamePassAPI] ⚠️ Aucun gamepass trouvé, nouvelle tentative dans {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
    
    # ❌ Après toutes les tentatives, on retourne une liste vide
    print(f"[GamePassAPI] ❌ Aucun gamepass trouvé après {MAX_RETRIES} tentatives pour placeId {place_id}")
    return []

# -----------------------------
# Route principale /gamepasses/<place_id>
# -----------------------------
@app.route("/gamepasses/<int:place_id>")
def get_gamepasses(place_id):
    # Optionnel : récupérer universeId depuis query params
    universe_id = None
    if "universeId" in dict(request.args):
        try:
            universe_id = int(request.args.get("universeId"))
        except:
            pass

    # Vérifier le cache
    if place_id in cache:
        ts, data = cache[place_id]
        if time.time() - ts < CACHE_DURATION:
            print(f"[GamePassAPI] 📦 Réponse depuis le cache pour placeId {place_id}")
            return Response(json.dumps(data, indent=2), mimetype="application/json")

    # Fetch avec retry automatique
    passes = fetch_gamepasses_with_retry(place_id, universe_id)

    # Sauvegarder dans le cache (même si vide, pour éviter de spammer l'API)
    cache[place_id] = (time.time(), passes)

    return Response(json.dumps(passes, indent=2), mimetype="application/json")

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
