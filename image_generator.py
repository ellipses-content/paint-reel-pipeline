import io
import os
import time
import hashlib
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageChops

# Pollinations.ai — free text-to-image, no API key required.
POLLINATIONS_BASE = os.environ.get(
    "POLLINATIONS_BASE", "https://image.pollinations.ai/prompt"
).rstrip("/")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "flux")

# Match the video canvas so images fill the frame without letterbox bars.
IMAGE_WIDTH = int(os.environ.get("IMAGE_WIDTH", "1280"))
IMAGE_HEIGHT = int(os.environ.get("IMAGE_HEIGHT", "720"))

# An optional Pollinations token (raises rate limits) — works fine without one.
POLLINATIONS_TOKEN = os.environ.get("POLLINATIONS_TOKEN", "")

# Simple MS-Paint style. Forced up front on every request (Pollinations' "sana"
# model ignores style cues buried in a long prompt). Flat + minimal detail also
# means fewer places for the model to invent extra limbs.
STYLE_PREFIX = os.environ.get(
    "IMAGE_STYLE_PREFIX",
    "flat simple MS Paint drawing, clean bold even black outline, flat solid "
    "fill colors, no shading, no gradient, no texture, no sketchy lines, "
    "minimal cartoon clip art, plain pure white background, ",
)

# Pollinations' free image API flaps (intermittent 530s). Retry a few times to
# ride out a brief blip, then fail over to the AI Horde fallback rather than
# grinding for minutes when it's fully down. Tunable via env.
MAX_RETRIES = int(os.environ.get("IMAGE_MAX_RETRIES", "4"))
RETRY_BACKOFF = 8   # seconds * attempt, capped below
RETRY_BACKOFF_CAP = 30
REQUEST_TIMEOUT = 180  # image generation can be slow under load


def _seed_for(prompt: str) -> int:
    """Deterministic seed from the prompt so reruns reproduce the same image."""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return int(digest, 16) % 1_000_000


# AI Horde — free, no-key (anonymous), volunteer GPU network. Used as a fallback
# when Pollinations' image API is down, so an outage never kills the whole run.
# Slower (anonymous = low priority) but we only need one image per video.
AIHORDE_API = os.environ.get("AIHORDE_API", "https://aihorde.net/api/v2").rstrip("/")
AIHORDE_KEY = os.environ.get("AIHORDE_API_KEY", "0000000000")  # anonymous
AIHORDE_MODELS = os.environ.get("AIHORDE_MODELS", "")  # optional csv; blank = any worker
AIHORDE_TIMEOUT = int(os.environ.get("AIHORDE_TIMEOUT", "600"))
AIHORDE_POLL = 8
AIHORDE_W, AIHORDE_H = 1024, 576  # multiples of 64, safe for anonymous users


def _generate_pollinations(prompt: str, output_path: str, seed: int = None) -> str:
    """Render a single prompt to a PNG via Pollinations. Returns the path.

    Retries on transient failures (timeouts, 5xx, rate limits, non-image
    responses) with capped backoff. Raises only if every attempt fails.
    """
    styled_prompt = f"{STYLE_PREFIX}{prompt}"
    url = f"{POLLINATIONS_BASE}/{quote(styled_prompt)}"
    params = {
        "width": IMAGE_WIDTH,
        "height": IMAGE_HEIGHT,
        "model": IMAGE_MODEL,
        "seed": seed if seed is not None else _seed_for(prompt),
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

            image = Image.open(io.BytesIO(resp.content)).convert("RGB")
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            image.save(output_path, format="PNG")
            return output_path
        except Exception as e:  # noqa: BLE001 — retry on anything transient
            last_err = e
            if attempt < MAX_RETRIES:
                wait = min(RETRY_BACKOFF * attempt, RETRY_BACKOFF_CAP)
                print(f"    [retry {attempt}/{MAX_RETRIES - 1}] {e}; waiting {wait}s...")
                time.sleep(wait)

    raise RuntimeError(
        f"image generation failed after {MAX_RETRIES} attempts: {last_err}"
    )


def _generate_aihorde(prompt: str, output_path: str) -> str:
    """Fallback image generation via AI Horde (async submit -> poll -> fetch)."""
    styled_prompt = f"{STYLE_PREFIX}{prompt}"
    payload = {
        "prompt": styled_prompt,
        "params": {
            "width": AIHORDE_W, "height": AIHORDE_H, "steps": 22, "n": 1,
            "cfg_scale": 7, "sampler_name": "k_euler_a",
        },
        "nsfw": True, "censor_nsfw": False, "r2": True, "trusted_workers": False,
    }
    if AIHORDE_MODELS:
        payload["models"] = [m.strip() for m in AIHORDE_MODELS.split(",") if m.strip()]
    headers = {"apikey": AIHORDE_KEY, "Client-Agent": "cryptid-files-pipeline:1.0"}

    resp = requests.post(f"{AIHORDE_API}/generate/async", json=payload,
                         headers=headers, timeout=40)
    resp.raise_for_status()
    job_id = resp.json()["id"]
    print(f"    [aihorde] queued job {job_id}; waiting (this can take a few min)...")

    deadline = time.time() + AIHORDE_TIMEOUT
    while time.time() < deadline:
        time.sleep(AIHORDE_POLL)
        chk = requests.get(f"{AIHORDE_API}/generate/check/{job_id}", timeout=30).json()
        if chk.get("faulted"):
            raise RuntimeError("aihorde job faulted")
        if chk.get("done"):
            break
    else:
        raise RuntimeError(f"aihorde timed out after {AIHORDE_TIMEOUT}s")

    status = requests.get(f"{AIHORDE_API}/generate/status/{job_id}", timeout=60).json()
    gens = status.get("generations") or []
    if not gens:
        raise RuntimeError("aihorde returned no image")
    img_bytes = requests.get(gens[0]["img"], timeout=90).content
    image = Image.open(io.BytesIO(img_bytes)).convert("RGB").resize((IMAGE_WIDTH, IMAGE_HEIGHT))
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG")
    print(f"    [aihorde] got image → {output_path}")
    return output_path


def generate_image(prompt: str, output_path: str, seed: int = None) -> str:
    """Generate one image. Pollinations first (fast); on failure fall back to the
    free AI Horde so a Pollinations outage doesn't kill the whole run."""
    try:
        return _generate_pollinations(prompt, output_path, seed)
    except Exception as e:  # noqa: BLE001
        print(f"  [pollinations failed ({e}); falling back to AI Horde...]")
        return _generate_aihorde(prompt, output_path)


# --- Draw-once-and-reuse compositing -----------------------------------------
#
# The only free way to guarantee the cryptid is IDENTICAL in every scene (and to
# avoid the per-scene "AI slop" of extra heads/limbs) is to draw it exactly once
# and reuse that same image everywhere. There's no free vision model to filter
# anatomy, so we lean on a tight anatomy prompt + the flat simple style to keep
# the single sprite clean; a rare bad sprite is caught at the human approval gate.
#
# Each panel is the same creature composited onto the white page at a varied
# size/position/facing, so scenes still differ while the creature never changes.
_LAYOUTS = [
    {"scale": 0.85, "cx": 0.50, "cy": 0.48, "flip": False},
    {"scale": 0.70, "cx": 0.56, "cy": 0.46, "flip": True},
    {"scale": 0.92, "cx": 0.45, "cy": 0.50, "flip": False},
    {"scale": 0.74, "cx": 0.53, "cy": 0.47, "flip": True},
    {"scale": 0.80, "cx": 0.48, "cy": 0.49, "flip": False},
]


def generate_creature_sprite(creature_design: str, out_path: str) -> str:
    """Draw the cryptid ONCE, isolated on white, as the sprite reused everywhere."""
    prompt = (
        f"{creature_design.rstrip('. ')}. Full body side view, the whole creature centered and "
        "isolated on a plain pure white background. Exactly ONE head, correct "
        "number of limbs, no extra legs, no extra arms, no extra eyes, no extra "
        "horns, no extra tails, no duplicated body parts. Simple clean shapes, "
        "nothing else in the image."
    )
    generate_image(prompt, out_path, seed=_seed_for(creature_design))
    print(f"  [sprite] drew creature → {out_path}")
    return out_path


def _load_sprite(path: str) -> Image.Image:
    """Load the sprite cropped to the creature (drop the surrounding white)."""
    im = Image.open(path).convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im, bg).convert("L").point(lambda p: 255 if p > 14 else 0)
    bbox = diff.getbbox()
    return im.crop(bbox) if bbox else im


def compose_panel(sprite: Image.Image, index: int, out_path: str) -> str:
    """Composite the identical sprite onto a white page using layout[index].

    Multiply blend drops the sprite's white margin onto the page seamlessly; the
    creature's colored body prints on top. Same pixels every time = identical.
    """
    W, H = IMAGE_WIDTH, IMAGE_HEIGHT
    lay = _LAYOUTS[index % len(_LAYOUTS)]

    s = sprite.transpose(Image.FLIP_LEFT_RIGHT) if lay["flip"] else sprite
    tw = max(1, int(W * lay["scale"]))
    th = max(1, int(s.height * tw / s.width))
    if th > H:  # keep it inside the frame
        th = H
        tw = max(1, int(s.width * th / s.height))
    s = s.resize((tw, th))
    x = max(0, min(int(W * lay["cx"] - tw / 2), W - tw))
    y = max(0, min(int(H * lay["cy"] - th / 2), H - th))

    layer = Image.new("RGB", (W, H), "white")
    layer.paste(s, (x, y))
    ImageChops.multiply(Image.new("RGB", (W, H), "white"), layer).save(out_path, "PNG")
    return out_path


def generate_all_images(script: list[dict], images_dir: str) -> list[dict]:
    """Draw the cryptid once, then composite that same drawing into every scene.

    Reusing one sprite guarantees the creature is identical across the whole reel
    (no drift, no extra appendages). Scenes vary only by the creature's
    size/position/facing on the page.
    """
    Path(images_dir).mkdir(parents=True, exist_ok=True)

    designs = {b.get("creature_design") for b in script if b.get("creature_design")}
    creature_design = next(iter(designs)) if len(designs) == 1 else None
    if not creature_design:
        raise ValueError("creature_design missing/inconsistent; run image_prompter first")

    sprite_path = str(Path(images_dir) / "creature.png")
    if not Path(sprite_path).exists():
        generate_creature_sprite(creature_design, sprite_path)
    sprite = _load_sprite(sprite_path)

    result = []
    for i, block in enumerate(script):
        image_path = str(Path(images_dir) / f"scene_{i:02d}.png")
        if Path(image_path).exists():
            print(f"  [image {i+1}/{len(script)}] exists, skipping")
        else:
            compose_panel(sprite, i, image_path)
            print(f"  [image {i+1}/{len(script)}] composed → {image_path}")
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
