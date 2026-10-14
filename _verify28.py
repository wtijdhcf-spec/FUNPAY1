# -*- coding: utf-8 -*-
import py_compile, re, threading, time, urllib.request, json as _json, io, os, tempfile
import webbrowser
webbrowser.open = lambda url: None

py_compile.compile("web_ui.py", doraise=True)
print("compile OK")
src = open("web_ui.py", encoding="utf-8").read()
methods = set(re.findall(r"^    def (_\w+)\(", src, re.M))
missing = sorted(c for c in set(re.findall(r"self\.(_\w+)\(", src)) if c not in methods)
assert not missing, missing
for s in ["_run_purge_task", "delete-inactive", "purgeInactive", "bulkAutoClean",
          "auto_clean", "_autoclean_node", "_registry_add", "deactivated.json",
          "Удалить выключенные", "Очищено выключенных"]:
    assert s in src, s
print("structure OK")
m = re.search(r"<script>(.*)</script>", src, re.S)
open("_inline.js", "w", encoding="utf-8").write(m.group(1))

import web_ui
from funpay_api import FunPayClient
H = web_ui.DashboardHTTPHandler
web_ui.FunPayClient.check_auth = lambda self: (True, 1, "T", None)
import time as _t
_t.sleep = lambda s: None

def make_stub(path, body=None):
    stub = H.__new__(H)
    raw = _json.dumps(body or {}).encode()
    stub.headers = {"Content-Length": str(len(raw))}
    stub.rfile = io.BytesIO(raw)
    stub.path = path
    stub._read_config = lambda: {"golden_key": "x"}
    stub.sent = {}
    stub._send_json = lambda data, status=200: stub.sent.update(data)
    return stub

# реестр: 1 выключенный (стёрт), 1 активный (не трогаем)
tmp = os.path.join(tempfile.gettempdir(), "_t_deact.json")
with open(tmp, "w", encoding="utf-8") as f:
    _json.dump([{"lot_id": "11", "node": "101", "title": "X", "type": "Услуги"},
                {"lot_id": "22", "node": "101", "title": "Y", "type": "Услуги"}], f)
import web_ui as W
_orig_reg, _orig_log = W._registry_load, W._log_deactivated_ids
W._registry_load = lambda: _json.load(open(tmp, encoding="utf-8"))
W._log_deactivated_ids = lambda: []
DETAILS = {"11": (False, "Услуги"), "22": (True, "Услуги")}
def fake_details(self, lid, is_chip=False):
    if lid not in DETAILS:
        return None
    act, tp = DETAILS[lid]
    fd = {"fields[type]": tp}
    if act:
        fd["active"] = "on"
    return {"form_data": fd, "action_url": "u"}
FunPayClient.get_lot_details = fake_details
deleted = []
FunPayClient.delete_lot = lambda self, lid, is_chip=False: (deleted.append(lid), (True, "gone"))[1]

s = make_stub("/api/lots/delete-inactive", {})
H.do_POST(s)
assert s.sent.get("started"), s.sent
deadline = time.time() + 20
while time.time() < deadline:
    with web_ui._BULK_LOCK:
        snap = dict(web_ui._DEL_TASK)
    if snap["finished"]:
        break
    time.sleep(0.2)
assert snap["finished"] and snap["deleted"] == 1 and deleted == ["11"], (snap, deleted)
assert "Удалено выключенных: 1" in snap["message"], snap["message"]
print("purge worker OK:", snap["message"])
W._registry_load, W._log_deactivated_ids = _orig_reg, _orig_log
os.remove(tmp)
print("ALL CHECKS PASSED")
