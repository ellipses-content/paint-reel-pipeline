import anthropic

client = anthropic.Anthropic()

# One fixed visual definition of the cryptid per topic. The pipeline draws this
# creature ONCE and reuses that exact image in every panel (see image_generator),
# so the monster can never drift or grow extra limbs mid-video. This description
# both drives that single drawing and keeps the look intentional and menacing.
CREATURE_SYSTEM = """You design ONE fixed visual look for a cryptid that will be drawn a single time and reused in every panel of a short video.

Output a single short description (one sentence, ~25 words) of how the creature LOOKS, and nothing else. It must:
- Be concrete and specific: body shape, main color, head/face, eyes, teeth or claws, and 1-2 distinctive features.
- Give the creature a clear, simple, drawable anatomy (one head, a sensible number of limbs) — nothing convoluted.
- Make it menacing and scary — never cute or friendly.
- Describe ONLY the creature itself: no scene, no background, no people, no art style.
- Never mention text, words, or letters.

Return only the description, no preamble."""


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


SCENE_SYSTEM = """You turn a horror narration line into a short visual description of ONE scene showing a cryptid, for an image generator.

The creature's fixed look is given to you. Describe the SAME creature in a pose / action / setting that fits this narration beat — what it is doing and simple surroundings.

Rules:
- Keep the creature's given look. Show only ONE creature, with a SINGLE head.
- NO people or human figures (they render with extra arms/limbs). NO text, letters, or signs. NO arrows.
- One clear creature, one focal point, simple scenery.

Return only the scene description, no preamble."""


def generate_scene_prompt(scene_text: str, topic: str, scene_index: int,
                          creature_design: str) -> str:
    """Build one scene's image prompt: the fixed creature + a story-fit pose."""
    message = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=200,
        system=SCENE_SYSTEM,
        messages=[
            {"role": "user",
             "content": (
                 f"Topic: {topic}\n"
                 f"The creature (fixed look): {creature_design}\n"
                 f"Scene {scene_index} narration: {scene_text}\n\n"
                 f"Describe this scene."
             )}
        ],
    )
    action = message.content[0].text.strip()
    return (
        f"{creature_design}, {action}. One creature only, exactly one head, "
        "no people, no text, no arrows"
    )


def generate_all_prompts(script: list[dict], topic: str) -> list[dict]:
    """Design one canonical creature, then a per-scene image prompt for it.

    Each scene draws the same creature in a story-fit pose; image_generator uses
    a reference + vision filter to keep those drawings on-model.
    """
    creature_design = generate_creature_design(topic)
    print(f"  [creature] {creature_design}")

    result = []
    for i, block in enumerate(script):
        prompt = generate_scene_prompt(block["text"], topic, i, creature_design)
        result.append({
            **block,
            "creature_design": creature_design,
            "image_prompt": prompt,
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