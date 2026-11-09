"""Mafiya Mini App serveri — 40 rol. FastAPI + WebSocket + aiogram (webhook).
Env: BOT_TOKEN, BASE_URL, APP_NAME, MIN_PLAYERS
Start: uvicorn server:app --host 0.0.0.0 --port $PORT"""
import asyncio, hashlib, hmac, html, json, os, random, sqlite3, time
from datetime import datetime, timedelta, timezone
from collections import Counter
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message, PreCheckoutQuery, Update

TOKEN, BASE = os.environ["BOT_TOKEN"], os.environ["BASE_URL"]
APP, MIN = os.getenv("APP_NAME", "mafia"), int(os.getenv("MIN_PLAYERS", "4"))
T = {"night": 35, "day": 45, "vote": 25}
bot, dp, ME = Bot(TOKEN), Dispatcher(), ""
rooms: dict = {}


class DelCmd(BaseMiddleware):  # guruhdagi har qanday /buyruq xabari ishlangach o'chiriladi (botga xabarlarni o'chirish huquqi kerak)
    async def __call__(self, handler, m, data):
        res = await handler(m, data)
        if getattr(m, "chat", None) and m.chat.type in ("group", "supergroup") and (m.text or "").startswith("/"):
            try: await m.delete()
            except Exception: pass
        return res
dp.message.outer_middleware(DelCmd())
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
                 "CREATE TABLE IF NOT EXISTS wk(week TEXT, uid INT, name TEXT, pts INT DEFAULT 0, PRIMARY KEY(week, uid));"
                 "CREATE TABLE IF NOT EXISTS trn(id INTEGER PRIMARY KEY AUTOINCREMENT, price INT, min INT, end_ts INT, status TEXT, pool INT DEFAULT 0);"
                 "CREATE TABLE IF NOT EXISTS trn_p(tid INT, uid INT, name TEXT, pts INT DEFAULT 0, charge TEXT, PRIMARY KEY(tid, uid));"
                 "CREATE TABLE IF NOT EXISTS trn_w(tid INT, uid INT, place INT, amt INT, status TEXT DEFAULT 'wait', PRIMARY KEY(tid, uid));"
                 "CREATE TABLE IF NOT EXISTS prem(uid INTEGER PRIMARY KEY, until INT DEFAULT 0, nick TEXT);"
                 "CREATE TABLE IF NOT EXISTS prof(uid INTEGER PRIMARY KEY, gender TEXT, partner INT);"
                 "CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);"
                 "CREATE TABLE IF NOT EXISTS crown(uid INT PRIMARY KEY, week TEXT);"
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
 "civil": ("👤", "Tinch aholi", "c", None, None, ""), "doctor": ("💉", "Doktor", "c", "heal", 1, ""),
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


# 🏆 Haftalik turnir: qatnashish bepul, ball: o'yin +1, g'alaba +3. Sovrin faqat o'yin ichida (pulga aylanmaydi)
PRIZES = [30, 15, 8]  # 1, 2, 3-o'rin uchun 💎 (1-o'rin + 👑 belgisi)
TZ = timezone(timedelta(hours=5))  # O'zbekiston vaqti


def wkey(): return datetime.now(TZ).strftime("%G-W%V")


def wk_left():
    n = datetime.now(TZ)
    end = (n + timedelta(days=7 - n.isoweekday())).replace(hour=23, minute=59, second=59, microsecond=0)
    return max(0, int((end - n).total_seconds()))


def wk_top(week=None, n=10):
    return db.execute("SELECT uid,name,pts FROM wk WHERE week=? ORDER BY pts DESC, rowid ASC LIMIT ?", (week or wkey(), n)).fetchall()


def tour(uid):
    r = db.execute("SELECT pts FROM wk WHERE week=? AND uid=?", (wkey(), uid)).fetchone()
    rank = db.execute("SELECT COUNT(*) FROM wk WHERE week=? AND pts>?", (wkey(), r[0])).fetchone()[0] + 1 if r else None
    return {"top": [[n, p] for _, n, p in wk_top()], "me": {"rank": rank, "pts": r[0] if r else 0}, "left": wk_left(), "prizes": PRIZES}


# 🏆 Pulli turnir (admin boshlaydi). Fond = kirish pullarining 40%, g'oliblar = qatnashchilarning ~20% (o'rin bo'yicha kamayib boradi)
def trn_open_id():
    r = db.execute("SELECT id FROM trn WHERE status='open'").fetchone()
    return r[0] if r else None


def trn_state(uid):
    t = db.execute("SELECT id,price,min,end_ts,status FROM trn ORDER BY id DESC LIMIT 1").fetchone()
    if not t: return None
    tid, price, mn, end, st = t
    n = db.execute("SELECT COUNT(*) FROM trn_p WHERE tid=?", (tid,)).fetchone()[0]
    me = db.execute("SELECT pts FROM trn_p WHERE tid=? AND uid=?", (tid, uid)).fetchone()
    top = [[a, b] for a, b in db.execute("SELECT name,pts FROM trn_p WHERE tid=? ORDER BY pts DESC, rowid ASC LIMIT 10", (tid,))]
    win = [list(x) for x in db.execute("SELECT w.place,p.name,w.amt,w.status,w.uid FROM trn_w w JOIN trn_p p ON p.tid=w.tid AND p.uid=w.uid WHERE w.tid=? ORDER BY w.place", (tid,))]
    return {"id": tid, "st": st, "price": price, "min": mn, "n": n, "in": bool(me), "pts": me[0] if me else 0,
            "left": max(0, end - int(time.time())), "top": top, "win": win, "pool": n * price * 40 // 100}


async def trn_close(force=False, cancel=False):
    t = db.execute("SELECT id,price,min,end_ts FROM trn WHERE status='open'").fetchone()
    if not t: return "Ochiq turnir yo'q." if force else ""
    tid, price, mn, end = t
    if not force and time.time() < end: return ""
    rows = db.execute("SELECT uid,name,pts,charge FROM trn_p WHERE tid=? ORDER BY pts DESC, rowid ASC", (tid,)).fetchall()
    act = [x for x in rows if x[2] > 0]
    if cancel or len(rows) < mn or not act:  # yetarli odam yo'q: hammaga pul qaytariladi
        for uid, _, _, ch in rows:
            try: await bot.refund_star_payment(user_id=uid, telegram_payment_charge_id=ch)
            except Exception as e: print("refund:", e)
            try: await bot.send_message(uid, f"↩️ Turnir #{tid} bekor qilindi, {price} ⭐ qaytarildi.")
            except Exception: pass
        db.execute("UPDATE trn SET status='cancel' WHERE id=?", (tid,)); db.commit()
        for x in rows: await refresh(x[0])
        return f"↩️ Turnir #{tid} bekor qilindi, {len(rows)} kishiga pul qaytarildi."
    pool = len(rows) * price * 40 // 100
    k = min(len(act), max(1, int(len(rows) * 0.2 + 0.5)))
    wts = [k - i for i in range(k)]; amts = [pool * x // sum(wts) for x in wts]; amts[0] += pool - sum(amts)
    lines = []
    for i, ((uid, name, pts, _), amt) in enumerate(zip(act, amts), 1):
        db.execute("INSERT INTO trn_w(tid,uid,place,amt) VALUES(?,?,?,?)", (tid, uid, i, amt))
        lines.append(f"{i}. {html.escape(name)} — {pts} ball, {amt} ⭐")
        try: await bot.send_message(uid, f"🏆 Turnir #{tid} yakunlandi! {i}-o'rin: {amt} ⭐. Ilovada «Turnir» bo'limida «Yutug'ini olish» ni bosing.")
        except Exception: pass
    db.execute("UPDATE trn SET status='done', pool=? WHERE id=?", (pool, tid)); db.commit()
    for x in rows: await refresh(x[0])
    return f"🏁 Turnir #{tid} yakunlandi. Fond: {pool} ⭐\n" + "\n".join(lines)


HORN = "🦌"  # Premium: ism yonidagi "shox" belgisi (o'zgartirish mumkin)
PLANS = {"p1": (1, 5), "p3": (3, 8), "p10": (10, 15), "p30": (30, 50)}  # kalit: (kun, narx ⭐)


def prem(uid):
    r = db.execute("SELECT until,nick FROM prem WHERE uid=?", (uid,)).fetchone()
    return {"left": max(0, (r[0] if r else 0) - int(time.time())), "nick": (r[1] if r and r[1] else "")}


def pname(uid, base):  # o'yindagi ism: premium bo'lsa maxsus nom + shox
    p = prem(uid)
    n = (HORN + p["nick"]) if p["left"] and p["nick"] else base
    return ("👑" if db.execute("SELECT 1 FROM crown WHERE uid=? AND week=?", (uid, wkey())).fetchone() else "") + n


def prof(uid):  # jins va juft
    r = db.execute("SELECT gender,partner FROM prof WHERE uid=?", (uid,)).fetchone() or (None, None)
    nm = db.execute("SELECT name FROM st WHERE uid=?", (r[1],)).fetchone() if r[1] else None
    return {"g": r[0], "pid": r[1], "pn": (nm[0] if nm else "") or ""}


def ml(uid, name):  # guruhda ko'k, bosilsa profilga o'tadigan ism
    return f"<a href='tg://user?id={uid}'>{html.escape(str(name))}</a>"


def touch(uid, name):
    stat(uid); db.execute("UPDATE st SET name=? WHERE uid=?", (name, uid)); db.commit()


def apply_name(uid):
    for r in rooms.values():
        p = r.p.get(uid)
        if p and r.phase == "lobby": p["name"] = pname(uid, p.get("base", p["name"]))


async def say(r, text):  # guruhga bot xabari
    try:
        if int(r.id) < 0: await bot.send_message(int(r.id), text, parse_mode="HTML")
    except Exception: pass


async def note(r, uid, text):
    try: await r.ws[uid].send_json({"note": text})
    except Exception: pass


def ntext(r, w):
    nm = lambda i: f"{ml(i, r.p[i]['name'])} ({R[r.p[i]['role']][0]} {R[r.p[i]['role']][1]})"
    if w["kind"] == "night":
        t = ("☠️ Tunda o'ldirildi: " + ", ".join(nm(i) for i in w["dead"])) if w["dead"] else "🌅 Tun xotirjam o'tdi, hech kim o'lmadi."
        return t + (("\n🕯 Tirildi: " + ", ".join(ml(i, r.p[i]["name"]) for i in w["rev"])) if w["rev"] else "")
    return ("⚰️ Osildi: " + ", ".join(nm(i) for i in w["dead"])) if w["dead"] else ("🛂 Imtiyozli omon qoldi!" if w.get("imm") else "🤷 Hech kim osilmadi.")


def auth(init: str):  # Telegram initData imzosini tekshirish
    d = dict(parse_qsl(init)); h = d.pop("hash", "")
    s = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    k = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    ok = hmac.compare_digest(hmac.new(k, s.encode(), hashlib.sha256).hexdigest(), h)
    return json.loads(d["user"]) if ok and time.time() - int(d.get("auth_date", 0)) < 86400 else None


def newp(name): return {"name": name, "role": "civil", "team": "c", "fl": "", "uses": None, "alive": True}


class Room:
    def __init__(r, rid=""):
        r.id, r.adm = rid, set(); r.chat, r.last, r.mode = [], {}, {}  # adm: guruh adminlari
        r.p, r.ws, r.host, r.task, r.ev = {}, {}, None, None, asyncio.Event(); r.need = set(); r.reset()

    def reset(r):
        r.phase, r.day, r.win, r.wid, r.news, r.until = "lobby", 0, None, None, None, time.time()
        r.priv, r.acts, r.acted, r.votes, r.poison, r.muted = {}, {}, set(), {}, set(), set()
        for u in list(r.p):
            if u in r.ws: r.p[u] = {**newp(r.p[u]["name"]), "ph": r.p[u].get("ph", ""), "base": r.p[u].get("base", "?")}
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
                       "votes": sum(wt(r, v) for v, t in r.votes.items() if t == i), "w": end and won(r, i), "ph": p.get("ph", "")})
        act = me and me["alive"] and me["uses"] != 0 and R[me["role"]][3] or None
        try:
            await ws.send_json({"phase": r.phase, "left": max(0, int(r.until - time.time())), "host": r.host, "me": uid,
                "role": me and me["role"], "kills": me and me.get("kills"), "act": act, "uses": me and me["uses"], "players": pl, "news": r.news,
                "win": r.win, "min": MIN, "info": r.priv.get(uid, []), "muted": uid in r.muted, "acted": uid in r.acted,
                "day": r.day, "chat": r.chat if r.phase in ("day", "vote") else [], "inv": inv(uid) if r.phase == "lobby" else None, "tour": tour(uid) if r.phase == "lobby" else None, "trn": trn_state(uid) if r.phase == "lobby" else None, "pm": prem(uid) if r.phase == "lobby" else None, "pf": prof(uid) if r.phase == "lobby" else None, "st": stat(uid) if r.phase == "lobby" else None,
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
        p.update(role=k, team=e[2], fl=e[5], uses=e[4], vest=int("v" in e[5]), lv=int("i" in e[5]), kills=2 if k == "sheriff" else 0)
        if use(u, "shield"): p["vest"] += 1
        if "2" not in p["fl"] and use(u, "vote2"): p["fl"] += "2"
        if "l" not in p["fl"] and use(u, "luck"): p["fl"] += "l"
    civ = [u for u in ids if r.p[u]["team"] == "c"]
    for u in ids:
        if r.p[u]["role"] == "executioner" and civ: r.p[u]["tgt"] = random.choice(civ)
        elif r.p[u]["role"] == "executioner": r.p[u]["fl"] = "J"


def resolve(r):
    P, inf, nm = r.p, (lambda u, s: r.priv.setdefault(u, []).append(s)), (lambda u: r.p[u]["name"])
    act = lambda u: "shoot" if r.mode.get(u) == "kill" else R[P[u]["role"]][3]
    A = {u: t for u, t in r.acts.items() if u in P and P[u]["alive"]}
    for u, t in A.items():
        if act(u) == "block": inf(t, "🚫 Bu tun sizning harakatingiz to'sildi!")
    B = {t for u, t in A.items() if act(u) == "block"}
    A = {u: t for u, t in A.items() if u not in B}
    for u in A:
        if P[u]["uses"] is not None: P[u]["uses"] -= 1
        if r.mode.get(u) == "kill": P[u]["kills"] -= 1
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
    deal(r); r.phase = "roles"
    await say(r, f"🎭 <b>Mafiya boshlandi!</b> O'yinchilar: {len(r.p)}\n" + "\n".join("• " + ml(u, p["name"]) for u, p in r.p.items()) + "\nRollar ilovada ko'rinadi. Tez orada tun tushadi...")
    await wait(r, 7)
    while True:
        r.day += 1; r.phase = "night"; r.acts, r.acted, r.priv, r.votes = {}, set(), {}, {}; r.mode = {}
        await say(r, f"🌙 <b>{r.day}-tun.</b> Shahar uxladi. Mafiya, Doktor va Komissar ilovada tanlov qilmoqda...")
        r.need = {u for u, p in r.p.items() if p["alive"] and R[p["role"]][3] and p["uses"] != 0}
        await wait(r, T["night"])
        r.news = resolve(r); r.win = winner(r)
        await say(r, ntext(r, r.news))
        if r.win: break
        r.chat = []; r.phase = "day"; await say(r, "☀️ <b>Kun boshlandi.</b> Muhokama qiling: kim mafiya?"); await wait(r, T["day"])
        r.phase, r.votes = "vote", {}; await say(r, "🗳 <b>Ovoz berish boshlandi!</b> Ilovada kimni osishni tanlang."); await wait(r, T["vote"])
        lynch(r); r.win = r.win or winner(r)
        await say(r, ntext(r, r.news))
        if r.win: break
        await push(r); await asyncio.sleep(3)
    r.phase = "end"
    for u, p in r.p.items():  # statistika va g'oliblarga 20 tanga
        stat(u); w = int(won(r, u))
        db.execute("UPDATE st SET name=?, games=games+1, wins=wins+?, coins=coins+? WHERE uid=?", (p["name"], w, 20 * w, u))
        db.execute("INSERT INTO wk(week,uid,name,pts) VALUES(?,?,?,?) ON CONFLICT(week,uid) DO UPDATE SET pts=pts+?, name=?",
                   (wkey(), u, p["name"], 1 + 3 * w, 1 + 3 * w, p["name"]))
        tid = trn_open_id()
        if tid: db.execute("UPDATE trn_p SET pts=pts+? WHERE tid=? AND uid=?", (1 + 3 * w, tid, u))
    db.commit(); await push(r)
    wn = {"civil": "🎉 Tinch aholi", "mafia": "😈 Mafiya", "killer": "🔪 Yolg'iz qotil", "jester": "🤡 Hazilkash", "exec": "🪓 Jallod"}.get(r.win, "")
    await say(r, f"🏁 <b>O'yin tugadi: {wn} g'alaba qildi!</b>\n\n" + "\n".join(f"{'🏆' if won(r, u) else '▫️'} {ml(u, p['name'])} — {R[p['role']][0]} {R[p['role']][1]}" for u, p in r.p.items()) + "\n\nYana o'ynash: /newgame")


async def handle(r, uid, m):
    t, to, me = m.get("t"), m.get("to"), r.p.get(uid)
    if t == "start" and r.phase == "lobby" and (uid == r.host or uid in r.adm) and len(r.p) >= MIN:
        r.task = asyncio.create_task(run(r))
    elif t == "again" and r.phase == "end" and (uid == r.host or uid in r.adm):
        r.reset(); await push(r)
    elif t == "chat" and r.phase in ("day", "vote") and me and me["alive"]:  # tirik o'yinchilar yozadi
        v = str(m.get("v", "")).strip()[:200]
        if v and time.time() - r.last.get(uid, 0) > 1.5:
            r.last[uid] = time.time(); r.chat = (r.chat + [[me["name"], v, uid]])[-40:]; await push(r)
    elif t == "gift":  # olmos / tanga sovg'a qilish
        try: to, n, k = int(m.get("to")), int(m.get("n")), m.get("k")
        except Exception: return await note(r, uid, "❌ ID va miqdorni to'g'ri yozing")
        ok = to != uid and n > 0 and k in ("dia", "coin") and db.execute("SELECT 1 FROM st WHERE uid=?", (to,)).fetchone()
        if ok:
            inv(uid); inv(to)
            ok = db.execute("UPDATE inv SET dia=dia-? WHERE uid=? AND dia>=?", (n, uid, n)).rowcount if k == "dia" else db.execute("UPDATE st SET coins=coins-? WHERE uid=? AND coins>=?", (n, uid, n)).rowcount
            if ok: db.execute("UPDATE inv SET dia=dia+? WHERE uid=?", (n, to)) if k == "dia" else db.execute("UPDATE st SET coins=coins+? WHERE uid=?", (n, to))
            db.commit()
        if not ok: return await note(r, uid, "❌ ID topilmadi yoki yetarli emas")
        a = db.execute("SELECT name FROM st WHERE uid=?", (uid,)).fetchone()[0] or str(uid); b = db.execute("SELECT name FROM st WHERE uid=?", (to,)).fetchone()[0] or str(to)
        txt = f"🎁 {ml(uid, a)} → {ml(to, b)} ga {n} ta {'💎 olmos' if k == 'dia' else '🪙 tanga'} sovg'a qildi!"
        try: await bot.send_message(int(r.id) if int(r.id) < 0 else to, txt, parse_mode="HTML")
        except Exception: pass
        await note(r, uid, "✅ Yuborildi"); await refresh(uid); await refresh(to)
    elif t == "gender" and m.get("g") in ("m", "f"):
        db.execute("INSERT INTO prof(uid,gender) VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET gender=?", (uid, m["g"], m["g"])); db.commit(); await push(r)
    elif t == "pair":  # para taklifi
        try: to = int(m.get("to"))
        except Exception: return await note(r, uid, "❌ ID noto'g'ri")
        a, b = prof(uid), prof(to)
        if to == uid or not a["g"] or not b["g"] or a["g"] == b["g"] or a["pid"] or b["pid"]:
            return await note(r, uid, "❌ Para faqat o'g'il va qiz bola o'rtasida, ikkalasi jinsini tanlagan va juftsiz bo'lishi kerak")
        try:
            await bot.send_message(to, f"💞 <a href='tg://user?id={uid}'>{html.escape(me['name'] if me else str(uid))}</a> sizga para bo'lishni taklif qilmoqda!", parse_mode="HTML",
                                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(text="💖 Qabul qilish", callback_data=f"pr:y:{uid}"), B(text="❌ Rad etish", callback_data=f"pr:n:{uid}")]]))
            await note(r, uid, "💞 Taklif yuborildi")
        except Exception: await note(r, uid, "❌ U botga /start bosmagan")
    elif t == "unpair":
        a = prof(uid)
        if a["pid"]: db.execute("UPDATE prof SET partner=NULL WHERE uid IN (?,?)", (uid, a["pid"])); db.commit(); await refresh(a["pid"]); await push(r)
    elif t == "nick" and prem(uid)["left"]:  # premium: maxsus nom
        v = str(m.get("v", "")).strip()[:14]
        db.execute("INSERT INTO prem(uid,until,nick) VALUES(?,0,?) ON CONFLICT(uid) DO UPDATE SET nick=?", (uid, v, v)); db.commit(); apply_name(uid); await push(r)
    elif t == "claim":  # g'olib yutug'ini so'raydi
        tid = int(m.get("tid") or 0)
        w = db.execute("SELECT place,amt FROM trn_w WHERE tid=? AND uid=? AND status='wait'", (tid, uid)).fetchone()
        if w:
            db.execute("UPDATE trn_w SET status='req' WHERE tid=? AND uid=?", (tid, uid)); db.commit()
            nm = r.p[uid]["name"] if uid in r.p else uid
            if ADMIN: await bot.send_message(ADMIN, f"🎁 {nm} ({uid}) yutuq so'radi: turnir #{tid}, {w[0]}-o'rin, {w[1]} ⭐",
                                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(text="✅ To'landi", callback_data=f"adm:paid:{tid}:{uid}")]]))
            await push(r)
    elif t == "buy" and r.phase == "lobby" and m.get("item") in ITEMS:
        it = m["item"]
        if spend_dia(uid, ITEMS[it][2]): db.execute(f"UPDATE inv SET {it}={it}+1 WHERE uid=?", (uid,)); db.commit()
        await push(r)
    elif t == "act" and r.phase == "night" and me and me["alive"] and uid not in r.acted:
        a, tg = R[me["role"]][3], r.p.get(to)
        if not a or me["uses"] == 0 or not tg: return
        if (a == "revive") == tg["alive"] or (to == uid and a not in ("heal", "heal2")) or (a == "mkill" and tg["team"] == "m"): return
        if m.get("mode") == "kill" and me["role"] == "sheriff" and me.get("kills", 0) > 0: r.mode[uid] = "kill"
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
    loop_task = asyncio.create_task(payout_loop())
    yield

app = FastAPI(lifespan=life)


async def payout_loop():  # hafta tugaganda sovrinlarni avtomatik beradi
    while True:
        try:
            cur = wkey(); row = db.execute("SELECT v FROM meta WHERE k='wk'").fetchone()
            if row and row[0] != cur:
                lines = []
                for i, (uid, name, pts) in enumerate(wk_top(row[0], 3)):
                    add_dia(uid, PRIZES[i]); lines.append(f"{'🥇🥈🥉'[i]} {name} — {pts} ball, +{PRIZES[i]}💎")
                    if i == 0: db.execute("INSERT OR REPLACE INTO crown VALUES(?,?)", (uid, cur))
                    try: await bot.send_message(uid, f"🏆 Haftalik turnirda {i + 1}-o'rin! +{PRIZES[i]} 💎" + (" Siz haftaning 👑 chempionisiz!" if i == 0 else ""))
                    except Exception: pass
                if ADMIN and lines: await bot.send_message(ADMIN, f"🏆 {row[0]} turniri yakunlandi:\n" + "\n".join(lines))
            if not row or row[0] != cur: db.execute("INSERT OR REPLACE INTO meta VALUES('wk',?)", (cur,)); db.commit()
        except Exception as e: print("payout:", e)
        try:
            txt = await trn_close()  # turnir muddati tugagan bo'lsa yakunlaydi
            if txt and ADMIN: await bot.send_message(ADMIN, txt, parse_mode="HTML")
        except Exception as e: print("trn:", e)
        await asyncio.sleep(60)


@dp.message(Command("turnir"))
async def turnir(m: Message):
    rows, h = wk_top(), wk_left() // 3600
    await m.answer("🏆 Haftalik turnir\n" + ("\n".join(f"{i}. {n} — {p}" for i, (_, n, p) in enumerate(rows, 1)) or "Hali o'yin yo'q.")
                   + f"\n\n🎁 🥇{PRIZES[0]}💎+👑 · 🥈{PRIZES[1]}💎 · 🥉{PRIZES[2]}💎\n⏳ Tugashiga: {h // 24} kun {h % 24} soat\nBall: o'yin +1, g'alaba +3. Qatnashish bepul!")


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
        touch(uid, u.get("first_name", "?")[:14]); r.p[uid] = {**newp(pname(uid, u.get("first_name", "?")[:14])), "ph": u.get("photo_url", ""), "base": u.get("first_name", "?")[:14]}; r.host = r.host if r.host in r.p else uid
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
async def invoice(req: Request):  # Stars bilan olmos yoki turnirga kirish
    d = await req.json(); u = auth(d.get("init", ""))
    if not u: return {"error": "bad"}
    if d.get("item") == "trn":
        t = db.execute("SELECT id,price FROM trn WHERE status='open'").fetchone()
        if not t or db.execute("SELECT 1 FROM trn_p WHERE tid=? AND uid=?", (t[0], u["id"])).fetchone(): return {"error": "bad"}
        title, price, payload = f"🏆 Turnir #{t[0]}", t[1], f"T{t[0]}:{u['id']}"
    elif d.get("item") in PLANS:
        dd, price = PLANS[d["item"]]; title, payload = f"👑 Premium {dd} kun", f"{d['item']}:{u['id']}"
    elif d.get("item") in PACKS:
        pk = PACKS[d["item"]]; title, price, payload = f"💎 {pk[0]} olmos", pk[1], f"{d['item']}:{u['id']}"
    else: return {"error": "bad"}
    link = await bot.create_invoice_link(title=title, description="Mafiya o'yini", payload=payload, provider_token="",
                                         currency="XTR", prices=[LabeledPrice(label=title, amount=price)])
    return {"link": link}


@dp.pre_checkout_query()
async def pre(q: PreCheckoutQuery): await q.answer(ok=True)


@dp.message(F.successful_payment)
async def paid(m: Message):
    sp = m.successful_payment; item, uid = sp.invoice_payload.split(":"); uid = int(uid)
    if item.startswith("T") and uid == m.from_user.id:  # turnirga kirish
        tid = int(item[1:]); t = db.execute("SELECT status FROM trn WHERE id=?", (tid,)).fetchone()
        ch = sp.telegram_payment_charge_id
        if t and t[0] == "open":
            db.execute("INSERT OR IGNORE INTO trn_p(tid,uid,name,charge) VALUES(?,?,?,?)", (tid, uid, m.from_user.full_name[:14], ch)); db.commit()
            await m.answer("✅ Turnirga qo'shildingiz! O'ynang va ball yig'ing.")
            if ADMIN: await bot.send_message(ADMIN, f"🏆 Turnir #{tid}: {m.from_user.full_name} qo'shildi (⭐{sp.total_amount})")
            await refresh(uid)
        else:
            await bot.refund_star_payment(user_id=uid, telegram_payment_charge_id=ch); await m.answer("Turnir yopilgan edi, ⭐ qaytarildi.")
        return
    if item in PLANS and uid == m.from_user.id:  # premium
        days = PLANS[item][0]; end = int(time.time()) + prem(uid)["left"] + days * 86400
        db.execute("INSERT INTO prem(uid,until) VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET until=?", (uid, end, end)); db.commit()
        await m.answer(f"👑 Premium {days} kunga faollashdi! Ilovada «Premium» bo'limida maxsus nom qo'ying.")
        apply_name(uid); await refresh(uid); return
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
       "bc": "📢 Hamma foydalanuvchilarga yuboriladigan xabarni yozing.",
       "tnew": "🏆 Turnir: kirish narxi (⭐), minimal ishtirokchi, davomiyligi (soat).\nMasalan: <code>10 25 48</code>"}


def panel_kb(chat_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [B(text="📊 Statistika", callback_data="adm:stats"), B(text="🎮 Faol o'yinlar", callback_data="adm:rooms")],
        [B(text="🏆 Top 10", callback_data="adm:top"), B(text="🧾 To'lovlar", callback_data="adm:orders")],
        [B(text="💎 Olmos berish", callback_data="adm:give"), B(text="🔍 Foydalanuvchi", callback_data="adm:user")],
        [B(text="↩️ Refund", callback_data="adm:refund"), B(text="📢 Xabar tarqatish", callback_data="adm:bc")],
        [B(text="⚙️ Narxlar", callback_data="adm:price"), B(text="👤 Profilim", callback_data="adm:me")],
        [B(text="🏆 Turnir boshlash", callback_data="adm:tnew"), B(text="🏁 Tugatish", callback_data="adm:tend")],
        [B(text="🚫 Turnirni bekor qilish (refund)", callback_data="adm:tcancel")],
        [B(text="🏆 Turnir", callback_data="adm:tour"), B(text="🎭 O'yinni ochish", url=f"https://t.me/{ME}/{APP}?startapp={chat_id}")]])


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
    if k.startswith("paid:"):  # g'olibga to'landi
        _, tid, uid = k.split(":")
        db.execute("UPDATE trn_w SET status='paid' WHERE tid=? AND uid=?", (int(tid), int(uid))); db.commit()
        try: await bot.send_message(int(uid), "✅ Turnir yutug'ingiz to'landi!")
        except Exception: pass
        await refresh(int(uid)); return await m.answer("✅ Belgilandi")
    if k in ("tend", "tcancel"): return await out(await trn_close(True, k == "tcancel"))
    if k == "tann":
        t = db.execute("SELECT price,min FROM trn WHERE status='open'").fetchone()
        if t: asyncio.create_task(bcast(f"🏆 Yangi turnir! Kirish {t[0]} ⭐, kamida {t[1]} kishi. Ilovada «Turnir» bo'limidan qo'shiling: https://t.me/{ME}/{APP}"))
        return await m.answer("⏳ E'lon yuborilmoqda...")
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
    elif k == "tour":
        rows = wk_top(); await out(f"🏆 <b>Turnir {wkey()}</b>\n" + ("\n".join(f"{i}. {html.escape(n or '?')} — {p}" for i, (_, n, p) in enumerate(rows, 1)) or "Hali o'yin yo'q.") + f"\n⏳ {wk_left() // 3600} soat qoldi")
    elif k == "me": await out(ptext(ADMIN, c.from_user.full_name))


async def gift_cmd(m, kind):  # guruhda xabarga javob qilib: /money 5 yoki /olmos 5
    try: n = int(m.text.split()[1])
    except Exception: return await m.answer("Birovning xabariga javob qilib yozing: /money 5 yoki /olmos 5")
    rp, a = m.reply_to_message, m.from_user
    if not rp or not rp.from_user or rp.from_user.is_bot or rp.from_user.id == a.id or n <= 0:
        return await m.answer("Birovning xabariga javob qilib yozing. Masalan: /money 5")
    to = rp.from_user.id; touch(a.id, a.full_name[:14]); touch(to, rp.from_user.full_name[:14]); inv(a.id); inv(to)
    ok = db.execute("UPDATE inv SET dia=dia-? WHERE uid=? AND dia>=?", (n, a.id, n)).rowcount if kind == "dia" else db.execute("UPDATE st SET coins=coins-? WHERE uid=? AND coins>=?", (n, a.id, n)).rowcount
    if not ok: db.commit(); return await m.answer("❌ Hisobingizda yetarli " + ("olmos" if kind == "dia" else "tanga") + " yo'q")
    db.execute("UPDATE inv SET dia=dia+? WHERE uid=?", (n, to)) if kind == "dia" else db.execute("UPDATE st SET coins=coins+? WHERE uid=?", (n, to)); db.commit()
    await m.answer(f"🎁 {ml(a.id, a.full_name)} → {ml(to, rp.from_user.full_name)} ga {n} ta {'💎 olmos' if kind == 'dia' else '🪙 tanga'} sovg'a qildi!", parse_mode="HTML")
    await refresh(a.id); await refresh(to)


@dp.message(Command("money"))
async def money_cmd(m: Message): await gift_cmd(m, "coin")


@dp.message(Command("olmos"))
async def olmos_cmd(m: Message): await gift_cmd(m, "dia")


@dp.callback_query(F.data.startswith("pr:"))
async def pair_cb(c: CallbackQuery):  # para taklifiga javob
    _, a, frm = c.data.split(":"); frm = int(frm); me = c.from_user.id
    await c.answer()
    if a == "y":
        x, y = prof(frm), prof(me)
        if x["pid"] or y["pid"] or not x["g"] or not y["g"] or x["g"] == y["g"]: return await c.message.edit_text("❌ Bu taklif endi amal qilmaydi.")
        for u, p in ((frm, me), (me, frm)): db.execute("INSERT INTO prof(uid,partner) VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET partner=?", (u, p, p))
        db.commit(); touch(me, c.from_user.full_name[:14]); await c.message.edit_text("💖 Siz juft bo'ldingiz!")
        try: await bot.send_message(frm, f"💖 {c.from_user.full_name} taklifingizni qabul qildi! Endi siz juftsiz.")
        except Exception: pass
        await refresh(frm); await refresh(me)
    else:
        await c.message.edit_text("❌ Rad etildi.")
        try: await bot.send_message(frm, f"💔 {c.from_user.full_name} taklifingizni rad etdi.")
        except Exception: pass


@dp.message(Command("meninghayotim"))
async def myhayot(m: Message):
    p = prof(m.from_user.id)
    if p["pid"]: await m.answer(f"♥️{ml(m.from_user.id, m.from_user.full_name)} {ml(p['pid'], p['pn'] or str(p['pid']))}💝", parse_mode="HTML")
    else: await m.answer("💔 Sizda hali para yo'q. Ilovada Profil bo'limidan para taklif qiling.")


# ❌⭕ X O: faqat ilovada, 2 kishilik, taklif bilan (olmos/tanga berilmaydi)
xo: dict = {}


def xo_win(b):
    for a, c, d in ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)):
        if b[a] and b[a] == b[c] == b[d]: return b[a]
    return "d" if all(b) else None


async def xo_push(g):
    for uid, ws in list(g["ws"].items()):
        try: await ws.send_json({"board": g["board"], "turn": g["turn"], "you": "X" if uid == g["x"] else "O", "st": g["st"], "w": g["w"], "nm": g["nm"], "left": g["left"]})
        except Exception: g["ws"].pop(uid, None)


async def xo_expire(code):  # 1 daqiqada javob bo'lmasa taklif rad etiladi
    await asyncio.sleep(60)
    g = xo.get(code)
    if g and g["st"] == "wait":
        g["st"] = "expired"; await xo_push(g)
        try: await bot.send_message(g["x"], "⌛ Taklifingiz rad etildi (1 daqiqa ichida javob bo'lmadi). Boshqa odam bilan o'ynang.")
        except Exception: pass


@app.post("/xo/invite")
async def xo_invite(req: Request):
    d = await req.json(); u = auth(d.get("init", ""))
    try: to = int(d.get("to"))
    except Exception: return {"error": "ID noto'g'ri"}
    if not u or to == u["id"]: return {"error": "ID noto'g'ri"}
    code = "".join(random.choices("abcdefghjkmnpqrstuvwxyz23456789", k=6)); name = u.get("first_name", "?")[:14]
    try:
        await bot.send_message(to, f"🎮 <a href='tg://user?id={u['id']}'>{html.escape(name)}</a> sizni X O o'yiniga taklif qilmoqda!", parse_mode="HTML",
                               reply_markup=InlineKeyboardMarkup(inline_keyboard=[[B(text="▶️ Kirish", url=f"https://t.me/{ME}/{APP}?startapp=xo_{code}"), B(text="❌ Rad etish", callback_data=f"xo:no:{code}")]]))
    except Exception: return {"error": "Bu foydalanuvchi botga /start bosmagan yoki ID xato"}
    xo[code] = {"x": u["id"], "o": to, "st": "wait", "board": [""] * 9, "turn": "X", "w": None, "ws": {}, "nm": {}, "left": None}
    asyncio.create_task(xo_expire(code))
    return {"code": code}


@dp.callback_query(F.data.startswith("xo:no:"))
async def xo_no(c: CallbackQuery):
    code = c.data[6:]; g = xo.get(code); await c.answer()
    if g and g["st"] == "wait" and c.from_user.id == g["o"]:
        g["st"] = "declined"; await xo_push(g)
        try: await bot.send_message(g["x"], f"❌ {c.from_user.full_name} taklifingizni rad etdi. Boshqa odam bilan o'ynang.")
        except Exception: pass
    try: await c.message.edit_text("❌ Rad etildi.")
    except Exception: pass


@app.websocket("/xows")
async def xo_ws(ws: WebSocket, init: str, code: str):
    await ws.accept(); u = auth(init); g = xo.get(code)
    if not u or not g or u["id"] not in (g["x"], g["o"]): return await ws.close()
    uid = u["id"]; g["nm"][str(uid)] = pname(uid, u.get("first_name", "?")[:14]); g["ws"][uid] = ws
    if uid == g["o"] and g["st"] == "wait": g["st"] = "play"
    await xo_push(g)
    try:
        while True:
            m = await ws.receive_json(); t = m.get("t")
            if t == "move" and g["st"] == "play":
                i, me = m.get("i"), ("X" if uid == g["x"] else "O")
                if isinstance(i, int) and 0 <= i < 9 and not g["board"][i] and g["turn"] == me:
                    g["board"][i] = me; w = xo_win(g["board"])
                    if w: g["st"], g["w"] = "end", w
                    else: g["turn"] = "O" if me == "X" else "X"
                    await xo_push(g)
            elif t == "again" and g["st"] == "end": g.update(board=[""] * 9, turn="X", w=None, st="play", left=None); await xo_push(g)
            elif t == "leave": g["st"], g["w"], g["left"] = "end", "left", g["nm"].get(str(uid)); await xo_push(g); break
    except WebSocketDisconnect: pass
    g["ws"].pop(uid, None)
    if g["st"] == "play" and g["ws"]: g["st"], g["w"], g["left"] = "end", "left", g["nm"].get(str(uid)); await xo_push(g)
    if not g["ws"]: xo.pop(code, None)


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
        elif a == "tnew":
            price, mn, hrs = map(int, m.text.split())
            if trn_open_id(): raise ValueError("Ochiq turnir bor, avval tugating")
            db.execute("INSERT INTO trn(price,min,end_ts,status) VALUES(?,?,?,'open')", (price, mn, int(time.time() + hrs * 3600))); db.commit()
            await m.answer(f"✅ Turnir boshlandi: {price} ⭐, kamida {mn} kishi, {hrs} soat.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [B(text="📢 Hammaga e'lon qilish", callback_data="adm:tann")], [B(text="⬅️ Panel", callback_data="adm:home")]]))
        elif a == "bc":
            bc["t"] = m.text; n, = db.execute("SELECT COUNT(*) FROM st").fetchone()
            await m.answer(f"📢 Xabar {n} kishiga yuboriladi:\n\n{m.text}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [B(text="✅ Yuborish", callback_data="adm:bcgo"), B(text="❌ Bekor", callback_data="adm:home")]]))
    except Exception as e: await m.answer(f"Xato: {e}", reply_markup=BACK)


app.mount("/", StaticFiles(directory="static", html=True))
