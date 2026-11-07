"""Mafiya Mini App serveri — 40 rol. FastAPI + WebSocket + aiogram (webhook).
Env: BOT_TOKEN, BASE_URL, APP_NAME, MIN_PLAYERS
Start: uvicorn server:app --host 0.0.0.0 --port $PORT"""
import asyncio, hashlib, hmac, html, json, os, random, sqlite3, time
from collections import Counter
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery, Update

TOKEN, BASE = os.environ["BOT_TOKEN"], os.environ["BASE_URL"]
APP, MIN = os.getenv("APP_NAME", "mafia"), int(os.getenv("MIN_PLAYERS", "4"))
T = {"night": 35, "day": 45, "vote": 25}
bot, dp, ME = Bot(TOKEN), Dispatcher(), ""
rooms: dict = {}
ADMIN = int(os.getenv("ADMIN_ID", "0"))

# 💎 Olmos do'koni. ITEMS: kalit: (nom, tavsif, narx 💎). PACKS: kalit: (olmos soni, narx ⭐).
ITEMS = {"shield": ("🛡 Qalqon", "O'yin boshida zirh: birinchi hujumdan omon qolasiz", 5),
         "vote2": ("🗳 Qo'sh ovoz", "Bir o'yin davomida ovozingiz 2 ta hisoblanadi", 7),
         "luck": ("🍀 Omad", "Bir o'yinda hujumdan 50% omon qolasiz", 4)}
PACKS = {"d10": (10, 25), "d50": (50, 110), "d120": (120, 230)}
OWNER = "mubashirbek"  # karta orqali to'lov uchun havola: t.me/mubashirbek
# DIQQAT: Render bepul tarifida fayl o'chib ketadi. Haqiqiy to'lov uchun doimiy disk yoki tashqi baza kerak.
db = sqlite3.connect("mafia.db", check_same_thread=False)
db.executescript("CREATE TABLE IF NOT EXISTS inv(uid INTEGER PRIMARY KEY, shield INT DEFAULT 0, vote2 INT DEFAULT 0);"
                 "CREATE TABLE IF NOT EXISTS orders(uid INT, item TEXT, stars INT, charge TEXT);"
                 "CREATE TABLE IF NOT EXISTS st(uid INTEGER PRIMARY KEY, name TEXT, games INT DEFAULT 0, wins INT DEFAULT 0, coins INT DEFAULT 0);")


for c in ("luck", "dia"):  # eski bazani yangilash
    try: db.execute(f"ALTER TABLE inv ADD COLUMN {c} INT DEFAULT 0")
    except sqlite3.OperationalError: pass
db.commit()


def inv(uid):
    db.execute("INSERT OR IGNORE INTO inv(uid) VALUES(?)", (uid,))
    s, v, l, d = db.execute("SELECT shield,vote2,luck,dia FROM inv WHERE uid=?", (uid,)).fetchone()
    return {"shield": s, "vote2": v, "luck": l, "dia": d}


def add_dia(uid, n):
    inv(uid); db.execute("UPDATE inv SET dia=MAX(dia+?,0) WHERE uid=?", (n, uid)); db.commit()


def spend_dia(uid, n):
    inv(uid); ok = db.execute("UPDATE inv SET dia=dia-? WHERE uid=? AND dia>=?", (n, uid, n)).rowcount; db.commit()
    return bool(ok)


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


def stat(uid):  # o'yinlar, g'alabalar, tangalar
    db.execute("INSERT OR IGNORE INTO st(uid) VALUES(?)", (uid,))
    g, w, c = db.execute("SELECT games,wins,coins FROM st WHERE uid=?", (uid,)).fetchone()
    return {"games": g, "wins": w, "coins": c}


def auth(init: str):  # Telegram initData imzosini tekshirish
    d = dict(parse_qsl(init)); h = d.pop("hash", "")
    s = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    k = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    ok = hmac.compare_digest(hmac.new(k, s.encode(), hashlib.sha256).hexdigest(), h)
    return json.loads(d["user"]) if ok and time.time() - int(d.get("auth_date", 0)) < 86400 else None


def newp(name): return {"name": name, "role": "civil", "team": "c", "fl": "", "uses": None, "alive": True}


class Room:
    def __init__(r, rid=""):
        r.id, r.adm = rid, set()  # adm: guruh adminlari
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
                "day": r.day, "inv": inv(uid) if r.phase == "lobby" else None, "st": stat(uid) if r.phase == "lobby" else None,
                "can": uid == r.host or uid in r.adm, "tgt": me and me.get("tgt") and r.p[me["tgt"]]["name"]})
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
        if "l" not in p["fl"] and use(u, "luck"): p["fl"] += "l"
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
    r.phase = "end"
    for u, p in r.p.items():  # statistika va g'oliblarga 20 tanga
        stat(u); w = int(won(r, u))
        db.execute("UPDATE st SET name=?, games=games+1, wins=wins+?, coins=coins+? WHERE uid=?", (p["name"], w, 20 * w, u))
    db.commit(); await push(r)


async def handle(r, uid, m):
    t, to, me = m.get("t"), m.get("to"), r.p.get(uid)
    if t == "start" and r.phase == "lobby" and (uid == r.host or uid in r.adm) and len(r.p) >= MIN:
        r.task = asyncio.create_task(run(r))
    elif t == "again" and r.phase == "end" and (uid == r.host or uid in r.adm):
        r.reset(); await push(r)
    elif t == "buy" and r.phase == "lobby" and m.get("item") in ITEMS:
        it = m["item"]
        if spend_dia(uid, ITEMS[it][2]): db.execute(f"UPDATE inv SET {it}={it}+1 WHERE uid=?", (uid,)); db.commit()
        await push(r)
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


@dp.message(Command("newgame"))
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
    r, uid = rooms.setdefault(room, Room(room)), u["id"]
    if uid not in r.p and r.phase == "lobby" and len(r.p) < 40:
        r.p[uid] = newp(u.get("first_name", "?")[:14]); r.host = r.host if r.host in r.p else uid
    try:  # guruh adminlari o'yinni boshlay oladi
        if int(room) < 0 and (await bot.get_chat_member(int(room), uid)).status in ("administrator", "creator"): r.adm.add(uid)
    except Exception: pass
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

@dp.message(Command("profile"))
async def profile(m: Message):
    s, i = stat(m.from_user.id), inv(m.from_user.id)
    await m.answer(f"👤 {m.from_user.full_name}\n🎮 O'yinlar: {s['games']}\n🏆 G'alabalar: {s['wins']}\n🪙 Tangalar: {s['coins']}\n"
                   f"💎 Olmos: {i['dia']}\n🛡 Qalqon: {i['shield']} · 🗳 2x ovoz: {i['vote2']} · 🍀 Omad: {i['luck']}")


async def refresh(uid):
    for r in rooms.values():
        if uid in r.ws: await push(r)


@app.post("/invoice")
async def invoice(req: Request):  # Stars bilan olmos sotib olish
    d = await req.json(); u = auth(d.get("init", "")); pk = PACKS.get(d.get("item"))
    if not u or not pk: return {"error": "bad"}
    link = await bot.create_invoice_link(title=f"💎 {pk[0]} olmos", description="Olmos bilan o'yinda narsalar olasiz",
                                         payload=f"{d['item']}:{u['id']}", provider_token="", currency="XTR",
                                         prices=[LabeledPrice(label=f"{pk[0]} olmos", amount=pk[1])])
    return {"link": link}


@dp.pre_checkout_query()
async def pre(q: PreCheckoutQuery): await q.answer(ok=True)


@dp.message(F.successful_payment)
async def paid(m: Message):
    sp = m.successful_payment; item, uid = sp.invoice_payload.split(":"); uid = int(uid)
    if item not in PACKS or uid != m.from_user.id: return
    add_dia(uid, PACKS[item][0])
    db.execute("INSERT INTO orders VALUES(?,?,?,?)", (uid, item, sp.total_amount, sp.telegram_payment_charge_id)); db.commit()
    await m.answer(f"✅ 💎 {PACKS[item][0]} olmos hisobingizga qo'shildi!")
    if ADMIN: await bot.send_message(ADMIN, f"💰 {uid}: {item} ⭐{sp.total_amount}\nCharge: {sp.telegram_payment_charge_id}")
    await refresh(uid)


@dp.message(Command("refund"))
async def refund(m: Message):  # admin: /refund <user_id> <charge_id>
    if m.from_user.id != ADMIN: return
    try:
        _, uid, ch = m.text.split()
        await bot.refund_star_payment(user_id=int(uid), telegram_payment_charge_id=ch)
        row = db.execute("SELECT item FROM orders WHERE charge=?", (ch,)).fetchone()
        if row and row[0] in PACKS: add_dia(int(uid), -PACKS[row[0]][0])
        db.execute("DELETE FROM orders WHERE charge=?", (ch,)); db.commit()
        await m.answer("✅ Stars qaytarildi, olmos ayirildi"); await refresh(int(uid))
    except Exception as e: await m.answer(f"Xato: {e}\n/refund <user_id> <charge_id>")


@dp.message(Command("give"))
async def give(m: Message):  # admin: /give <user_id> <olmos>  (karta orqali to'lov uchun)
    if m.from_user.id != ADMIN: return
    try:
        _, uid, n = m.text.split(); add_dia(int(uid), int(n)); await m.answer(f"✅ {uid} ga {n} 💎 berildi")
    except Exception as e: return await m.answer(f"Xato: {e}\n/give <user_id> <olmos>")
    try: await bot.send_message(int(uid), f"💎 Hisobingizga {n} olmos qo'shildi!")
    except Exception: pass
    await refresh(int(uid))


# ---------------- Admin panel va foydalanuvchi menyusi ----------------
wait_for: dict = {}  # admin hozir nima kiritishi kutilmoqda
bc: dict = {}
B = InlineKeyboardButton
BACK = InlineKeyboardMarkup(inline_keyboard=[[B(text="⬅️ Panel", callback_data="adm:home")]])
ASK = {"give": "💎 Foydalanuvchi ID va olmos sonini yozing.\nMasalan: <code>123456789 50</code>",
       "user": "🔍 Foydalanuvchi ID raqamini yozing.", "refund": "↩️ Yozing: <code>user_id charge_id</code>",
       "bc": "📢 Hamma foydalanuvchilarga yuboriladigan xabarni yozing."}


def panel_kb(chat_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="📊 Statistika", callback_data="adm:stats"), B(text="🎮 Faol o'yinlar", callback_data="adm:rooms")],
        [B(text="🏆 Top 10", callback_data="adm:top"), B(text="🧾 To'lovlar", callback_data="adm:orders")],
        [B(text="💎 Olmos berish", callback_data="adm:give"), B(text="🔍 Foydalanuvchi", callback_data="adm:user")],
        [B(text="↩️ Refund", callback_data="adm:refund"), B(text="📢 Xabar tarqatish", callback_data="adm:bc")],
        [B(text="⚙️ Narxlar", callback_data="adm:price"), B(text="👤 Profilim", callback_data="adm:me")],
        [B(text="🎭 O'yinni ochish", url=f"https://t.me/{ME}/{APP}?startapp={chat_id}")]])


def ptext(uid, name=""):
    s, i = stat(uid), inv(uid)
    name = name or (db.execute("SELECT name FROM st WHERE uid=?", (uid,)).fetchone() or ("",))[0] or ""
    return (f"👤 {html.escape(name)} <code>{uid}</code>\n🎮 O'yinlar: {s['games']} · 🏆 G'alabalar: {s['wins']}\n"
            f"🪙 Tangalar: {s['coins']} · 💎 Olmos: {i['dia']}\n🛡 {i['shield']} · 🗳 {i['vote2']} · 🍀 {i['luck']}")


async def do_refund(uid, ch):
    await bot.refund_star_payment(user_id=uid, telegram_payment_charge_id=ch)
    row = db.execute("SELECT item FROM orders WHERE charge=?", (ch,)).fetchone()
    if row and row[0] in PACKS: add_dia(uid, -PACKS[row[0]][0])
    db.execute("DELETE FROM orders WHERE charge=?", (ch,)); db.commit(); await refresh(uid)


async def bcast(text):
    ok = 0
    for (uid,) in db.execute("SELECT uid FROM st").fetchall():
        try: await bot.send_message(uid, text); ok += 1
        except Exception: pass
        await asyncio.sleep(0.05)
    await bot.send_message(ADMIN, f"✅ Xabar {ok} kishiga yetkazildi")


async def show_panel(m: Message):
    wait_for.pop(ADMIN, None)
    await m.answer("🛠 <b>Admin panel</b>\nKerakli bo'limni tanlang:", parse_mode="HTML", reply_markup=panel_kb(m.chat.id))


@dp.message(Command("start"))
async def start(m: Message):
    if m.chat.type != "private": return await newgame(m)
    stat(m.from_user.id)  # xabar tarqatish ro'yxatiga qo'shiladi
    if m.from_user.id == ADMIN: return await show_panel(m)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [B(text="🎭 O'yinni ochish", url=f"https://t.me/{ME}/{APP}?startapp={m.chat.id}")],
        [B(text="👤 Profilim", callback_data="u:me"), B(text="ℹ️ Qo'llanma", callback_data="u:help")]])
    await m.answer("🎭 <b>Mafiya o'yini</b>\n40 xil rol, do'stlar bilan guruhda o'ynang!", parse_mode="HTML", reply_markup=kb)


@dp.message(Command("admin"))
async def admin_cmd(m: Message):
    if m.from_user.id == ADMIN: await show_panel(m)


@dp.callback_query(F.data.startswith("u:"))
async def user_cb(c: CallbackQuery):
    await c.answer()
    if c.data == "u:me": await c.message.answer(ptext(c.from_user.id, c.from_user.full_name), parse_mode="HTML")
    else: await c.message.answer("ℹ️ Botni guruhga qo'shing va /newgame yozing. Hamma «O'yinni ochish» tugmasini bosib qo'shiladi, "
                                 "keyin «Boshlash» bosiladi (kamida " + str(MIN) + " kishi). Tunda rolingiz bo'yicha nishon tanlang, "
                                 "kunduzi ovoz bering. G'olib +20 🪙 oladi (reyting uchun).")


@dp.callback_query(F.data.startswith("adm:"))
async def adm(c: CallbackQuery):
    if c.from_user.id != ADMIN: return await c.answer("Ruxsat yo'q", show_alert=True)
    k, m = c.data[4:], c.message
    await c.answer()
    out = lambda t: m.answer(t, parse_mode="HTML", reply_markup=BACK)
    if k == "home": return await show_panel(m)
    if k in ASK: wait_for[ADMIN] = k; return await m.answer(ASK[k] + "\n\n(Bekor qilish: /admin)", parse_mode="HTML")
    if k == "bcgo":
        t = bc.pop("t", None)
        if t: asyncio.create_task(bcast(t)); await m.answer("⏳ Yuborilmoqda...")
    elif k == "stats":
        u, = db.execute("SELECT COUNT(*) FROM st").fetchone(); g, = db.execute("SELECT COALESCE(SUM(games),0) FROM st").fetchone()
        o, rev = db.execute("SELECT COUNT(*), COALESCE(SUM(stars),0) FROM orders").fetchone(); d, = db.execute("SELECT COALESCE(SUM(dia),0) FROM inv").fetchone()
        await out(f"📊 <b>Statistika</b>\n👥 Foydalanuvchilar: {u}\n🎮 Jami o'yin (o'yinchi hisobida): {g}\n💰 To'lovlar: {o} ta, ⭐{rev}\n"
                  f"💎 Muomaladagi olmos: {d}\n🟢 Faol xonalar: {len(rooms)}, onlayn: {sum(len(r.ws) for r in rooms.values())}")
    elif k == "rooms":
        await out("🎮 <b>Faol o'yinlar</b>\n" + ("\n".join(f"<code>{i}</code> · {r.phase} · {len(r.p)} kishi" for i, r in rooms.items()) or "Hozir hech qanday o'yin yo'q."))
    elif k == "top":
        rows = db.execute("SELECT name,wins,games,coins FROM st WHERE games>0 ORDER BY wins DESC LIMIT 10").fetchall()
        await out("🏆 <b>Top 10</b>\n" + ("\n".join(f"{i}. {html.escape(n or '?')} — 🏆{w} / 🎮{g} / 🪙{c}" for i, (n, w, g, c) in enumerate(rows, 1)) or "Hali o'yin yo'q."))
    elif k == "orders":
        rows = db.execute("SELECT uid,item,stars,charge FROM orders ORDER BY rowid DESC LIMIT 10").fetchall()
        await out("🧾 <b>So'nggi to'lovlar</b> (bosib nusxalang)\n" + ("\n".join(f"{it} ⭐{st}: <code>/refund {u} {ch}</code>" for u, it, st, ch in rows) or "To'lovlar yo'q."))
    elif k == "price":
        await out("⚙️ <b>Narxlar</b> (server.py da o'zgartiriladi)\n" + "\n".join(f"{n[0]} — {n[2]}💎" for n in ITEMS.values())
                  + "\n" + "\n".join(f"💎{n} — ⭐{st}" for n, st in PACKS.values()) + "\n🪙 G'olibga +20 (olmosga almashtirib bo'lmaydi)")
    elif k == "me": await out(ptext(ADMIN, c.from_user.full_name))


@dp.message(F.text, F.chat.type == "private", F.from_user.id == ADMIN)
async def admin_input(m: Message):
    a = wait_for.get(ADMIN)
    if not a or m.text.startswith("/"): return
    wait_for.pop(ADMIN, None)
    try:
        if a == "give":
            uid, n = m.text.split(); add_dia(int(uid), int(n)); await m.answer(f"✅ {uid} ga {n} 💎 berildi", reply_markup=BACK)
            try: await bot.send_message(int(uid), f"💎 Hisobingizga {n} olmos qo'shildi!")
            except Exception: pass
            await refresh(int(uid))
        elif a == "user": await m.answer(ptext(int(m.text)), parse_mode="HTML", reply_markup=BACK)
        elif a == "refund":
            uid, ch = m.text.split(); await do_refund(int(uid), ch); await m.answer("✅ Stars qaytarildi, olmos ayirildi", reply_markup=BACK)
        elif a == "bc":
            bc["t"] = m.text; n, = db.execute("SELECT COUNT(*) FROM st").fetchone()
            await m.answer(f"📢 Xabar {n} kishiga yuboriladi:\n\n{m.text}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [B(text="✅ Yuborish", callback_data="adm:bcgo"), B(text="❌ Bekor", callback_data="adm:home")]]))
    except Exception as e: await m.answer(f"Xato: {e}", reply_markup=BACK)


app.mount("/", StaticFiles(directory="static", html=True))
