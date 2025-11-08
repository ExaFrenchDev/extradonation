import os
from flask import Flask, jsonify
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

DEFAULT_ICON = "https://tr.rbxcdn.com/180DAY-9babd76e0a0b581e7f689f06cac80194/150/150/Image/Webp/noFilter"

# -----------------------------
# Route test / ping
# -----------------------------
@app.route("/ping")
def ping():
    return jsonify({"status": "ok"})

# -----------------------------
# Route pour récupérer les gamepasses
# -----------------------------
@app.route("/gamepasses/<int:place_id>")
def get_gamepasses(place_id):
    urls = [
        f"https://www.roproxy.com/games/getgamepassesinnerpartial?startIndex=0&maxRows=50&placeId={place_id}",
        f"https://roblox.com.proxy.robloxapi.dev/games/getgamepassesinnerpartial?startIndex=0&maxRows=50&placeId={place_id}",
        f"https://games.roproxy.com/games/getgamepassesinnerpartial?startIndex=0&maxRows=50&placeId={place_id}"
    ]

    html = None
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    }

    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200 and "real-game-pass" in r.text:
                html = r.text
                break
        except Exception as e:
            print(f"[GamePassAPI] Failed to fetch {url}: {e}")

    if not html:
        return jsonify({"error": "failed_to_fetch", "gamepasses": [], "placeId": place_id}), 502

    # -----------------------------
    # Parser HTML
    # -----------------------------
    soup = BeautifulSoup(html, "html.parser")
    gamepasses = []

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
                "productId": 0,  # optionnel: tu peux récupérer via MarketplaceService si besoin
                "sellerId": 0,
                "status": "Buy"
            })
        except Exception as e:
            print(f"[GamePassAPI] Failed to parse a pass: {e}")

    return jsonify(gamepasses)

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
