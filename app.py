import os
import time
from flask import Flask, Response, jsonify
import requests
from bs4 import BeautifulSoup
import json

app = Flask(__name__)

DEFAULT_ICON = "https://tr.rbxcdn.com/180DAY-9babd76e0a0b581e7f689f06cac80194/150/150/Image/Webp/noFilter"

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
# Convertit un universeId en rootPlaceId si nécessaire
# -----------------------------
def universe_to_place(universe_id):
    try:
        r = requests.get(f"https://games.roproxy.com/v1/games?universeIds={universe_id}", timeout=5)
        if r.status_code == 200:
            data = r.json()
            if "data" in data and len(data["data"]) > 0:
                return data["data"][0]["rootPlaceId"]
    except Exception as e:
        print(f"[GamePassAPI] Failed to convert universeId {universe_id}: {e}")
    return universe_id  # fallback

# -----------------------------
# Récupère HTML depuis roproxy
# -----------------------------
def fetch_html(place_id, timeout=10):
    url = f"https://www.roproxy.com/games/getgamepassesinnerpartial?startIndex=0&maxRows=50&placeId={place_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200 and "real-game-pass" in r.text:
            return r.text
        else:
            print(f"[GamePassAPI] Unexpected status or no game passes at {url} (status {r.status_code})")
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
# Route principale /gamepasses/<place_id>
# -----------------------------
@app.route("/gamepasses/<int:place_id>")
def get_gamepasses(place_id):
    # Conversion automatique si un universeId est fourni
    real_place_id = universe_to_place(place_id)

    # Vérifier le cache
    if real_place_id in cache:
        ts, data = cache[real_place_id]
        if time.time() - ts < CACHE_DURATION:
            return Response(json.dumps(data, indent=2), mimetype="application/json")

    html = fetch_html(real_place_id)

    if not html:
        result = {
            "error": "failed_to_fetch",
            "message": f"No valid HTML received from roproxy for placeId {real_place_id}",
            "gamepasses": [],
            "placeId": real_place_id
        }
        return Response(json.dumps(result, indent=2), mimetype="application/json")

    gamepasses = parse_gamepasses(html, real_place_id)

    # Sauvegarder dans le cache
    cache[real_place_id] = (time.time(), gamepasses)

    return Response(json.dumps(gamepasses, indent=2), mimetype="application/json")

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
