from flask import Flask, jsonify, request
import requests
from bs4 import BeautifulSoup
import re
import time

app = Flask(__name__)

# User-Agent pour ressembler à un navigateur et éviter certains blocages
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

# Liste d'URL candidates (ordre d'essai)
PROXIES = [
    "https://www.roproxy.com/games/getgamepassesinnerpartial?startIndex=0&maxRows=50&placeId={placeId}",
    "https://roblox.com.proxy.robloxapi.dev/games/getgamepassesinnerpartial?startIndex=0&maxRows=50&placeId={placeId}",
    "https://games.roproxy.com/games/getgamepassesinnerpartial?startIndex=0&maxRows=50&placeId={placeId}",
    # tu peux ajouter d'autres proxys si tu en as un perso
]

# Timeout et retries pour requests
REQUEST_TIMEOUT = 8
MAX_RETRIES = 2
RETRY_DELAY = 0.5

def fetch_gamepasses_html(place_id):
    """
    Essaie plusieurs urls (proxies) et retourne le HTML si succès, sinon None.
    """
    for url_template in PROXIES:
        url = url_template.format(placeId=place_id)
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = requests.get(url, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT)
                if r.status_code == 200 and "real-game-pass" in r.text:
                    return r.text
                else:
                    # log court dans la console
                    app.logger.debug("Fetch failed (status %s) for %s", r.status_code, url)
            except Exception as e:
                app.logger.debug("Exception fetching %s: %s", url, e)
            time.sleep(RETRY_DELAY)
    return None

def parse_gamepasses_from_html(html):
    """
    Parse le HTML retourné par getgamepassesinnerpartial et renvoie une liste de dicts.
    """
    soup = BeautifulSoup(html, "html.parser")
    result = []

    # on cherche tous les <li class="list-item real-game-pass">
    items = soup.find_all("li", class_="list-item real-game-pass")
    for li in items:
        try:
            # passId via le <a href="/game-pass/<id>/...">
            a = li.find("a", href=True)
            pass_id = None
            if a:
                m = re.search(r"/game-pass/(\d+)", a["href"])
                if m:
                    pass_id = int(m.group(1))

            # name : titre dans la div store-card-name (title attr ou texte)
            name_div = li.select_one(".store-card-caption .store-card-name, .store-card-caption .text-overflow.store-card-name")
            name = None
            if name_div:
                # prefer title attribute if present
                name = name_div.get("title") or name_div.get_text(strip=True)

            # price : <span class="text-robux">123</span>
            price_span = li.select_one(".store-card-price .text-robux")
            price = None
            if price_span:
                price_text = price_span.get_text(strip=True).replace(",", "")
                if price_text.isdigit():
                    price = int(price_text)
                else:
                    # parfois il peut y avoir des espaces ou autres
                    price_digits = re.search(r"(\d+)", price_text)
                    if price_digits:
                        price = int(price_digits.group(1))

            # icon : src de l'image dans <a> ou <img>
            img = li.find("img")
            icon = img["src"] if img and img.get("src") else None

            # seller / product id / data attributes : bouton PurchaseButton
            purchase_btn = li.find(attrs={"class": re.compile(r"PurchaseButton|btn-buy-md")})
            seller_id = None
            product_id = None
            expected_price = None
            if purchase_btn:
                seller_id = purchase_btn.get("data-expected-seller-id")
                product_id = purchase_btn.get("data-product-id")
                expected_price = purchase_btn.get("data-expected-price")

                # cast to int si possible
                if seller_id and seller_id.isdigit():
                    seller_id = int(seller_id)
                if product_id and product_id.isdigit():
                    product_id = int(product_id)
                if expected_price and expected_price.isdigit():
                    expected_price = int(expected_price)

            # statut (Owned / Buy text)
            footer = li.select_one(".store-card-footer")
            status = None
            if footer:
                status_text = footer.get_text(strip=True)
                if status_text:
                    status = status_text

            # build object (norme : inclure uniquement champs valides)
            entry = {
                "passId": pass_id,
                "name": name or "",
                "price": price if price is not None else 0,
                "icon": icon or "",
                "sellerId": seller_id,
                "productId": product_id,
                "expectedPrice": expected_price,
                "status": status or ""
            }
            result.append(entry)
        except Exception as e:
            app.logger.debug("Error parsing li: %s", e)
            continue

    return result

@app.route("/gamepasses/<int:place_id>", methods=["GET"])
def get_gamepasses(place_id):
    """
    Endpoint principal. Retourne JSON list of gamepasses.
    Optional query param: ?as_lua=true to receive a small lua-table-like string (optionnel).
    """
    html = fetch_gamepasses_html(place_id)
    if not html:
        return jsonify({"error": "failed_to_fetch", "placeId": place_id, "gamepasses": []}), 502

    passes = parse_gamepasses_from_html(html)
    return jsonify(passes), 200

if __name__ == "__main__":
    # port 5000 par défaut, debug False pour prod
    app.run(host="0.0.0.0", port=5000, debug=False)
