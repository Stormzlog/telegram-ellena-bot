import re
import time
import random
from collections import defaultdict, deque
from typing import Dict, Any, List, Tuple

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, CommandHandler, filters

from config import BOT_TOKEN, ADMIN_ID
from bot_db import (
    init_db,
    get_profile, set_profile,
    ensure_user, bump_user,
    get_state, set_state,
    add_pair, find_pair,
    reset_user,
    clear_pairs, count_pairs,
    DEFAULT_PROFILE,
)
from delay_engine import human_delay
from style_engine import apply_style

# -------------------------
# Global runtime switches
# -------------------------
PAUSED_GLOBAL = False

# Anti-spam
_last_ts = defaultdict(float)
_burst = defaultdict(lambda: deque(maxlen=8))

# Vibe dictionaries
SAD_WORDS = {"sad", "tired", "lonely", "depressed", "cry", "hurt", "stress", "stressed", "down", "broken"}
SWEET_WORDS = {"miss", "missed", "love", "baby", "babe", "sweet", "honey", "darling", "cute"}
ANGRY_WORDS = {"angry", "mad", "annoyed", "pissed", "hate"}

# Keep “naughty” suggestive, not explicit
EXPLICIT_WORDS = {"fuck", "pussy", "dick", "blowjob", "cum", "nude", "naked", "sex"}

def is_admin(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == ADMIN_ID)

def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def _keywords(text: str) -> List[str]:
    t = re.sub(r"[^a-zA-Z0-9\s']", " ", text or "").lower()
    words = [w for w in t.split() if len(w) >= 3]
    return words[:6]

def _make_key_phrase(user_line: str) -> str:
    words = _keywords(user_line)
    return " ".join(words[:5]).strip()

def detect_vibe(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()
    t = raw.lower()
    words = re.findall(r"[a-z']+", t)
    wset = set(words)

    vibe = "playful"
    if wset & SAD_WORDS:
        vibe = "soft"
    elif wset & ANGRY_WORDS:
        vibe = "serious"
    elif wset & SWEET_WORDS:
        vibe = "romantic"
    elif "?" in raw:
        vibe = "curious"

    energy = 0.45
    energy += min(0.25, raw.count("!") * 0.06)
    energy += 0.10 if len(raw) <= 7 else 0.0
    energy = clamp01(energy)

    return {"vibe": vibe, "energy": energy}

def pick_not_repeat(options: List[str], last: List[str]) -> str:
    last_set = set(last[-10:])
    pool = [o for o in options if o not in last_set]
    return random.choice(pool) if pool else random.choice(options)

def maybe_emoji(profile: Dict[str, Any], intensity: float = 1.0) -> str:
    if random.random() < profile.get("emoji_level", 0.65) * intensity:
        return random.choice(profile.get("fav_emojis", ["😂"]))
    return ""

def lb(profile: Dict[str, Any]) -> str:
    return "\n\n" if random.random() < profile.get("linebreak_level", 0.75) else " "

def energy_pack(profile: Dict[str, Any], mode: str, relationship: str, flirt: bool) -> Dict[str, List[str]]:
    # relationship stages change warmth + closeness
    if relationship == "new":
        reacts = ["Hi", "Hey", "Okay", "Hmm", "Really"]
        endings = ["Tell me", "Wym", "Go on"]
    elif relationship == "close":
        reacts = ["Awwwn", "Okayyy", "Hehe", "Mhm", "Lol", "Yay"]
        endings = ["Tell me baby", "Come here", "Go on", "And then😂", "Say more"]
    else:  # warm
        reacts = profile.get("fav_reacts", ["Okayyy", "Awwwn", "Lol", "Mhm"])
        endings = profile.get("fav_endings", ["Tell me", "Go on", "And then😂", "Wym😂"])

    if mode == "soft":
        endings = ["Talk to me", "I’m listening", "What happened", "Come here"]
    if mode == "serious":
        endings = ["Explain it", "Tell me properly", "What happened", "Be real with me"]

    if not flirt:
        # reduce teasing
        endings = [e.replace("baby", "").strip() for e in endings]
        endings = [e for e in endings if e]

    return {"reacts": reacts, "endings": endings}

def generate_reply(user_text: str, state: Dict[str, Any], profile: Dict[str, Any]) -> str:
    raw = (user_text or "").strip()
    t = raw.lower()
    last = state.get("last_replies", [])

    # explicit handling (keep it “naughty” but not graphic)
    if any(w in t.split() for w in EXPLICIT_WORDS):
        if state.get("flirt", True) and state.get("relationship") in ("warm", "close"):
            return pick_not_repeat([
                f"Stoppp😂{lb(profile)}You’re wild😏",
                f"Hehe😏{lb(profile)}Keep it cute😂",
                f"Okayyy😏{lb(profile)}Not too much now😂",
            ], last)
        return pick_not_repeat([
            f"Lol😂{lb(profile)}Let’s chill",
            f"Hmm{lb(profile)}Nope",
            f"Okayyy😂{lb(profile)}Not that",
        ], last)

    # learned pair match
    learned = find_pair(raw)
    if learned:
        if random.random() < 0.35:
            react = pick_not_repeat(profile.get("fav_reacts", ["Okayyy"]), last)
            learned = f"{react}{maybe_emoji(profile, 0.9)}{lb(profile)}{learned}"
        return learned

    # vibe detection + lock
    dv = detect_vibe(raw)
    user_vibe = dv["vibe"]

    forced_mode = state.get("mode")  # None=auto
    mood_locked = bool(state.get("mood_locked", False))
    if forced_mode:
        mode = forced_mode
    else:
        if mood_locked:
            mode = state.get("last_mode") or user_vibe
        else:
            mode = user_vibe
            state["last_mode"] = mode

    flirt = bool(state.get("flirt", True))
    relationship = state.get("relationship", "warm")

    pack = energy_pack(profile, mode, relationship, flirt)
    reacts = pack["reacts"]
    endings = pack["endings"]

    # greetings
    if re.search(r"\b(hi|hey|hello|hii|heyy|yo)\b", t):
        return pick_not_repeat([
            "Heyy" + maybe_emoji(profile, 1.0),
            "Hii" + maybe_emoji(profile, 1.0),
            "Hi hi" + maybe_emoji(profile, 1.0),
        ], last)

    # how are you
    if "how are you" in t or "how r you" in t or "how you" in t:
        return pick_not_repeat([
            f"Good{maybe_emoji(profile,1.0)}{lb(profile)}You",
            f"Chilling{maybe_emoji(profile,1.0)}{lb(profile)}Wbu",
            f"I’m okay{lb(profile)}You good{maybe_emoji(profile,0.9)}",
        ], last)

    # missed you
    if "miss you" in t or "missed you" in t:
        if relationship == "new":
            return pick_not_repeat([
                f"Aww{maybe_emoji(profile,1.0)}{lb(profile)}That’s sweet",
                f"Hehe{maybe_emoji(profile,1.0)}{lb(profile)}Tell me more",
            ], last)
        return pick_not_repeat([
            f"Awwwn{maybe_emoji(profile,1.0)}{lb(profile)}I missed you too{lb(profile)}Where you been{maybe_emoji(profile,0.8)}",
            f"Hehe{maybe_emoji(profile,1.0)}{lb(profile)}Come here{lb(profile)}Tell me what’s up",
            f"Yay{maybe_emoji(profile,1.0)}{lb(profile)}I like that{lb(profile)}So how was your day{maybe_emoji(profile,0.7)}",
        ], last)

    # “can I tell you…”
    if "can i tell you" in t or "let me tell you" in t or "i want to tell you" in t:
        return pick_not_repeat([
            f"Yes pls{maybe_emoji(profile,1.0)}{lb(profile)}Tell me everything",
            f"Go onnn{maybe_emoji(profile,1.0)}{lb(profile)}I’m listening",
            f"Say it{lb(profile)}I’m here{maybe_emoji(profile,0.9)}",
        ], last)

    # questions
    if "?" in raw:
        react = pick_not_repeat([r + maybe_emoji(profile, 0.9) for r in reacts], last)
        end = random.choice(endings) + maybe_emoji(profile, 0.8)

        if mode == "shy":
            end = pick_not_repeat([
                f"Umm{maybe_emoji(profile,1.0)}",
                f"Tell me pls{maybe_emoji(profile,1.0)}",
                f"Wym{maybe_emoji(profile,1.0)}",
            ], last)
        if mode == "romantic" and flirt and relationship in ("warm", "close"):
            end = pick_not_repeat([
                f"Tell me baby{maybe_emoji(profile,0.9)}",
                f"Talk to me{maybe_emoji(profile,0.8)}",
                f"Come here{maybe_emoji(profile,0.8)}",
            ], last)

        return f"{react}{lb(profile)}{end}"

    # very short texts
    if len(t) <= 7:
        base = pick_not_repeat([
            f"Yep{maybe_emoji(profile,0.9)}",
            f"Really{maybe_emoji(profile,1.0)}",
            f"Okayyy{maybe_emoji(profile,1.0)}",
            f"Huh{maybe_emoji(profile,1.0)}",
            f"Lol{maybe_emoji(profile,1.0)}",
        ], last)

        if random.random() < 0.6:
            nudge = random.choice(endings) + maybe_emoji(profile, 0.7)
            return f"{base}{lb(profile)}{nudge}"
        return base

    # default: reaction + pull (alive)
    react_word = pick_not_repeat(reacts, last)
    react = react_word + maybe_emoji(profile, 0.9)

    playful_pulls = [
        f"And then{maybe_emoji(profile,0.9)}",
        f"So what happened next{maybe_emoji(profile,0.8)}",
        f"Tell me the full thing{maybe_emoji(profile,0.8)}",
        f"Go onnn{maybe_emoji(profile,1.0)}",
    ]
    if mode == "soft":
        playful_pulls = ["Talk to me", "I’m listening", "Come here", "What happened"]
    elif mode == "serious":
        playful_pulls = ["Explain it", "Tell me properly", "What’s the real issue", "Okay\n\nGo on"]
    elif mode == "shy":
        playful_pulls = [f"Hehe{maybe_emoji(profile,1.0)}{lb(profile)}Go on", f"Okayyy{maybe_emoji(profile,1.0)}{lb(profile)}Tell me"]
    elif mode == "romantic":
        playful_pulls = [f"Awwwn{maybe_emoji(profile,1.0)}{lb(profile)}Talk to me", f"Come here{maybe_emoji(profile,0.8)}{lb(profile)}Tell me"]

    pull = pick_not_repeat(playful_pulls, last)

    # extra spice (only if flirt + warm/close)
    if state.get("flirt", True) and relationship in ("warm", "close") and mode in ("playful", "romantic") and random.random() < profile.get("tease_level", 0.6):
        pull = pick_not_repeat([
            f"Okayyy😏{lb(profile)}You’re trouble😂",
            f"Stoppp😂{lb(profile)}Come closer😏",
            f"Hehe😏{lb(profile)}What you want from me😂",
        ], last)

    return f"{react}{lb(profile)}{pull}"


# -------------------------
# Admin Teaching Parser
# -------------------------
TRAIN_HELP = (
    "Teaching mode ✅\n\n"
    "Paste like this:\n"
    "U: hi\n"
    "ME: heyy😂\n\n"
    "U: i missed you\n"
    "ME: awwwn😂 come here\n\n"
    "I learn U -> ME pairs.\n"
)

def parse_training_block(block: str) -> List[tuple]:
    lines = [ln.strip() for ln in (block or "").splitlines() if ln.strip()]
    pairs = []
    last_u = None
    for ln in lines:
        if ln.lower().startswith("u:"):
            last_u = ln[2:].strip()
        elif ln.lower().startswith("me:") and last_u:
            me = ln[3:].strip()
            pairs.append((last_u, me))
            last_u = None
    return pairs


# -------------------------
# Message handler
# -------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PAUSED_GLOBAL
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    username = update.effective_user.username or ""
    text = update.message.text

    # Anti-spam
    now = time.time()
    _burst[chat_id].append(now)
    if len(_burst[chat_id]) >= 8 and (now - _burst[chat_id][0]) < 7:
        return
    if now - _last_ts[chat_id] < 0.20:
        return
    _last_ts[chat_id] = now

    ensure_user(chat_id, username)
    bump_user(chat_id, username)

    state = get_state(chat_id)
    profile = get_profile()

    # Teaching mode (admin only)
    if is_admin(update) and state.get("teach_on", False):
        pairs = parse_training_block(text)
        learned = 0
        for u, me in pairs:
            key = _make_key_phrase(u)
            if key:
                add_pair(key, me)
                learned += 1
        await update.message.reply_text(f"Learned {learned} ✅")
        return

    # Global pause blocks normal replies (admin still can command)
    if PAUSED_GLOBAL:
        return

    # Generate reply
    reply = generate_reply(text, state, profile)
    reply = apply_style(reply, profile.get("linebreak_level", 0.75))

    # Save last replies
    state["last_replies"] = (state.get("last_replies", []) + [reply])[-10:]
    set_state(chat_id, state)

    # Typing + delay
    await context.bot.send_chat_action(chat_id, ChatAction.TYPING)
    await human_delay(text, reply)

    await update.message.reply_text(reply)


# -------------------------
# Admin-only command gate
# -------------------------
async def require_admin(update: Update, cmd_name: str) -> bool:
    if is_admin(update):
        return True
    # Always reply so user knows it’s working (and controlled)
    await update.message.reply_text("Not allowed 😏")
    return False


# -------------------------
# Commands (ALL admin-only + always reply)
# -------------------------
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "ping"):
        return
    await update.message.reply_text("Working ✅")

async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PAUSED_GLOBAL
    if not await require_admin(update, "pause"):
        return
    PAUSED_GLOBAL = True
    await update.message.reply_text("Paused ✅")

async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global PAUSED_GLOBAL
    if not await require_admin(update, "resume"):
        return
    PAUSED_GLOBAL = False
    await update.message.reply_text("Resumed ✅")

async def cmd_teach_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "teach_on"):
        return
    chat_id = update.effective_chat.id
    st = get_state(chat_id)
    st["teach_on"] = True
    set_state(chat_id, st)
    await update.message.reply_text("Teaching ON ✅\n\n" + TRAIN_HELP)

async def cmd_teach_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "teach_off"):
        return
    chat_id = update.effective_chat.id
    st = get_state(chat_id)
    st["teach_on"] = False
    set_state(chat_id, st)
    await update.message.reply_text("Teaching OFF ✅")

async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "mode"):
        return
    chat_id = update.effective_chat.id
    st = get_state(chat_id)

    if not context.args:
        await update.message.reply_text("Use: /mode playful|shy|romantic|soft|serious|auto ✅")
        return

    m = context.args[0].strip().lower()
    if m == "auto":
        st["mode"] = None
        set_state(chat_id, st)
        await update.message.reply_text("Mode: AUTO ✅")
        return

    if m not in {"playful", "shy", "romantic", "soft", "serious"}:
        await update.message.reply_text("Mode options: playful, shy, romantic, soft, serious, auto ✅")
        return

    st["mode"] = m
    set_state(chat_id, st)
    await update.message.reply_text(f"Mode: {m.upper()} ✅")

async def cmd_flirt_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "flirt_on"):
        return
    chat_id = update.effective_chat.id
    st = get_state(chat_id)
    st["flirt"] = True
    set_state(chat_id, st)
    await update.message.reply_text("Flirt: ON ✅")

async def cmd_flirt_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "flirt_off"):
        return
    chat_id = update.effective_chat.id
    st = get_state(chat_id)
    st["flirt"] = False
    set_state(chat_id, st)
    await update.message.reply_text("Flirt: OFF ✅")

async def cmd_relationship(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "relationship"):
        return
    chat_id = update.effective_chat.id
    st = get_state(chat_id)

    if not context.args:
        await update.message.reply_text("Use: /relationship new|warm|close|reset ✅")
        return

    v = context.args[0].strip().lower()
    if v == "reset":
        st["relationship"] = "warm"
        set_state(chat_id, st)
        await update.message.reply_text("Relationship: reset → WARM ✅")
        return

    if v not in {"new", "warm", "close"}:
        await update.message.reply_text("Options: new, warm, close, reset ✅")
        return

    st["relationship"] = v
    set_state(chat_id, st)
    await update.message.reply_text(f"Relationship: {v.upper()} ✅")

async def cmd_lock_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "lock_mood"):
        return
    chat_id = update.effective_chat.id
    st = get_state(chat_id)
    st["mood_locked"] = True
    set_state(chat_id, st)
    await update.message.reply_text("Mood lock: ON ✅")

async def cmd_unlock_mood(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "unlock_mood"):
        return
    chat_id = update.effective_chat.id
    st = get_state(chat_id)
    st["mood_locked"] = False
    set_state(chat_id, st)
    await update.message.reply_text("Mood lock: OFF ✅")

async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "profile"):
        return
    p = get_profile()
    await update.message.reply_text(
        "Profile ✅\n"
        f"emoji_level={p.get('emoji_level')}\n"
        f"linebreak_level={p.get('linebreak_level')}\n"
        f"tease_level={p.get('tease_level')}\n"
        f"fav_emojis={' '.join(p.get('fav_emojis', []))}\n"
        f"fav_reacts={', '.join(p.get('fav_reacts', [])[:8])}\n"
        f"pairs={count_pairs()}"
    )

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "status"):
        return
    chat_id = update.effective_chat.id
    st = get_state(chat_id)
    await update.message.reply_text(
        "Status ✅\n"
        f"paused_global={PAUSED_GLOBAL}\n"
        f"mode={st.get('mode') or 'auto'}\n"
        f"flirt={'on' if st.get('flirt', True) else 'off'}\n"
        f"relationship={st.get('relationship','warm')}\n"
        f"mood_locked={'yes' if st.get('mood_locked', False) else 'no'}\n"
        f"teach_on={'yes' if st.get('teach_on', False) else 'no'}\n"
        f"pairs={count_pairs()}"
    )

async def cmd_reset_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "reset_chat"):
        return
    chat_id = update.effective_chat.id
    reset_user(chat_id)      # deletes user row
    ensure_user(chat_id, update.effective_user.username or "")
    await update.message.reply_text("Chat memory reset ✅")

async def cmd_clear_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "clear_pairs"):
        return
    clear_pairs()
    await update.message.reply_text("All taught pairs cleared ✅")

async def cmd_reset_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "reset_style"):
        return
    set_profile(dict(DEFAULT_PROFILE))
    await update.message.reply_text("Style profile reset ✅")

async def cmd_help_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_admin(update, "help_admin"):
        return
    await update.message.reply_text(
        "Admin Commands ✅\n\n"
        "/ping - test bot\n"
        "/pause /resume\n"
        "/teach_on /teach_off\n"
        "/mode playful|shy|romantic|soft|serious|auto\n"
        "/flirt_on /flirt_off\n"
        "/relationship new|warm|close|reset\n"
        "/lock_mood /unlock_mood\n"
        "/profile /status\n"
        "/reset_chat - reset this chat memory\n"
        "/clear_pairs - delete all taught pairs\n"
        "/reset_style - reset learned style profile\n"
        "\n" + TRAIN_HELP
    )

async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Always reply so you *see* it works
    if is_admin(update):
        await update.message.reply_text("Command not found 😅\nTry /help_admin")
    else:
        await update.message.reply_text("Not allowed 😏")


def main():
    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Admin-only commands
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))

    app.add_handler(CommandHandler("teach_on", cmd_teach_on))
    app.add_handler(CommandHandler("teach_off", cmd_teach_off))

    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("flirt_on", cmd_flirt_on))
    app.add_handler(CommandHandler("flirt_off", cmd_flirt_off))
    app.add_handler(CommandHandler("relationship", cmd_relationship))

    app.add_handler(CommandHandler("lock_mood", cmd_lock_mood))
    app.add_handler(CommandHandler("unlock_mood", cmd_unlock_mood))

    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("status", cmd_status))

    app.add_handler(CommandHandler("reset_chat", cmd_reset_chat))
    app.add_handler(CommandHandler("clear_pairs", cmd_clear_pairs))
    app.add_handler(CommandHandler("reset_style", cmd_reset_style))

    app.add_handler(CommandHandler("help_admin", cmd_help_admin))

    # Messages + unknown commands
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
