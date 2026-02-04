import random

def apply_style(text: str, linebreak_level: float = 0.75) -> str:
    """
    Keep it short, line-broken, casual.
    """
    if not text:
        return "Ok"

    # Occasionally split into two lines if it's a bit long
    if len(text) > 45 and "\n" not in text and random.random() < linebreak_level:
        mid = len(text) // 2
        return text[:mid].rstrip() + "\n\n" + text[mid:].lstrip()

    return text
