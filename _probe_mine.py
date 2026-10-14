# -*- coding: utf-8 -*-
# READ-ONLY: страница "Ваши предложения" раздела — видны ли выключенные лоты
import json, re
from funpay_api import FunPayClient
from bs4 import BeautifulSoup

cfg = json.load(open("config.json", encoding="utf-8"))
client = FunPayClient(golden_key=cfg.get("golden_key", ""))
r = client.session.get("https://funpay.com/lots/4386/trade", timeout=25)
print("status", r.status_code, "len", len(r.text))
open("_trade4386.html", "w", encoding="utf-8").write(r.text)
soup = BeautifulSoup(r.text, "html.parser")
print("TITLE:", soup.title.get_text(strip=True) if soup.title else None)
txt = soup.get_text(" ", strip=True)
for marker in ["Ваши предложения", "Много предложений", "удалите ненужные", "Включить лоты"]:
    if marker in txt:
        i = txt.find(marker)
        print("MARKER:", txt[max(0, i-80):i+120].replace("\n", " "))
# ссылки на редактирование своих лотов
links = {}
for a in soup.find_all("a", href=re.compile(r"offerEdit\?offer=\d+")):
    m = re.search(r"offer=(\d+)", a["href"])
    if m:
        links[m.group(1)] = a.get_text(" ", strip=True)[:60]
print("my lots found:", len(links))
for lid, t in list(links.items())[:10]:
    print("  #", lid, t)
