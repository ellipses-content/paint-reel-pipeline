import anthropic
import re

client = anthropic.Anthropic()

SYSTEM_PROMPT = """You are a master horror storyteller writing scripts for a YouTube Shorts series called "Cryptid Files."
Your scripts are cinematic, atmospheric, and deeply unsettling. You write for a faceless narration channel.

Script rules:
- Exactly 60 seconds when read aloud at a moderate pace (~130 words)
- Format: each line starts with a timestamp in seconds, followed by a pipe, followed on a new line with the narration
- Structure: Hook (0s) → Historical context (10s) → Escalation (30s) → Unsettling close (50s)
- Tone: slow-burn dread, not cheesy. Think true crime meets folklore
- Never say "subscribe" or "like"
- End on an open, haunting note — no resolution

Timestamp format (strict):
[0]
Narration line here.

[7]
Next narration line here.

[12]
Continue the story.

Generate 8-10 timestamp blocks covering the full 60 seconds."""

def generate_script(topic: str) -> list[dict]:
    """Returns list of {time: int, text: str} dicts."""
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"Write a Cryptid Files script about: {topic}"}
        ]
    )

    raw = message.content[0].text
    blocks = re.findall(r'\[(\d+)\]\n(.*?)(?=\n\[|\Z)', raw, re.DOTALL)

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
