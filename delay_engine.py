import asyncio, random

async def human_delay(text):
    base = random.uniform(0.4, 1.2)
    length = min(len(text) / 120, 2.5)
    emoji = text.count("😂") * 0.2
    await asyncio.sleep(base + length + emoji)
