"""Mafiya Mini App serveri — 40 rol. FastAPI + WebSocket + aiogram (webhook).
Env: BOT_TOKEN, BASE_URL, APP_NAME, MIN_PLAYERS
Start: uvicorn server:app --host 0.0.0.0 --port $PORT"""
import asyncio, hashlib, hmac, json, os, random, sqlite3, time
from collections import Counter
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery, Update

TOKEN, BASE = os.environ["BOT_TOKEN"], os.environ["BASE_URL"]
APP, MIN = os.getenv("APP_NAME", "mafia"), int(os.getenv("MIN_PLAYERS", "4"))
T = {"night": 35, "day": 45, "vote": 25}
bot, dp, ME = Bot(TOKEN), Dispatcher(), ""
rooms: dict = {}
ADMIN = int(os.getenv("ADMIN_ID", "0"))

# Stars do'koni: kalit: (nom, tavsif, narx ⭐). Narsa keyingi o'yin boshida avtomatik ishlatiladi.
ITEMS = {"shield": ("🛡 Qalqon", "O'yin boshida zirh: birinchi hujumdan omon qolasiz", 15),
         "vote2": ("🗳 Qo'sh ovoz", "Bir o'yin davomida ovozingiz 2 ta hisoblanadi", 20)}
# DIQQAT: Render bepul tarifida fayl o'chib ketadi. Haqiqiy to'lov uchun doimiy disk yoki tashqi baza kerak.
db = sqlite3.connect("mafia.db", check_same_thread=False)
db.executescript("CREATE TABLE IF NOT EXISTS inv(uid INTEGER PRIMARY KEY, shield INT DEFAULT 0, vote2 INT DEFAULT 0);"
                 "CREATE TABLE IF NOT EXISTS orders(uid INT, item TEXT, stars INT, charge TEXT);")


def inv(uid):
    db.execute("INSERT OR IGNORE INTO inv(uid) VALUES(?)", (uid,))
    s, v = db.execute("SELECT shield,vote2 FROM inv WHERE uid=?", (uid,)).fetchone()
    return {"shield": s, "vote2": v}


def use(uid, item):  # item faqat ITEMS kalitlaridan
    cur = db.execute(f"UPDATE inv SET {item}={item}-1 WHERE uid=? AND {item}>0", (uid,)); db.commit()
    return cur.rowcount > 0

# kalit: (emoji, nom, jamoa c/m/s, tungi harakat, necha marta, belgilar)
# belgilar: v zirh, 2 qo'sh ovoz, r qasos, l omad, i osishdan himoya, d niqob,
#           K yolg'iz qotil, J hazilkash, X jallod, S oxirigacha tirik qolsa yutadi
R = {
 "civil": ("👤", "Tinch aholi", "c", None, None, ""), "doctor": ("💉", "Doktor", "c", "heal", None, ""),
 "sheriff": ("🕵️", "Komissar", "c", "check", None, ""), "detective": ("🧐", "Detektiv", "c", "team", None, ""),
 "seer": ("🔮", "Folbin", "c", "role", None, ""), "bodyguard": ("🛡", "Qo'riqchi", "c", "guard", None, ""),
 "watcher": ("👁", "Kuzatuvchi", "c", "watch", None, ""), "tracker": ("🐾", "Izquvar", "c", "track", None, ""),
 "escort": ("💃", "Kezuvchi", "c", "block", None, ""), "nurse": ("🩺", "Hamshira", "c", "heal2", None, ""),
 "sniper": ("🎯", "Snayper", "c", "shoot", 1, ""), "reviver": ("🕯", "Tiriltiruvchi", "c", "revive", 1, ""),
 "judge": ("⚖️", "Sudya", "c", "mute", None, ""), "mayor": ("🎩", "Mer", "c", None, None, "2"),
 "vest": ("🦺", "Zirhli", "c", None, None, "v"), "hunter": ("🏹", "Ovchi", "c", None, None, "r"),
 "lucky": ("🍀", "Omadli", "c", None, None, "l"), "privileged": ("🛂", "Imtiyozli", "c", None, None, "i"),
 "mafia": ("😈", "Mafiya", "m", "mkill", None, ""), "godfather": ("🕴", "Cho'qintirgan ota", "m", "mkill", None, "d"),
 "framer": ("🎭", "Aldamchi", "m", "frame", None, ""), "consort": ("💋", "Kurtizan", "m", "block", None, ""),
 "poisoner": ("☠️", "Zaharchi", "m", "poison", None, ""), "hitman": ("🗡", "Yollanma", "m", "shoot", 1, ""),
 "chemist": ("🧪", "Kimyogar", "m", "heal", None, ""), "dog": ("🐕", "Mafiya iti", "m", "guard", None, ""),
 "spy": ("🕶", "Josus", "m", "role", None, ""), "silencer": ("🤐", "Tilsiz", "m", "mute", None, ""),
 "bomber": ("💣", "Bombachi", "m", None, None, "r"),
 "maniac": ("🔪", "Manyak", "s", "kill", None, "K"), "wolf": ("🐺", "Yolg'iz bo'ri", "s", "kill", None, "Kv"),
 "jester": ("🤡", "Hazilkash", "s", None, None, "J"), "executioner": ("🪓", "Jallod", "s", None, None, "X"),
 "survivor": ("🧍", "Omon qoluvchi", "s", None, None, "Sv"), "tourist": ("🧳", "Sayyoh", "s", "watch", None, "S"),
 "oracle": ("🃏", "Folchi", "s", "role", None, "S"), "medic": ("🧑‍⚕️", "Yolg'iz shifokor", "s", "heal", None, "S"),
 "merchant": ("🧺", "Savdogar", "s", "block", None, "S"), "pirate": ("🏴‍☠️", "Qaroqchi", "s", "shoot", 1, "S"),
 "campaigner": ("🗳", "Targ'ibotchi", "s", None, None, "2S"),
}


def auth(init: str):  # Telegram initData imzosini tekshirish
    d = dict(parse_qsl(init)); h = d.pop("hash", "")
    s = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    k = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    ok = hmac.compare_digest(hmac.new(k, s.encode(), hashlib.sha256).hexdigest(), h)
    return json.loads(d["user"]) if ok and time.time() - int(d.get("auth_date", 0)) < 86400 else None


def newp(name): return {"name": name, "role": "civil", "team": "c", "fl": "", "uses": None, "alive": True}


class Room:
    def __init__(r):
        r.p, r.ws, r.host, r.task, r.ev = {}, {}, None, None, asyncio.Event(); r.need = set(); r.reset()

    def reset(r):
        r.phase, r.day, r.win, r.wid, r.news, r.until = "lobby", 0, None, None, None, time.time()
        r.priv, r.acts, r.acted, r.votes, r.poison, r.muted = {}, {}, set(), {}, set(), set()
        for u in list(r.p):
            if u in r.ws: r.p[u] = newp(r.p[u]["name"])
            else: del r.p[u]


def wt(r, u): return 2 if "2" in r.p[u]["fl"] else 1


def won(r, u):
    p, w = r.p[u], r.win
    return bool(("S" in p["fl"] and p["alive"]) or (w == "civil" and p["team"] == "c") or (w == "mafia" and p["team"] == "m")
                or (w == "killer" and "K" in p["fl"] and p["alive"]) or (w in ("jester", "exec") and u == r.wid))


async def push(r):
    end = r.phase == "end"
    for uid, ws in list(r.ws.items()):
        me, pl = r.p.get(uid), []
        for i, p in r.p.items():
            show = end or not p["alive"] or i == uid or (me and me["team"] == "m" and p["team"] == "m")
            pl.append({"id": i, "name": p["name"], "alive": p["alive"], "role": p["role"] if show else None,
                       "votes": sum(wt(r, v) for v, t in r.votes.items() if t == i), "w": end and won(r, i)})
        act = me and me["alive"] and me["uses"] != 0 and R[me["role"]][3] or None
        try:
            await ws.send_json({"phase": r.phase, "left": max(0, int(r.until - time.time())), "host": r.host, "me": uid,
                "role": me and me["role"], "act": act, "uses": me and me["uses"], "players": pl, "news": r.news,
                "win": r.win, "min": MIN, "info": r.priv.get(uid, []), "muted": uid in r.muted, "acted": uid in r.acted,
                "day": r.day, "inv": inv(uid) if r.phase == "lobby" else None, "tgt": me and me.get("tgt") and r.p[me["tgt"]]["name"]})
        except Exception:
            r.ws.pop(uid, None)


async def wait(r, sec):
    r.until, r.ev = time.time() + sec, asyncio.Event()
    await push(r)
    try: await asyncio.wait_for(r.ev.wait(), sec)
    except asyncio.TimeoutError: pass


def winner(r):
    a = [p for p in r.p.values() if p["alive"]]
    m, k = sum(p["team"] == "m" for p in a), sum("K" in p["fl"] for p in a)
    o = len(a) - m - k
    if m == 0 and k == 0: return "civil"
    if k == 0 and m >= o: return "mafia"
    if m == 0 and o <= k: return "killer"
    if m and k and o == 0: return "mafia" if m > k else "killer"


def deal(r):
    ids = list(r.p); random.shuffle(ids); n = len(ids)
    nm, ns = max(1, n // 4), n // 6
    nc = n - nm - ns
    by = lambda t, ex=(): [k for k, v in R.items() if v[2] == t and k not in ex]
    M = ["mafia"] + random.sample(by("m", ("mafia",)), min(nm - 1, 10)); M += ["mafia"] * (nm - len(M))
    S = random.sample(by("s"), ns)
    C0 = by("c", ("civil", "doctor", "sheriff")); random.shuffle(C0)
    C = (["doctor", "sheriff"] + C0)[:nc]; C += ["civil"] * (nc - len(C))
    roles = M + S + C; random.shuffle(roles)
    for u, k in zip(ids, roles):
        e = R[k]; p = r.p[u]
        p.update(role=k, team=e[2], fl=e[5], uses=e[4], vest=int("v" in e[5]), lv=int("i" in e[5]))
        if use(u, "shield"): p["vest"] += 1
        if "2" not in p["fl"] and use(u, "vote2"): p["fl"] += "2"
    civ = [u for u in ids if r.p[u]["team"] == "c"]
    for u in ids:
        if r.p[u]["role"] == "executioner" and civ: r.p[u]["tgt"] = random.choice(civ)
        elif r.p[u]["role"] == "executioner": r.p[u]["fl"] = "J"


def resolve(r):
    P, inf, nm = r.p, (lambda u, s: r.priv.setdefault(u, []).append(s)), (lambda u: r.p[u]["name"])
    act = lambda u: R[P[u]["role"]][3]
    A = {u: t for u, t in r.acts.items() if u in P and P[u]["alive"]}
    for u, t in A.items():
        if act(u) == "block": inf(t, "🚫 Bu tun sizning harakatingiz to'sildi!")
    B = {t for u, t in A.items() if act(u) == "block"}
    A = {u: t for u, t in A.items() if u not in B}
    for u in A:
        if P[u]["uses"] is not None: P[u]["uses"] -= 1
    H = {t for u, t in A.items() if act(u) in ("heal", "heal2")}
    G = {t: u for u, t in A.items() if act(u) == "guard"}
    F = {t for u, t in A.items() if act(u) == "frame"}
    att, dead, rev = [], [], []
    c = Counter(t for u, t in A.items() if act(u) == "mkill")
    if c:
        top = max(c.values()); att.append(random.choice([t for t, n in c.items() if n == top]))
    att += [t for u, t in A.items() if act(u) in ("kill", "shoot")]

    def die(u):
        if P[u]["alive"]: P[u]["alive"] = False; dead.append(u)
    for t in r.poison:
        if P[t]["alive"]:
            if t in H: inf(t, "🧪 Zahar ta'sir qilmadi: davolandingiz!")
            else: die(t)
    for t in att:
        if not P[t]["alive"]: continue
        if t in G and P[G[t]]["alive"]:
            die(G[t]); inf(G[t], "🛡 Himoya qilgan odamingiz uchun jon berdingiz."); inf(t, "🛡 Kimdir sizni himoya qildi!")
        elif t in H: inf(t, "💉 Sizga hujum bo'ldi, lekin davolandingiz!")
        elif P[t].get("vest"): P[t]["vest"] -= 1; inf(t, "🦺 Zirh sizni asradi!")
        elif "l" in P[t]["fl"] and random.random() < .5: inf(t, "🍀 Omad kuldi: hujumdan omon qoldingiz!")
        else: die(t)
    r.poison = {t for u, t in A.items() if act(u) == "poison"}
    for t in r.poison: inf(t, "☠️ Siz zaharlandingiz! Keyingi tunda o'lasiz (davolanmasangiz).")
    V = {}
    for u, t in A.items(): V.setdefault(t, []).append(u)
    for u, t in A.items():
        a, X = act(u), P[t]
        if a == "check": inf(u, f"🕵️ {nm(t)}: " + ("🔴 MAFIYA" if (X["team"] == "m" and "d" not in X["fl"]) or t in F else "🟢 mafiya emas"))
        elif a == "team": inf(u, f"🧐 {nm(t)} jamoasi: " + {"c": "Aholi", "m": "Mafiya", "s": "Yolg'iz"}["m" if t in F else "c" if "d" in X["fl"] else X["team"]])
        elif a == "role": inf(u, f"🔮 {nm(t)} — {R[X['role']][0]} {R[X['role']][1]}")
        elif a == "watch": inf(u, f"👁 {nm(t)} ga kelganlar: " + (", ".join(nm(x) for x in V[t] if x != u) or "hech kim"))
        elif a == "track": inf(u, f"🐾 {nm(t)} tunda " + (nm(A[t]) + " ga bordi" if t in A else "uyda qoldi"))
        elif a == "heal2" and t in att: inf(u, f"🩺 {nm(t)} ga hujum bo'lgan!")
        elif a == "mute": r.muted.add(t); inf(t, "🤐 Bugun ovoz bera olmaysiz!")
        elif a == "revive" and not X["alive"]: X["alive"] = True; rev.append(t); inf(t, "🕯 Siz tirildingiz!")
    for p in P.values():
        if "X" in p["fl"] and p.get("tgt") in dead: p["fl"] = p["fl"].replace("X", "J")
    return {"kind": "night", "dead": dead, "rev": rev}


def lynch(r):
    P, c, dead, imm = r.p, Counter(), [], False
    for v, t in r.votes.items():
        if t: c[t] += wt(r, v)
    top = c.most_common(2)
    if top and (len(top) == 1 or top[0][1] > top[1][1]):
        u = top[0][0]; p = P[u]
        if p.get("lv"): p["lv"] = 0; imm = True
        else:
            p["alive"] = False; dead.append(u)
            cand = [v for v, t in r.votes.items() if t == u and P[v]["alive"]]
            if "r" in p["fl"] and cand: x = random.choice(cand); P[x]["alive"] = False; dead.append(x)
            if "J" in p["fl"]: r.win, r.wid = "jester", u
            for q, e in P.items():
                if "X" in e["fl"] and e.get("tgt") == u and e["alive"]: r.win, r.wid = "exec", q
    r.news = {"kind": "vote", "dead": dead, "rev": [], "imm": imm}; r.muted = set()


async def run(r):
    deal(r); r.phase = "roles"; await wait(r, 7)
    while True:
        r.day += 1; r.phase = "night"; r.acts, r.acted, r.priv, r.votes = {}, set(), {}, {}
        r.need = {u for u, p in r.p.items() if p["alive"] and R[p["role"]][3] and p["uses"] != 0}
        await wait(r, T["night"])
        r.news = resolve(r); r.win = winner(r)
        if r.win: break
        r.phase = "day"; await wait(r, T["day"])
        r.phase, r.votes = "vote", {}; await wait(r, T["vote"])
        lynch(r); r.win = r.win or winner(r)
        if r.win: break
        await push(r); await asyncio.sleep(3)
    r.phase = "end"; await push(r)


async def handle(r, uid, m):
    t, to, me = m.get("t"), m.get("to"), r.p.get(uid)
    if t == "start" and r.phase == "lobby" and uid == r.host and len(r.p) >= MIN:
        r.task = asyncio.create_task(run(r))
    elif t == "again" and r.phase == "end" and uid == r.host:
        r.reset(); await push(r)
    elif t == "act" and r.phase == "night" and me and me["alive"] and uid not in r.acted:
        a, tg = R[me["role"]][3], r.p.get(to)
        if not a or me["uses"] == 0 or not tg: return
        if (a == "revive") == tg["alive"] or (to == uid and a not in ("heal", "heal2")) or (a == "mkill" and tg["team"] == "m"): return
        r.acts[uid] = to; r.acted.add(uid)
        if r.need <= r.acted: r.ev.set()
        await push(r)
    elif t == "vote" and r.phase == "vote" and me and me["alive"] and uid not in r.muted and (to == 0 or r.p.get(to, {}).get("alive")):
        r.votes[uid] = to
        if len(r.votes) >= sum(p["alive"] for u, p in r.p.items() if u not in r.muted): r.ev.set()
        await push(r)


@asynccontextmanager
async def life(app):
    global ME
    ME = (await bot.get_me()).username
    await bot.set_webhook(BASE + "/webhook")
    yield

app = FastAPI(lifespan=life)


@dp.message(Command("start", "newgame"))
async def newgame(m: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="🎭 O'yinni ochish", url=f"https://t.me/{ME}/{APP}?startapp={m.chat.id}")]])
    await m.answer("🎭 Mafiya! Tugmani bosing va o'yinga qo'shiling.", reply_markup=kb)


@app.post("/webhook")
async def hook(req: Request):
    await dp.feed_update(bot, Update.model_validate(await req.json(), context={"bot": bot}))
    return {}


@app.get("/ping")
def ping(): return "ok"


@app.get("/roles")
def roles(): return R


@app.websocket("/ws")
async def ws_ep(ws: WebSocket, init: str, room: str):
    await ws.accept()
    u = auth(init)
    if not u: return await ws.close()
    r, uid = rooms.setdefault(room, Room()), u["id"]
    if uid not in r.p and r.phase == "lobby" and len(r.p) < 40:
        r.p[uid] = newp(u.get("first_name", "?")[:14]); r.host = r.host if r.host in r.p else uid
    r.ws[uid] = ws; await push(r)
    try:
        while True: await handle(r, uid, await ws.receive_json())
    except WebSocketDisconnect:
        r.ws.pop(uid, None)
        if r.phase == "lobby":
            r.p.pop(uid, None); r.host = next(iter(r.p), None)
        if not r.ws:
            if r.task: r.task.cancel()
            rooms.pop(room, None)
        else: await push(r)

@app.post("/invoice")
async def invoice(req: Request):
    d = await req.json(); u = auth(d.get("init", "")); it = ITEMS.get(d.get("item"))
    if not u or not it: return {"error": "bad"}
    link = await bot.create_invoice_link(title=it[0], description=it[1], payload=f"{d['item']}:{u['id']}",
                                         provider_token="", currency="XTR", prices=[LabeledPrice(label=it[0], amount=it[2])])
    return {"link": link}


@dp.pre_checkout_query()
async def pre(q: PreCheckoutQuery): await q.answer(ok=True)


@dp.message(F.successful_payment)
async def paid(m: Message):
    sp = m.successful_payment; item, uid = sp.invoice_payload.split(":"); uid = int(uid)
    if item not in ITEMS or uid != m.from_user.id: return
    inv(uid); db.execute(f"UPDATE inv SET {item}={item}+1 WHERE uid=?", (uid,))
    db.execute("INSERT INTO orders VALUES(?,?,?,?)", (uid, item, sp.total_amount, sp.telegram_payment_charge_id)); db.commit()
    await m.answer(f"✅ {ITEMS[item][0]} sotib olindi! Keyingi o'yin boshida avtomatik ishlaydi.")
    if ADMIN: await bot.send_message(ADMIN, f"💰 {uid}: {item} ⭐{sp.total_amount}\nCharge: {sp.telegram_payment_charge_id}")
    for r in rooms.values():
        if uid in r.ws: await push(r)


@dp.message(Command("refund"))
async def refund(m: Message):  # admin: /refund <user_id> <charge_id>
    if m.from_user.id != ADMIN: return
    try:
        _, uid, ch = m.text.split()
        await bot.refund_star_payment(user_id=int(uid), telegram_payment_charge_id=ch)
        row = db.execute("SELECT item FROM orders WHERE charge=?", (ch,)).fetchone()
        if row: use(int(uid), row[0])
        await m.answer("✅ Stars qaytarildi")
    except Exception as e: await m.answer(f"Xato: {e}\n/refund <user_id> <charge_id>")


app.mount("/", StaticFiles(directory="static", html=True))
