import anthropic

client = anthropic.Anthropic()

# Designed once per topic: a fixed visual definition of the cryptid so it can be
# drawn the SAME in every panel. Consistency across panels was the big win of the
# old flat style; injecting one canonical description into every scene prompt
# (plus a shared seed in image_generator) recreates it.
CREATURE_SYSTEM = """You design ONE fixed visual look for a cryptid that will be drawn identically in every panel of a short video.

Output a single short description (one sentence, ~25 words) of how the creature LOOKS, and nothing else. It must:
- Be concrete and specific enough to redraw identically: body shape, main color, head/face, eyes, teeth or claws, and 1-2 distinctive features.
- Make the creature menacing and scary — never cute or friendly.
- Describe ONLY the creature itself: no scene, no background, no people, no art style.
- Never mention text, words, or letters.

Return only the description, no preamble."""

# Per-panel: describe only what HAPPENS. The creature's appearance is fixed and
# supplied separately, so Claude must not redescribe it (that would let it drift).
SCENE_SYSTEM = """You turn a horror narration line into a simple SCENE description for ONE panel of a cryptid video.

The creature's appearance is ALREADY decided and given to you. Do NOT redescribe what the creature looks like. Describe only what is HAPPENING in this panel:
- What the creature is doing — its pose, action, and where it is
- Human characters are simple stick figures with round heads; give their poses and expressions (terrified, fleeing, cowering)
- The layout: who is where, what is big vs small, one clear focal point with lots of empty space
- A red arrow pointing at the creature when it helps direct attention

DO NOT specify art style, colors, line quality, or rendering — that is applied separately.

NO TEXT (critical): never request words, letters, numbers, captions, labels, signs, or speech bubbles. Do not describe anything bearing writing. Convey everything through the scene alone.

OUTPUT: return only the scene action description. No preamble."""


def generate_creature_design(topic: str) -> str:
    """One canonical, reusable visual description of the cryptid for `topic`."""
    message = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=120,
        system=CREATURE_SYSTEM,
        messages=[
            {"role": "user",
             "content": f"Cryptid: {topic}\n\nDescribe how this creature looks."}
        ],
    )
    return message.content[0].text.strip()


def generate_image_prompt(scene_text: str, topic: str, scene_index: int,
                          creature_design: str) -> str:
    """Build one panel's image prompt: the fixed creature look + the scene action.

    Art style is applied downstream in image_generator (STYLE_PREFIX); the
    creature look is fixed here so every panel draws the same monster.
    """
    message = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=300,
        system=SCENE_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Topic: {topic}\n"
                    f"The creature (fixed look — do NOT redescribe it): {creature_design}\n"
                    f"Scene {scene_index} narration: {scene_text}\n\n"
                    f"Describe what happens in this panel."
                ),
            }
        ],
    )
    action = message.content[0].text.strip()
    return f"{creature_design}. Scene: {action}"


def generate_all_prompts(script: list[dict], topic: str) -> list[dict]:
    """Add image_prompt (and the shared creature_design) to each script block."""
    creature_design = generate_creature_design(topic)
    print(f"  [creature] {creature_design}")

    result = []
    for i, block in enumerate(script):
        prompt = generate_image_prompt(block["text"], topic, i, creature_design)
        result.append({
            **block,
            "image_prompt": prompt,
            "creature_design": creature_design,
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