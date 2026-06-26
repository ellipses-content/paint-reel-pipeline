import os
import time
from pathlib import Path

import requests

HF_API_TOKEN = os.environ.get("HF_API_TOKEN", "")

# Text-to-image model on the Hugging Face Inference API.
HF_MODEL = os.environ.get("HF_MODEL", "black-forest-labs/FLUX.1-schnell")
HF_API_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL}"

# Negative prompt reinforces the intentionally-amateur MS Paint look.
NEGATIVE_PROMPT = (
    "photorealistic, realistic, 3d render, gradient, shading, soft lighting, "
    "anime, disney, professional, detailed, high quality, polished, complex background"
)

MAX_RETRIES = 6
RETRY_BACKOFF = 15  # seconds between retries while the model warms up


def generate_image(prompt: str, output_path: str) -> str:
    """Render a single prompt to a PNG via the HF Inference API. Returns the path."""
    if not HF_API_TOKEN:
        raise ValueError("HF_API_TOKEN not set")

    headers = {"Authorization": f"Bearer {HF_API_TOKEN}"}
    payload = {
        "inputs": prompt,
        "parameters": {
            "negative_prompt": NEGATIVE_PROMPT,
            "width": 1024,
            "height": 576,  # 16:9 widescreen
        },
    }

    for attempt in range(1, MAX_RETRIES + 1):
        r = requests.post(HF_API_URL, headers=headers, json=payload, timeout=120)

        if r.status_code == 200:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(r.content)
            return output_path

        # 503 = model is loading; back off and retry.
        if r.status_code == 503:
            wait = RETRY_BACKOFF
            try:
                wait = int(r.json().get("estimated_time", RETRY_BACKOFF))
            except Exception:
                pass
            print(f"    [hf] model loading, retry {attempt}/{MAX_RETRIES} in {wait}s...")
            time.sleep(wait)
            continue

        # 429 = rate limited; back off and retry.
        if r.status_code == 429:
            print(f"    [hf] rate limited, retry {attempt}/{MAX_RETRIES} in {RETRY_BACKOFF}s...")
            time.sleep(RETRY_BACKOFF)
            continue

        raise RuntimeError(f"HF image request failed ({r.status_code}): {r.text[:200]}")

    raise RuntimeError(f"HF image generation gave up after {MAX_RETRIES} retries")


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
