import re

from text_client import generate_text

SYSTEM_PROMPT = """You write short horror narration scripts for a YouTube Shorts series called "Cryptid Files" about a given cryptid.

Write about 120-140 words of narration total, split into 8 to 10 short beats.
Tone: slow-burn dread, eerie, true-crime-meets-folklore. Never say "subscribe" or "like". End on an unresolved, haunting note.

FORMAT — output ONLY the beats, exactly like this, nothing else:
- Each beat is a timestamp in square brackets on its own line (seconds, starting at 0, increasing, ending near 58),
- then the narration sentence on the very next line,
- then one blank line.

Do NOT include any titles, notes, explanations, reasoning, or the word "narration" — output only real narration sentences about the cryptid.

Example of the shape (write your OWN sentences about the cryptid, do not copy these):

[0]
In the swamps of Louisiana, something has been watching the treeline for a hundred years.

[8]
The first hunters who saw it never spoke of it again.

[16]
They said it stood upright, taller than any man.

Now write the script for the cryptid the user names."""

def generate_script(topic: str) -> list[dict]:
    """Returns list of {time: int, text: str} dicts."""
    raw = generate_text(
        SYSTEM_PROMPT,
        f"Write a Cryptid Files script about: {topic}",
        max_tokens=1024,
        temperature=0.9,
    )

    blocks = re.findall(r'\[(\d+)\]\s*\n(.*?)(?=\n\s*\[|\Z)', raw, re.DOTALL)

    result = []
    for time_str, text in blocks:
        result.append({
            "time": int(time_str),
            "text": text.strip()
        })

    return result


if __name__ == "__main__":
    import json, sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "Skinwalker"
    script = generate_script(topic)
    print(json.dumps(script, indent=2))
