import anthropic

client = anthropic.Anthropic()

SYSTEM_PROMPT = """You convert horror narration lines into image generation prompts for a very specific art style.

THE STYLE (non-negotiable):
- Looks like it was drawn in MS Paint by a beginner
- Thick, uneven black outlines
- Wobbly hand-drawn lines
- Stick figures with round heads
- Simple dot eyes or circle eyes
- Very basic facial expressions
- Flat colors only (no gradients, no shading, no 3D)
- White background with mostly empty space
- Simple shapes: squares, circles, rectangles, triangles
- Amateur and intentionally "bad" — like a child drew it
- No anime, no Disney, no realistic art, no cartoon polish
- No complex textures or backgrounds

COMPOSITION RULES:
- Horizontal 16:9 widescreen
- Centered subjects, lots of white space
- Red arrows or question marks when helpful
- If text appears, it must be short, handwritten-looking, and easy to read
- One clear focal point per image

OUTPUT FORMAT:
Return only the image prompt. No explanation. No preamble. Just the prompt text."""

def generate_image_prompt(scene_text: str, topic: str, scene_index: int) -> str:
    """Convert a narration line into an MS Paint style image prompt."""
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Topic: {topic}\nScene {scene_index}: {scene_text}\n\nGenerate the MS Paint style image prompt for this scene."
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
