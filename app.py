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
from threading import Thread

app = Flask(__name__)

DEFAULT_ICON = "https://tr.rbxcdn.com/180DAY-9babd76e0a0b581e7f689f06cac80194/150/150/Image/Webp/noFilter"

# -----------------------------
# Cache interne
# -----------------------------
CACHE_DURATION = 10 * 60  # 10 minutes
cache = {}  # placeId -> (timestamp, data)

# -----------------------------
# Keep-Alive Configuration
# -----------------------------
KEEP_ALIVE_INTERVAL = 5 * 60  # Ping toutes les 5 minutes
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080")  # URL de ton hébergeur

def keep_alive():
    """Thread qui envoie des requêtes ping régulières pour garder le serveur actif"""
    while True:
        try:
            time.sleep(KEEP_ALIVE_INTERVAL)
            # Envoie une requête ping à soi-même
            requests.get(f"{BASE_URL}/ping", timeout=5)
            print(f"[Keep-Alive] Ping envoyé à {time.strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"[Keep-Alive] Erreur lors du ping: {e}")

# -----------------------------
# Route test / ping
# -----------------------------
@app.route("/ping")
def ping():
    return jsonify({"status": "ok", "timestamp": time.time()})

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
# Fallback : essaye rootPlaceId si aucun gamepass trouvé
# -----------------------------
def fetch_gamepasses(place_id, universe_id=None):
    # 1️⃣ Essayer le placeId
    html = fetch_html(place_id)
    passes = parse_gamepasses(html, place_id) if html else []

    # 2️⃣ Si vide et qu'on a un universeId, fallback avec rootPlaceId
    if not passes and universe_id:
        root_place_id = get_root_place_id_from_universe(universe_id)
        if root_place_id:
            html = fetch_html(root_place_id)
            passes = parse_gamepasses(html, root_place_id) if html else []

    return passes

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
            return Response(json.dumps(data, indent=2), mimetype="application/json")

    passes = fetch_gamepasses(place_id, universe_id)

    # Sauvegarder dans le cache
    cache[place_id] = (time.time(), passes)

    return Response(json.dumps(passes, indent=2), mimetype="application/json")

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    
    # Démarre le thread de keep-alive en arrière-plan
    keep_alive_thread = Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    print("[Keep-Alive] Thread démarré")
    
    app.run(host="0.0.0.0", port=port)
