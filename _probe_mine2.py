# -*- coding: utf-8 -*-
import re
from bs4 import BeautifulSoup
html = open("_trade4386.html", encoding="utf-8").read()
soup = BeautifulSoup(html, "html.parser")
# все вхождения offer
for m in sorted(set(re.findall(r"[A-Za-z]*offer[A-Za-z]*", html)))[:20]:
    print("offer-word:", m)
print("----")
for m in sorted(set(re.findall(r'href="([^"]*(?:offer|Offer)[^"]*)"', html)))[:20]:
    print("HREF:", m[:150])
print("----rows----")
for cls in ["offer", "lot", "my-offer", "table", "row"]:
    els = soup.find_all(class_=re.compile(cls))
    print(cls, len(els))
txt = soup.get_text(" ", strip=True)
i = txt.find("Ваши предложения")
print(txt[i:i+800] if i >= 0 else "NO HEADER")
