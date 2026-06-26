import os
from pathlib import Path

from huggingface_hub import InferenceClient

HF_API_TOKEN = os.environ.get("HF_API_TOKEN", "")

# Text-to-image model on the Hugging Face Inference API.
HF_MODEL = os.environ.get("HF_MODEL", "black-forest-labs/FLUX.1-schnell")

_client = None


def _get_client() -> InferenceClient:
    """Lazily build a shared InferenceClient (so import never requires a token)."""
    global _client
    if _client is None:
        if not HF_API_TOKEN:
            raise ValueError("HF_API_TOKEN not set")
        _client = InferenceClient(model=HF_MODEL, token=HF_API_TOKEN)
    return _client


def generate_image(prompt: str, output_path: str) -> str:
    """Render a single prompt to a PNG via huggingface_hub. Returns the path."""
    client = _get_client()
    # text_to_image returns a PIL.Image.Image
    image = client.text_to_image(prompt, model=HF_MODEL)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path


def generate_all_images(script: list[dict], images_dir: str) -> list[dict]:
    """Render every scene's image_prompt and attach image_path to each block."""
    Path(images_dir).mkdir(parents=True, exist_ok=True)
    result = []
    for i, block in enumerate(script):
        image_path = str(Path(images_dir) / f"scene_{i:02d}.png")
        if Path(image_path).exists():
            print(f"  [image {i+1}/{len(script)}] exists, skipping")
        else:
            generate_image(block["image_prompt"], image_path)
            print(f"  [image {i+1}/{len(script)}] generated → {image_path}")
        result.append({**block, "image_path": image_path})
    return result


if __name__ == "__main__":
    import json, sys
    from script_writer import generate_script
    from image_prompter import generate_all_prompts

    topic = sys.argv[1] if len(sys.argv) > 1 else "Skinwalker"
    script = generate_script(topic)
    enriched = generate_all_prompts(script, topic)
    full = generate_all_images(enriched, f"output/{topic.lower().replace(' ', '_')}/images")
    print(json.dumps(full, indent=2))
