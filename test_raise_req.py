import json
import re
from bs4 import BeautifulSoup
from funpay_api import FunPayClient

with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

client = FunPayClient(config.get("golden_key"))

# First GET the trade page to get fresh CSRF and button attributes
trade_url = "https://funpay.com/lots/402/trade"
resp = client.session.get(trade_url)
client._extract_csrf(resp.text)

soup = BeautifulSoup(resp.text, "html.parser")
raise_btn = soup.find(class_=re.compile(r"js-lot-raise"))

game_id = raise_btn.get("data-game") if raise_btn else "141"
node_id = raise_btn.get("data-node") if raise_btn else "402"

print(f"Raising game_id={game_id}, node_id={node_id}, csrf={client.csrf_token}")

post_url = "https://funpay.com/lots/raise"
headers = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": trade_url,
    "Origin": "https://funpay.com",
}
data = {
    "game_id": game_id,
    "node_id": node_id,
    "csrf_token": client.csrf_token,
}

raise_resp = client.session.post(post_url, data=data, headers=headers)
print("Status code:", raise_resp.status_code)
print("Response text:", raise_resp.text)
try:
    print("Response JSON:", raise_resp.json())
except Exception as e:
    pass
