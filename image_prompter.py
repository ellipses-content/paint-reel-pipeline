from text_client import generate_text

# One fixed visual definition of the cryptid per topic. The pipeline draws this
# creature ONCE and reuses that exact image in every panel (see image_generator),
# so the monster is identical everywhere and can never grow extra limbs mid-video.
CREATURE_SYSTEM = """You design ONE fixed visual look for a cryptid that will be drawn a single time and reused in every panel of a short video.

Output a single short description (one sentence, ~25 words) of how the creature LOOKS, and nothing else. It must:
- Be concrete and specific: body shape, main color, head/face, eyes, teeth or claws, and 1-2 distinctive features.
- Give it a clear, simple, drawable anatomy: ONE head, a sensible number of limbs, no extra eyes/horns/tails. Nothing convoluted.
- Make it menacing and scary — never cute or friendly.
- Describe ONLY the creature itself: no scene, no background, no people, no art style.
- Never mention text, words, or letters.

Return only the description, no preamble."""


def generate_creature_design(topic: str) -> str:
    """One canonical, reusable visual description of the cryptid for `topic`."""
    return generate_text(
        CREATURE_SYSTEM,
        f"Cryptid: {topic}\n\nDescribe how this creature looks.",
        max_tokens=120,
        temperature=0.7,
    )


def generate_all_prompts(script: list[dict], topic: str) -> list[dict]:
    """Attach the shared creature_design to every block.

    The creature is drawn once and composited into every scene, so there are no
    per-scene image prompts — each block just carries the same design.
    """
    creature_design = generate_creature_design(topic)
    print(f"  [creature] {creature_design}")
    return [{**block, "creature_design": creature_design} for block in script]


if __name__ == "__main__":
    import json, sys
    from script_writer import generate_script

    topic = sys.argv[1] if len(sys.argv) > 1 else "Skinwalker"
    script = generate_script(topic)
    enriched = generate_all_prompts(script, topic)
    print(json.dumps(enriched, indent=2))
