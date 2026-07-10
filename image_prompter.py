from text_client import generate_text

# One canonical creature description per topic, injected into every scene prompt
# so the same creature (roughly) recurs. Free tools can't hold it perfectly
# consistent, but a shared description + shared seed keeps it close.
CREATURE_SYSTEM = """You design ONE fixed visual look for a cryptid for a short horror video.

Output a single short description (one sentence, ~25 words) of how the creature LOOKS, and nothing else. It must:
- Be concrete and specific: body shape, main color, head/face, eyes, teeth or claws, and 1-2 distinctive features.
- Give it a clear, simple anatomy: ONE head, a sensible number of limbs, no extra eyes/horns/tails.
- Make it menacing and scary — never cute.
- Describe ONLY the creature: no scene, no background, no people, no art style.
- Never mention text, words, or letters.

Return only the description, no preamble."""

# Per-scene: describe what the creature is doing this beat. No people (AI draws
# them with extra limbs), no text, no arrows.
SCENE_SYSTEM = """You turn a horror narration line into a short visual description of ONE scene showing a cryptid, for an image generator.

The creature's fixed look is given to you. Describe the SAME creature in a pose / action / simple setting that fits this narration beat.

Rules:
- Keep the creature's given look. Show only ONE creature, with a SINGLE head.
- NO people or human figures. NO text, letters, or signs. NO arrows.
- One clear creature, one focal point, simple scenery.

Return only the scene description, no preamble."""


def generate_creature_design(topic: str) -> str:
    """One canonical, reusable visual description of the cryptid for `topic`."""
    return generate_text(
        CREATURE_SYSTEM,
        f"Cryptid: {topic}\n\nDescribe how this creature looks.",
        max_tokens=400,
        temperature=0.7,
    )


def generate_scene_prompt(scene_text: str, topic: str, scene_index: int,
                          creature_design: str) -> str:
    """Build one scene's image prompt: the fixed creature + a story-fit pose."""
    action = generate_text(
        SCENE_SYSTEM,
        (f"Topic: {topic}\n"
         f"The creature (fixed look): {creature_design}\n"
         f"Scene {scene_index} narration: {scene_text}\n\n"
         f"Describe this scene."),
        max_tokens=400,
        temperature=0.8,
    )
    return (
        f"{creature_design}, {action}. One creature only, exactly one head, "
        "no people, no text"
    )


def generate_all_prompts(script: list[dict], topic: str) -> list[dict]:
    """Design one canonical creature, then a per-scene image prompt for it."""
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
