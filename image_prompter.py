import anthropic

client = anthropic.Anthropic()

SYSTEM_PROMPT = """You convert horror narration lines into a simple visual SCENE description for an image generator.

DESCRIBE ONLY WHAT IS SHOWN (not how it is drawn):
- The subjects and the cryptid/creature, what they look like in plain terms
- Make the creature look menacing and scary: sharp teeth, claws, mean or
  empty staring eyes, looming over the humans. Never friendly or cute.
- Human characters are simple stick figures with round heads
- Their poses, gestures, and basic expressions (terrified, fleeing, cowering)
- The spatial layout: who is where, what is big vs small
- A simple, near-empty setting — just enough to place the scene

DO NOT specify art style, medium, colors, line quality, shading, or rendering.
The drawing style is applied automatically afterward, so describing it here only
fights that style. Just describe the scene plainly.

COMPOSITION RULES:
- Horizontal 16:9 widescreen
- One clear focal point per image, centered, with lots of empty space around it
- A red arrow pointing at the subject when it helps direct attention

NO TEXT (critical):
- Never include words, letters, numbers, captions, labels, signs, or speech bubbles
- The image model renders any text as misspelled garbled nonsense, so never request it
- Do not describe anything that bears writing (no books, signs, banners, labels)
- Convey the idea through the scene alone — never through written words

OUTPUT FORMAT:
Return only the scene description. No explanation. No preamble. Just the text.
It must never ask for text, words, or letters in the image."""

def generate_image_prompt(scene_text: str, topic: str, scene_index: int) -> str:
    """Convert a narration line into a plain visual scene description.

    Style is applied downstream in image_generator (STYLE_PREFIX), so this only
    captures what the scene depicts.
    """
    message = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Topic: {topic}\nScene {scene_index}: {scene_text}\n\nDescribe the scene for this narration line."
            }
        ]
    )
    return message.content[0].text.strip()


def generate_all_prompts(script: list[dict], topic: str) -> list[dict]:
    """Add image_prompt to each script block."""
    result = []
    for i, block in enumerate(script):
        prompt = generate_image_prompt(block["text"], topic, i)
        result.append({
            **block,
            "image_prompt": prompt
        })
        print(f"  [prompt {i+1}/{len(script)}] generated")
    return result


if __name__ == "__main__":
    import json, sys
    from script_writer import generate_script

    topic = sys.argv[1] if len(sys.argv) > 1 else "Skinwalker"
    script = generate_script(topic)
    enriched = generate_all_prompts(script, topic)
    print(json.dumps(enriched, indent=2))
