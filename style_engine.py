import random

def apply_style(text):
    if len(text) > 60 and random.random() > 0.6:
        mid = len(text)//2
        return text[:mid] + "\n\n" + text[mid:]
    return text
