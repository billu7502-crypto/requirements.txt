import os
import sqlite3
import random
import string
from datetime import datetime
from telebot import TeleBot, types
import requests
import urllib.parse

# ---------------- CONFIG ----------------

BOT_TOKEN = "8282247701:AAHMYoquP4oFJg_D6D68I3BCMrlrIVke8xw"
BOT_USERNAME = "Osintinfopatcher_bot"

# IMPORTANT: keep as integer (negative channel ID)
ADMIN_CHANNEL = -1003174018278   # where service requests go

VPLINK_API_KEY = "bc7622086045fb1a6029b2c2df6f87deee61b71e"

DB_PATH = "bot.db"
CREDITS_PER_AD = 3
CREDITS_PER_REF = 1
SERVICE_COST = 3

# ---------------- REQUIRED CHANNELS ----------------
# Make sure bot is ADMIN in these channels to check membership
REQUIRED_CHANNELS = [
    {"id": -1003260452722, "link": "https://t.me/+tHcmKmgK_YQzMDdl"},
    {"id": -1003214602340, "link": "https://t.me/+zesWQUUOLuswNGM1"},
    {"id": -1003192654021, "link": "https://t.me/+R9RnWrIBEQQ2YmFl"},
]

# -----------------------------------------

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")

# ----------- DATABASE --------------------

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

conn = get_conn()
cur = conn.cursor()

cur.executescript("""
CREATE TABLE IF NOT EXISTS users(
   user_id INTEGER PRIMARY KEY,
   credits INTEGER DEFAULT 0,
   referred_by INTEGER,
   verified INTEGER DEFAULT 0,
   created_at TEXT
);

CREATE TABLE IF NOT EXISTS codes(
   code TEXT PRIMARY KEY,
   user_id INTEGER,
   used INTEGER DEFAULT 0,
   created_at TEXT
);
""")
conn.commit()

# -----------------------------------------

def create_user(uid):
    row = cur.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users(user_id, credits, created_at) VALUES(?,?,?)",
            (uid, 0, datetime.utcnow().isoformat())
        )
        conn.commit()

def get_user(uid):
    return cur.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()

# ---------------- CHANNEL GATE ----------------

def is_member(channel_id, uid):
    try:
        m = bot.get_chat_member(channel_id, uid)
        return m.status in ("member", "administrator", "creator")
    except Exception:
        return False

def check_join(uid):
    missing = []
    for ch in REQUIRED_CHANNELS:
        if not is_member(ch["id"], uid):
            missing.append(ch)
    return missing

# ------------------ AD CODE -------------------

def gen_code():
    return ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(16))

def create_ad_code(uid):
    code = gen_code()
    cur.execute(
        "INSERT INTO codes(code,user_id,created_at) VALUES(?,?,?)",
        (code, uid, datetime.utcnow().isoformat())
    )
    conn.commit()
    return code

def redeem_code(uid, code):
    row = cur.execute("SELECT * FROM codes WHERE code=?", (code,)).fetchone()
    if not row:
        return False, "❌ Invalid code."
    if row["used"] == 1:
        return False, "⚠️ Code already used."

    # Mark used and add credits to the returning user
    cur.execute("UPDATE codes SET used=1 WHERE code=?", (code,))
    cur.execute("UPDATE users SET credits = credits + ? WHERE user_id=?", (CREDITS_PER_AD, uid))
    conn.commit()
    return True, f"✅ +{CREDITS_PER_AD} credits added."

# ------------------ MENU ----------------------

def main_menu(cid, uid):
    user = get_user(uid)
    balance = user["credits"]

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Service Request", callback_data="service"))
    kb.add(types.InlineKeyboardButton(f"💳 Credits: {balance}", callback_data="noop"))
    kb.add(
        types.InlineKeyboardButton("➕ Get 1 credit free", callback_data="free1"),
        types.InlineKeyboardButton(f"🎬 Get {CREDITS_PER_AD} credits", callback_data="getad")
    )
    kb.add(types.InlineKeyboardButton("👥 Referral Link", callback_data="ref"))

    bot.send_message(cid, "<b>Main Menu</b>", reply_markup=kb)

# ---------------- START -----------------------

@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    create_user(uid)

    # deep-link check
    if " " in message.text:
        payload = message.text.split(" ", 1)[1]

        if payload.startswith("ad_"):
            code = payload.replace("ad_", "", 1)
            ok, msg = redeem_code(uid, code)
            bot.send_message(message.chat.id, msg)

        elif payload.startswith("ref_"):
            try:
                refid = int(payload.replace("ref_", "", 1))
                if refid != uid:
                    user = get_user(uid)
                    if user["referred_by"] is None:
                        cur.execute("UPDATE users SET referred_by=? WHERE user_id=?", (refid, uid))
                        conn.commit()
            except Exception:
                pass

    # show gate if not joined all
    missing = check_join(uid)
    if missing:
        join_kb = types.InlineKeyboardMarkup()
        for ch in missing:
            join_kb.add(types.InlineKeyboardButton("Join Channel", url=ch["link"]))
        join_kb.add(types.InlineKeyboardButton("✅ I joined", callback_data="verify_join"))
        bot.send_message(message.chat.id, "Please join all required channels.", reply_markup=join_kb)
        return

    main_menu(message.chat.id, uid)

# -------- VERIFY JOIN ----------

@bot.callback_query_handler(func=lambda c: c.data == "verify_join")
def verify(call):
    uid = call.from_user.id
    user_before = get_user(uid)
    missing = check_join(uid)

    if missing:
        bot.answer_callback_query(call.id, "Still missing channels.")
        return

    # Only reward referral when user becomes verified for the first time
    if user_before["verified"] == 0:
        cur.execute("UPDATE users SET verified=1 WHERE user_id=?", (uid,))
        # credit the referrer once
        if user_before["referred_by"]:
            cur.execute(
                "UPDATE users SET credits = credits + ? WHERE user_id=?",
                (CREDITS_PER_REF, user_before["referred_by"])
            )
        conn.commit()

    bot.answer_callback_query(call.id, "✅ Access Granted")
    main_menu(call.message.chat.id, uid)

# -------- CREDITS ----------

@bot.callback_query_handler(func=lambda c: c.data == "free1")
def free1(call):
    uid = call.from_user.id
    cur.execute("UPDATE users SET credits = credits + 1 WHERE user_id=?", (uid,))
    conn.commit()
    bot.answer_callback_query(call.id, "+1 credit added")
    main_menu(call.message.chat.id, uid)

@bot.callback_query_handler(func=lambda c: c.data == "getad")
def getad(call):
    uid = call.from_user.id
    code = create_ad_code(uid)

    # Final deep-link back to bot
    final = f"https://t.me/{BOT_USERNAME}?start=ad_{code}"

    # Build VPLink API call to get the shortened, ad-monetized link
    encoded_final = urllib.parse.quote(final, safe="")
    api_url = f"https://vplink.in/api?api={VPLINK_API_KEY}&url={encoded_final}"

    short_url = final  # fallback
    try:
        r = requests.get(api_url, timeout=12)
        data = r.json() if r.headers.get("content-type","").startswith("application/json") else {}
        # Try common fields from link shortener APIs
        short_url = (
            data.get("shortenedUrl")
            or data.get("short")
            or data.get("shorturl")
            or data.get("url")
            or short_url
        )
    except Exception:
        pass

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🎬 Watch Ad", url=short_url))

    bot.send_message(
        call.message.chat.id,
        "Watch ad → press the final “Get Link/Continue” → you’ll return to the bot and receive credits.",
        reply_markup=kb
    )

# -------- REFERRAL ----------

@bot.callback_query_handler(func=lambda c: c.data == "ref")
def ref(call):
    uid = call.from_user.id
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    bot.send_message(call.message.chat.id, f"Referral link:\n<code>{link}</code>")

# -------- SERVICE REQUEST ----------

@bot.callback_query_handler(func=lambda c: c.data == "service")
def service(call):
    uid = call.from_user.id
    user = get_user(uid)

    if user["credits"] < SERVICE_COST:
        bot.send_message(call.message.chat.id, "❌ Not enough credits.")
        return

    bot.send_message(
        call.message.chat.id,
        "✅ Enter your service request in one message.\n"
        "Format:\n"
        "• Your Telegram username for delivery\n"
        "• Target (phone with country code or vehicle number)\n\n"
        "Example:\n@myusername\n+911234567890"
    )

    bot.register_next_step_handler(call.message, take_request)

def take_request(message):
    uid = message.from_user.id
    user = get_user(uid)

    if user["credits"] < SERVICE_COST:
        bot.send_message(message.chat.id, "❌ You no longer have enough credits.")
        return

    # Deduct credits
    cur.execute("UPDATE users SET credits = credits - ? WHERE user_id=?", (SERVICE_COST, uid))
    conn.commit()

    bot.send_message(message.chat.id, "✅ Your request has been submitted. You will receive the result in ~15 minutes.")

    # Forward to admin channel
    try:
        bot.send_message(
            ADMIN_CHANNEL,
            f"📩 <b>New Service Request</b>\n"
            f"From user: <code>{uid}</code>\n\n"
            f"Request:\n{message.text}"
        )
    except Exception as e:
        # If forwarding fails, refund to be safe
        cur.execute("UPDATE users SET credits = credits + ? WHERE user_id=?", (SERVICE_COST, uid))
        conn.commit()
        bot.send_message(message.chat.id, "⚠️ Could not submit request right now. Your credits were refunded.")

# --------------- RUN -------------------

print("✅ Bot is running…")
bot.infinity_polling(skip_pending=True)
