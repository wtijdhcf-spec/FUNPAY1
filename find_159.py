import json
from funpay_api import FunPayClient

with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

client = FunPayClient(config.get("golden_key"))
offers = client.get_category_offers("https://funpay.com/lots/402/")

with open("find_159.txt", "w", encoding="utf-8") as out:
    out.write(f"Total offers: {len(offers)}\n\n")
    for o in offers:
        if abs(o.price - 0.159) < 0.005 or "159" in str(o.price) or (0.15 <= o.price <= 0.17):
            out.write(f"Seller: {o.seller_name} (ID: {o.seller_id}, Online: {o.is_online}) | Price: {o.price} | Title: {o.title} | Subcat: {o.subcategory}\n")
