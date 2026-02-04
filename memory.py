import sqlite3
import json
import time

DB_PATH = "memory.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
c = conn.cursor()


def ensure_schema():
    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        username TEXT,
        first_seen REAL,
        last_seen REAL,
        interaction_count INTEGER,
        user_state TEXT,
        topic_weights TEXT,
        summary TEXT
    )
    """)
    conn.commit()


def get_or_create_user(chat_id: int, username: str):
    ensure_schema()
    c.execute("SELECT chat_id FROM users WHERE chat_id=?", (chat_id,))
    if not c.fetchone():
        c.execute(
            "INSERT INTO users VALUES (?,?,?,?,?,?,?,?)",
            (
                chat_id,
                username,
                time.time(),
                time.time(),
                0,
                json.dumps({"mood": "casual", "nsfw": False, "last_replies": []}),
                json.dumps({}),
                "",
            )
        )
        conn.commit()


def bump_interaction(chat_id: int):
    c.execute(
        "UPDATE users SET interaction_count = interaction_count + 1, last_seen=? WHERE chat_id=?",
        (time.time(), chat_id),
    )
    conn.commit()


def get_user_state(chat_id: int) -> dict:
    c.execute("SELECT user_state FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    if not row or not row[0]:
        return {"mood": "casual", "nsfw": False, "last_replies": []}
    try:
        return json.loads(row[0])
    except Exception:
        return {"mood": "casual", "nsfw": False, "last_replies": []}


def set_user_state(chat_id: int, state: dict):
    c.execute("UPDATE users SET user_state=? WHERE chat_id=?", (json.dumps(state), chat_id))
    conn.commit()


def append_last_reply(chat_id: int, reply: str, keep: int = 8):
    state = get_user_state(chat_id)
    last = state.get("last_replies", [])
    last.append(reply)
    state["last_replies"] = last[-keep:]
    set_user_state(chat_id, state)


def update_topic_weights(chat_id: int, text: str):
    """
    Lightweight topic tracking:
    - extracts keywords
    - increments weights
    - decays old weights slowly
    """
    c.execute("SELECT topic_weights FROM users WHERE chat_id=?", (chat_id,))
    row = c.fetchone()
    weights = {}
    if row and row[0]:
        try:
            weights = json.loads(row[0])
        except Exception:
            weights = {}

    # decay
    for k in list(weights.keys()):
        weights[k] *= 0.97
        if weights[k] < 0.15:
            del weights[k]

    words = [w.lower() for w in _keywords(text)]
    for w in words[:6]:
        weights[w] = float(weights.get(w, 0.0)) + 1.0

    c.execute("UPDATE users SET topic_weights=? WHERE chat_id=?", (json.dumps(weights), chat_id))
    conn.commit()


def _keywords(text: str):
    # simple keywords (skip tiny/common words)
    import re
    stop = {"the","and","a","an","to","of","in","on","for","with","is","am","are","i","you","me","my","your","it"}
    tokens = re.findall(r"[a-zA-Z']+", text or "")
    return [t for t in tokens if len(t) >= 3 and t.lower() not in stop]


def reset_user_memory(chat_id: int):
    c.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
    conn.commit()
