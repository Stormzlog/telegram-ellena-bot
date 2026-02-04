import asyncio
import random
import re

def _emoji_count(s: str) -> int:
    return len(re.findall(r"[\U0001F300-\U0001FAFF]", s or ""))

async def human_delay(user_text: str, reply_text: str = ""):
    """
    Human-ish delay based on:
    - user message length
    - reply length
    - emojis
    - randomness
    """
    u = user_text or ""
    r = reply_text or ""

    base = random.uniform(0.35, 1.10)
    u_len = min(len(u) / 140, 2.2)
    r_len = min(len(r) / 180, 2.6)
    emo = min(_emoji_count(u) * 0.10, 0.6)

    # short chats feel faster
    if len(u.strip()) <= 7:
        base *= 0.75

    delay = base + u_len * 0.55 + r_len * 0.65 + emo
    delay = max(0.25, min(delay, 7.5))
    await asyncio.sleep(delay)
