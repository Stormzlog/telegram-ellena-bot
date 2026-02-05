import re
from typing import Dict, Any

EXPLICIT_WORDS = {"fuck", "pussy", "dick", "blowjob", "cum", "nude", "naked", "sex"}

def evaluate_safety(user_text: str, state: Dict[str, Any], signal: Dict[str, Any]) -> Dict[str, Any]:
    raw = (user_text or "").strip().lower()
    tension = float(signal.get("tension", 0.15))
    rel = state.get("relationship", "warm")
    flirt = bool(state.get("flirt", True))

    # Simple loop score update (rises when tension stays high)
    loop = int(state.get("negative_loop_score", 0))
    if tension >= 0.65:
        loop = min(10, loop + 2)
    elif tension >= 0.40:
        loop = min(10, loop + 1)
    else:
        loop = max(0, loop - 1)
    state["negative_loop_score"] = loop

    # Explicit handling
    has_explicit = any(w in raw.split() for w in EXPLICIT_WORDS)

    # Determine mode
    mode = "normal"
    if loop >= 6 or tension >= 0.75:
        mode = "deescalate"
    if has_explicit and (not flirt or rel == "new"):
        mode = "boundary"

    # Output directives
    return {
        "mode": mode,
        "pace": "slow" if mode in {"deescalate", "boundary"} else ("fast" if signal.get("energy", 0.4) > 0.7 else "normal"),
        "force_concise": mode in {"deescalate", "boundary"},
        "no_teasing": mode in {"deescalate", "boundary"},
    }
