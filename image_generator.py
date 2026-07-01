import io
import os
import re
import math
import time
import base64
import hashlib
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image, ImageChops, ImageDraw

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

# Style lock. Pollinations dropped the old FLUX checkpoints and now serves only
# "sana" (the "flux" model name is just an alias for it). Sana has a strong
# painterly bias and ignores style hints buried inside a long scene prompt, so
# the look has to be forced up front, on every request. This prefix is prepended
# to the scene description the prompter produces; keeping it here (not in the LLM
# prompt) guarantees it's always applied and never drifts.
#
# Flat, clean MS-Paint look (not the sketchy/painterly default). The menace lives
# in the per-topic creature description from image_prompter, not here, so the
# style words can stay purely about line/fill. Flat + simple also helps the
# creature read as the SAME character across panels (less detail to diverge).
STYLE_PREFIX = os.environ.get(
    "IMAGE_STYLE_PREFIX",
    "flat simple MS Paint drawing, clean bold even black outline, flat solid "
    "fill colors, no shading, no gradient, no texture, no sketchy lines, "
    "minimal cartoon clip art, simple stick figures with round heads and dot "
    "eyes, plain pure white background, ",
)

MAX_RETRIES = 5
RETRY_BACKOFF = 8  # seconds, multiplied by attempt number
REQUEST_TIMEOUT = 180  # image generation can be slow under load


def _seed_for(prompt: str) -> int:
    """Deterministic seed from the prompt so reruns reproduce the same image."""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return int(digest, 16) % 1_000_000


def generate_image(prompt: str, output_path: str, seed: int = None) -> str:
    """Render a single prompt to a PNG via Pollinations. Returns the path.

    `seed` pins the generation; pass a shared per-topic seed so every panel of a
    video looks like the same creature. Falls back to a per-prompt seed.

    Retries on transient failures (timeouts, 5xx, rate limits, non-image
    responses) with linear backoff. Raises only if every attempt fails.
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


# --- Consistent-creature compositing -----------------------------------------
#
# The only way to guarantee the cryptid is identical in every panel on a free
# text-to-image model is to draw it ONCE and reuse that exact image. We generate
# a few candidate sprites, let Claude's vision pick the cleanest anatomy (no
# extra limbs), then composite that single sprite into each panel at varied
# size/position/facing over the white "sketchbook" page, adding a stick-figure
# witness and a red arrow for scene context.

SPRITE_CANDIDATES = int(os.environ.get("SPRITE_CANDIDATES", "3"))

# Per-panel composition presets (fractions of the frame), cycled by scene index
# so panels vary (size, position, facing) without the creature ever changing.
# The creature stays in the upper area so the witness stick-figure fits below.
_LAYOUTS = [
    {"scale": 0.62, "cx": 0.55, "cy": 0.36, "flip": False},
    {"scale": 0.50, "cx": 0.60, "cy": 0.34, "flip": True},
    {"scale": 0.68, "cx": 0.47, "cy": 0.38, "flip": False},
    {"scale": 0.55, "cx": 0.42, "cy": 0.35, "flip": True},
    {"scale": 0.60, "cx": 0.52, "cy": 0.40, "flip": False},
]


def _pick_best_sprite(paths: list[str], creature_design: str) -> int:
    """Ask Claude's vision to choose the candidate with the cleanest anatomy.

    Best-effort: any failure falls back to the first candidate so image
    generation never hard-fails on the safety net.
    """
    if len(paths) == 1:
        return 0
    try:
        import anthropic
        content = []
        for idx, p in enumerate(paths):
            b64 = base64.standard_b64encode(Path(p).read_bytes()).decode()
            content.append({"type": "text", "text": f"Image {idx}:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": b64}})
        content.append({"type": "text", "text": (
            f"These are candidate drawings of this creature: {creature_design}.\n"
            "Pick the ONE with the cleanest, most correct anatomy: exactly one head, "
            "no extra or duplicated limbs/eyes/tails, a single clear creature, and no "
            "garbled artifacts. Reply with ONLY the integer index of the best image."
        )})
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-opus-4-8", max_tokens=10,
            messages=[{"role": "user", "content": content}],
        )
        m = re.search(r"\d+", msg.content[0].text)
        idx = int(m.group()) if m else 0
        return idx if 0 <= idx < len(paths) else 0
    except Exception as e:  # noqa: BLE001 — safety net must never break the run
        print(f"  [sprite] vision pick failed ({e}); using first candidate")
        return 0


def generate_creature_sprite(creature_design: str, out_path: str) -> str:
    """Draw the cryptid once (isolated on white) and save the chosen sprite."""
    base_seed = _seed_for(creature_design)
    prompt = (
        f"{creature_design}. Full body side profile, the whole creature centered "
        "and isolated on a plain pure white background, simple clean shapes, "
        "exactly one head, correct number of limbs, no extra limbs, no duplicated "
        "body parts, nothing else in the image"
    )
    candidates = []
    for k in range(max(1, SPRITE_CANDIDATES)):
        cand = str(Path(out_path).parent / f"_creature_cand_{k}.png")
        try:
            generate_image(prompt, cand, seed=base_seed + k * 7919)
            candidates.append(cand)
        except Exception as e:  # noqa: BLE001
            print(f"  [sprite] candidate {k} failed: {e}")
    if not candidates:
        raise RuntimeError("all creature sprite candidates failed")

    best = _pick_best_sprite(candidates, creature_design)
    Image.open(candidates[best]).convert("RGB").save(out_path, "PNG")
    print(f"  [sprite] chose candidate {best} of {len(candidates)} → {out_path}")
    return out_path


def _load_sprite(path: str) -> Image.Image:
    """Load the sprite cropped to the creature (drop surrounding white margin)."""
    im = Image.open(path).convert("RGB")
    bg = Image.new("RGB", im.size, (255, 255, 255))
    diff = ImageChops.difference(im, bg).convert("L").point(lambda p: 255 if p > 14 else 0)
    bbox = diff.getbbox()
    return im.crop(bbox) if bbox else im


def _draw_stick(draw: ImageDraw.ImageDraw, x: int, y: int, h: int):
    """A simple crude stick-figure 'witness' at feet position (x, y)."""
    c = (30, 30, 30)
    lw = max(3, int(h * 0.05))
    r = int(h * 0.14)
    hy = y - h
    draw.ellipse([x - r, hy, x + r, hy + 2 * r], outline=c, width=lw)
    neck = hy + 2 * r
    draw.line([x, neck, x, y - int(h * 0.35)], fill=c, width=lw)                    # body
    draw.line([x, neck + int(h * 0.08), x - int(h * 0.22), neck - int(h * 0.02)], fill=c, width=lw)  # arm up
    draw.line([x, neck + int(h * 0.08), x + int(h * 0.22), neck + int(h * 0.22)], fill=c, width=lw)  # arm
    draw.line([x, y - int(h * 0.35), x - int(h * 0.18), y], fill=c, width=lw)       # leg
    draw.line([x, y - int(h * 0.35), x + int(h * 0.18), y], fill=c, width=lw)       # leg


def _draw_arrow(draw: ImageDraw.ImageDraw, x1, y1, x2, y2):
    c = (210, 30, 30)
    lw = max(6, int(IMAGE_HEIGHT * 0.018))
    draw.line([x1, y1, x2, y2], fill=c, width=lw)
    ang = math.atan2(y2 - y1, x2 - x1)
    L = int(IMAGE_HEIGHT * 0.06)
    for da in (2.5, -2.5):
        draw.line([x2, y2, x2 + L * math.cos(ang + da), y2 + L * math.sin(ang + da)],
                  fill=c, width=lw)


def compose_panel(sprite: Image.Image, index: int, out_path: str) -> str:
    """Composite the (identical) sprite into one panel using layout[index].

    The creature is multiplied onto the white page first; the witness stick
    figure and the red arrow are drawn on TOP so they're never hidden, with the
    arrow pointing at the creature's head.
    """
    W, H = IMAGE_WIDTH, IMAGE_HEIGHT
    lay = _LAYOUTS[index % len(_LAYOUTS)]

    s = sprite.transpose(Image.FLIP_LEFT_RIGHT) if lay["flip"] else sprite
    tw = max(1, int(W * lay["scale"]))
    th = max(1, int(s.height * tw / s.width))
    s = s.resize((tw, th))
    x = max(0, min(int(W * lay["cx"] - tw / 2), W - tw))
    y = max(0, min(int(H * lay["cy"] - th / 2), H - th))

    layer = Image.new("RGB", (W, H), "white")
    layer.paste(s, (x, y))
    base = ImageChops.multiply(Image.new("RGB", (W, H), "white"), layer)

    draw = ImageDraw.Draw(base)
    # Witness on whichever bottom side the creature isn't hogging.
    on_left = (x + tw / 2) > W * 0.5
    fx = int(W * 0.15) if on_left else int(W * 0.85)
    fy = int(H * 0.90)
    fh = int(H * 0.24)
    _draw_stick(draw, fx, fy, fh)

    # Arrow from just above the witness to the creature's head (head is at the
    # sprite's leading edge, which flips with the sprite).
    head_x = x + (0.85 * tw if lay["flip"] else 0.15 * tw)
    head_y = y + 0.25 * th
    _draw_arrow(draw, fx, fy - fh - int(H * 0.02), head_x, head_y)

    base.save(out_path, "PNG")
    return out_path


def generate_all_images(script: list[dict], images_dir: str) -> list[dict]:
    """Draw the cryptid once, then composite it into every panel.

    Reusing one vetted sprite guarantees the creature is identical across the
    whole reel (no drift, no extra appendages).
    """
    Path(images_dir).mkdir(parents=True, exist_ok=True)

    designs = {b.get("creature_design") for b in script if b.get("creature_design")}
    creature_design = next(iter(designs)) if len(designs) == 1 else None

    sprite_path = str(Path(images_dir) / "creature.png")
    if not Path(sprite_path).exists():
        if not creature_design:
            raise ValueError("creature_design missing/inconsistent; run image_prompter first")
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
