import os
import time
from pathlib import Path

import requests

REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "")

# Text-to-image model on Replicate.
REPLICATE_MODEL = os.environ.get("REPLICATE_MODEL", "black-forest-labs/flux-schnell")
REPLICATE_URL = f"https://api.replicate.com/v1/models/{REPLICATE_MODEL}/predictions"

MAX_POLLS = 60
POLL_INTERVAL = 2  # seconds between status checks while a prediction runs


def _extract_image_url(output) -> str:
    """flux-schnell returns a list of image URLs; tolerate a bare string too."""
    if isinstance(output, list):
        if not output:
            raise RuntimeError("Replicate returned an empty output list")
        return output[0]
    if isinstance(output, str):
        return output
    raise RuntimeError(f"Unexpected Replicate output format: {output!r}")


def generate_image(prompt: str, output_path: str) -> str:
    """Render a single prompt to a PNG via the Replicate API. Returns the path."""
    if not REPLICATE_API_TOKEN:
        raise ValueError("REPLICATE_API_TOKEN not set")

    headers = {
        "Authorization": f"Bearer {REPLICATE_API_TOKEN}",
        "Content-Type": "application/json",
        # Ask Replicate to hold the request open until the prediction settles,
        # so we usually get the result without polling.
        "Prefer": "wait",
    }
    payload = {
        "input": {
            "prompt": prompt,
            "aspect_ratio": "16:9",
            "output_format": "png",
            "num_outputs": 1,
        }
    }

    r = requests.post(REPLICATE_URL, headers=headers, json=payload, timeout=120)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Replicate request failed ({r.status_code}): {r.text[:300]}")

    prediction = r.json()

    # If "Prefer: wait" did not fully resolve it, poll until it settles.
    polls = 0
    while prediction.get("status") not in ("succeeded", "failed", "canceled"):
        if polls >= MAX_POLLS:
            raise RuntimeError("Replicate prediction timed out")
        time.sleep(POLL_INTERVAL)
        polls += 1
        get_url = prediction.get("urls", {}).get("get")
        if not get_url:
            raise RuntimeError("Replicate response missing polling URL")
        pr = requests.get(get_url, headers=headers, timeout=60)
        pr.raise_for_status()
        prediction = pr.json()

    if prediction.get("status") != "succeeded":
        raise RuntimeError(
            f"Replicate prediction {prediction.get('status')}: "
            f"{prediction.get('error')}"
        )

    image_url = _extract_image_url(prediction.get("output"))

    img = requests.get(image_url, timeout=120)
    img.raise_for_status()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(img.content)
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
