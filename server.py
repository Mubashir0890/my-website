"""Birga Chizamiz: ikki kishilik umumiy rasm (Telegram Mini App).
Ishga tushirish: uvicorn server:app --host 0.0.0.0 --port 8000
"""
import os, hmac, hashlib, json, time, base64, re
from urllib.parse import parse_qsl
import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

BOT_TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ["WEBAPP_URL"]
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
APP_NAME = os.environ.get("APP_NAME", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "o'zgartiring")
API = f"https://api.telegram.org/bot{BOT_TOKEN}"
ROOM_RE = re.compile(r"^[a-z0-9]{8,24}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{3,8}$")
MAX_POINTS = 8000            # bitta chiziq uchun chegara
rooms = {}                   # xona_id -> holat (xotirada saqlanadi)

def auth(init_data: str):
    data = dict(parse_qsl(init_data, keep_blank_values=True))
    got = data.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got) \
            or time.time() - int(data.get("auth_date", 0)) > 86400:
        raise HTTPException(401, "Avtorizatsiya xatosi")
    u = json.loads(data["user"])
    return u["id"], u.get("first_name", "Do'st")

def users_list(room):
    return [{"id": k, **v} for k, v in room["users"].items()]

async def bcast(room, msg, skip=None):
    for ws in list(room["clients"]):
        if ws is skip: continue
        try: await ws.send_json(msg)
        except Exception: room["clients"].pop(ws, None)

app = FastAPI()

@app.websocket("/ws/{room_id}")
async def ws_room(sock: WebSocket, room_id: str, init: str = ""):
    await sock.accept()
    try:
        uid, name = auth(init)
    except HTTPException:
        await sock.close(4401); return
    if not ROOM_RE.match(room_id):
        await sock.close(4400); return
    room = rooms.setdefault(room_id, {"strokes": [], "idx": {}, "clients": {}, "users": {}, "theme": ""})
    if uid not in room["users"]:
        if len(room["users"]) >= 2:
            await sock.send_json({"t": "full"}); await sock.close(); return
        room["users"][uid] = {"name": name, "slot": len(room["users"])}
    room["clients"][sock] = uid
    link = f"https://t.me/{BOT_USERNAME}/{APP_NAME}?startapp=room_{room_id}" if BOT_USERNAME and APP_NAME else ""
    await sock.send_json({"t": "hello", "you": uid, "strokes": room["strokes"], "theme": room["theme"],
                          "users": users_list(room), "link": link})
    await bcast(room, {"t": "users", "users": users_list(room)}, skip=sock)
    try:
        while True:
            m = await sock.receive_json()
            t = m.get("t")
            if t == "seg":
                sid = str(m.get("id", ""))[:16]
                pts = [[float(x), float(y)] for x, y in m.get("p", [])[:200]]
                st = room["idx"].get(sid)
                if not st:
                    c = str(m.get("c", "#16263d"))
                    st = {"id": sid, "u": uid, "c": c if COLOR_RE.match(c) else "#16263d",
                          "w": max(1, min(float(m.get("w", 6)), 40)), "p": []}
                    room["idx"][sid] = st; room["strokes"].append(st)
                if st["u"] != uid or len(st["p"]) > MAX_POINTS: continue
                st["p"] += pts
                await bcast(room, {"t": "seg", "id": sid, "c": st["c"], "w": st["w"], "p": pts}, skip=sock)
            elif t == "undo":
                mine = [s for s in room["strokes"] if s["u"] == uid]
                if mine:
                    room["strokes"].remove(mine[-1]); room["idx"].pop(mine[-1]["id"], None)
                    await bcast(room, {"t": "undo", "id": mine[-1]["id"]})
            elif t == "clear":
                room["strokes"].clear(); room["idx"].clear()
                await bcast(room, {"t": "clear"})
            elif t == "theme":
                room["theme"] = str(m.get("v", ""))[:60]
                await bcast(room, {"t": "theme", "v": room["theme"]})
    except WebSocketDisconnect:
        pass
    finally:
        room["clients"].pop(sock, None)

class Img(BaseModel):
    room: str
    image: str

@app.post("/api/send")
async def send(b: Img, x_init_data: str = Header("")):
    uid, _ = auth(x_init_data)
    room = rooms.get(b.room)
    if not room or uid not in room["users"]: raise HTTPException(403, "Xona topilmadi")
    raw = base64.b64decode(b.image.split(",")[-1])
    if len(raw) > 5_000_000: raise HTTPException(413, "Rasm juda katta")
    caption = "🎨 " + (room["theme"] or "Bizning rasmimiz")
    async with httpx.AsyncClient(timeout=30) as c:
        for u in room["users"]:
            await c.post(f"{API}/sendPhoto", data={"chat_id": u, "caption": caption},
                         files={"photo": ("rasm.png", raw, "image/png")})
    return {"ok": True}

@app.post("/webhook/{secret}")
async def webhook(secret: str, req: Request):
    if secret != WEBHOOK_SECRET: raise HTTPException(403)
    msg = (await req.json()).get("message") or {}
    if msg.get("text", "").startswith("/start"):
        async with httpx.AsyncClient() as c:
            await c.post(f"{API}/sendMessage", json={
                "chat_id": msg["chat"]["id"], "text": "🎨 Birga Chizamiz: sevgan insoningiz bilan bitta rasmda ijod qiling!",
                "reply_markup": {"inline_keyboard": [[{"text": "Chizishni boshlash", "web_app": {"url": WEBAPP_URL}}]]}})
    return {"ok": True}

@app.get("/")
def index(): return FileResponse("static/index.html")
app.mount("/static", StaticFiles(directory="static"), name="static")
