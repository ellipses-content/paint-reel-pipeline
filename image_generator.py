import io
import os
import time
import hashlib
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image

# Pollinations.ai — free text-to-image, no API key required. FLUX under the hood.
# Override via env if you ever want a different host/model/size.
POLLINATIONS_BASE = os.environ.get(
    "POLLINATIONS_BASE", "https://image.pollinations.ai/prompt"
).rstrip("/")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "flux")

# Match the video canvas (video_assembler builds 1280x720) so images fill the
# frame without white letterbox bars.
IMAGE_WIDTH = int(os.environ.get("IMAGE_WIDTH", "1280"))
IMAGE_HEIGHT = int(os.environ.get("IMAGE_HEIGHT", "720"))

# An optional Pollinations token (raises rate limits) — works fine without one.
POLLINATIONS_TOKEN = os.environ.get("POLLINATIONS_TOKEN", "")

MAX_RETRIES = 5
RETRY_BACKOFF = 8  # seconds, multiplied by attempt number
REQUEST_TIMEOUT = 180  # image generation can be slow under load


def _seed_for(prompt: str) -> int:
    """Deterministic seed from the prompt so reruns reproduce the same image."""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return int(digest, 16) % 1_000_000


def generate_image(prompt: str, output_path: str) -> str:
    """Render a single prompt to a PNG via Pollinations. Returns the path.

    Retries on transient failures (timeouts, 5xx, rate limits, non-image
    responses) with linear backoff. Raises only if every attempt fails.
    """
    url = f"{POLLINATIONS_BASE}/{quote(prompt)}"
    params = {
        "width": IMAGE_WIDTH,
        "height": IMAGE_HEIGHT,
        "model": IMAGE_MODEL,
        "seed": _seed_for(prompt),
        "nologo": "true",
    }
    if POLLINATIONS_TOKEN:
        params["token"] = POLLINATIONS_TOKEN

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()

            content_type = resp.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                raise ValueError(
                    f"expected an image, got '{content_type or 'unknown'}' "
                    f"({len(resp.content)} bytes)"
                )

            # Re-encode through Pillow to guarantee a valid PNG at the path
            # (Pollinations may hand back JPEG) and verify it isn't corrupt.
            image = Image.open(io.BytesIO(resp.content)).convert("RGB")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path, format="PNG")
            return output_path
        except Exception as e:  # noqa: BLE001 — retry on anything transient
            last_err = e
            if attempt < MAX_RETRIES:
                wait = RETRY_BACKOFF * attempt
                print(f"    [retry {attempt}/{MAX_RETRIES - 1}] {e}; waiting {wait}s...")
                time.sleep(wait)

    raise RuntimeError(
        f"image generation failed after {MAX_RETRIES} attempts: {last_err}"
    )


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
