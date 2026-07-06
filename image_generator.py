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


# --- Per-scene generation with a vision consistency/anatomy filter -----------
#
# Free text-to-image redraws the creature each scene, which lets it deform
# (extra heads/limbs) and drift. We can't prevent that on a free model, but we
# can filter it: draw ONE vetted reference of the creature, then for every scene
# generate several candidates and let Claude's vision keep the one that best
# matches the reference AND has clean anatomy. Not perfect for snake-like bodies,
# but it removes most of the slop; the human approval gate catches the rest.

SPRITE_CANDIDATES = int(os.environ.get("SPRITE_CANDIDATES", "3"))
SCENE_CANDIDATES = int(os.environ.get("SCENE_CANDIDATES", "3"))


def _img_block(path: str) -> dict:
    b64 = base64.standard_b64encode(Path(path).read_bytes()).decode()
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": b64}}


def _vision_pick(content_blocks: list, n: int) -> int:
    """Run one vision request and return the chosen 0-based index.

    Best-effort: any failure (no API key, network) falls back to index 0 so
    image generation never hard-fails on the safety net.
    """
    try:
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-opus-4-8", max_tokens=10,
            messages=[{"role": "user", "content": content_blocks}],
        )
        m = re.search(r"\d+", msg.content[0].text)
        idx = int(m.group()) if m else 0
        return idx if 0 <= idx < n else 0
    except Exception as e:  # noqa: BLE001 — never break the run on the safety net
        print(f"    [vision] pick failed ({e}); using first candidate")
        return 0


def _pick_best_sprite(paths: list[str], creature_design: str) -> int:
    """Choose the candidate reference sprite with the cleanest anatomy."""
    if len(paths) == 1:
        return 0
    content = []
    for idx, p in enumerate(paths):
        content.append({"type": "text", "text": f"Image {idx}:"})
        content.append(_img_block(p))
    content.append({"type": "text", "text": (
        f"These are candidate drawings of this creature: {creature_design}.\n"
        "Pick the ONE with the cleanest, most correct anatomy: exactly one head, "
        "no extra or duplicated heads/limbs/eyes/tails, a single clear creature, "
        "no garbled artifacts. Reply with ONLY the integer index of the best image."
    )})
    return _vision_pick(content, len(paths))


def generate_creature_sprite(creature_design: str, out_path: str) -> str:
    """Draw the cryptid once (isolated, clean anatomy) as the reference used to
    keep later scenes on-model."""
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
    print(f"  [sprite] reference: chose candidate {best} of {len(candidates)} → {out_path}")
    return out_path


def _pick_matching_scene(candidate_paths: list[str], reference_path: str,
                         creature_design: str) -> int:
    """Pick the scene candidate that best matches the reference and is clean."""
    if len(candidate_paths) == 1:
        return 0
    content = [
        {"type": "text", "text": f"REFERENCE — this is the creature ({creature_design}):"},
        _img_block(reference_path),
        {"type": "text", "text": "CANDIDATE scene drawings of the same creature:"},
    ]
    for idx, p in enumerate(candidate_paths):
        content.append({"type": "text", "text": f"Candidate {idx}:"})
        content.append(_img_block(p))
    content.append({"type": "text", "text": (
        "Pick the candidate that BEST matches the reference creature's look AND has "
        "clean anatomy: exactly ONE head, no extra or duplicated heads/limbs/tails, "
        "a single clear creature, no garbled artifacts. Reply with ONLY the integer "
        "index of the best candidate."
    )})
    return _vision_pick(content, len(candidate_paths))


# Small pause between image requests so a burst of ~SCENE_CANDIDATES x scenes
# doesn't trip Pollinations rate limiting (which was making whole scenes fail).
SCENE_REQUEST_PAUSE = float(os.environ.get("SCENE_REQUEST_PAUSE", "2"))


def generate_scene(scene_prompt: str, reference_path: str, creature_design: str,
                   out_path: str, index: int) -> tuple:
    """Generate several candidates for one scene; keep the cleanest match.

    If every candidate fails (e.g. Pollinations is down/rate-limiting), fall back
    to the vetted reference creature so ONE bad scene never kills the whole reel —
    the human approval gate can still catch a weak frame.
    """
    base_seed = _seed_for(scene_prompt)
    candidates = []
    for k in range(max(1, SCENE_CANDIDATES)):
        cand = str(Path(out_path).parent / f"_scene{index:02d}_cand_{k}.png")
        try:
            generate_image(scene_prompt, cand, seed=base_seed + k * 6301)
            candidates.append(cand)
        except Exception as e:  # noqa: BLE001
            print(f"    [scene {index}] candidate {k} failed: {e}")
        time.sleep(SCENE_REQUEST_PAUSE)

    if not candidates:
        print(f"    [scene {index}] all candidates failed; using reference creature")
        Image.open(reference_path).convert("RGB").save(out_path, "PNG")
        return -1, 0

    best = _pick_matching_scene(candidates, reference_path, creature_design)
    Image.open(candidates[best]).convert("RGB").save(out_path, "PNG")
    return best, len(candidates)


def generate_all_images(script: list[dict], images_dir: str) -> list[dict]:
    """Draw a reference creature, then generate each scene as its own drawing,
    keeping the candidate that best matches the reference with clean anatomy.

    Different poses per scene (story-matching); the reference + vision filter keep
    the creature on-model and cut out most deformed frames. Free tools can't make
    this perfect, so the human approval gate is the final backstop.
    """
    Path(images_dir).mkdir(parents=True, exist_ok=True)

    designs = {b.get("creature_design") for b in script if b.get("creature_design")}
    creature_design = next(iter(designs)) if len(designs) == 1 else None
    if not creature_design:
        raise ValueError("creature_design missing/inconsistent; run image_prompter first")

    reference_path = str(Path(images_dir) / "creature.png")
    if not Path(reference_path).exists():
        generate_creature_sprite(creature_design, reference_path)

    result = []
    for i, block in enumerate(script):
        image_path = str(Path(images_dir) / f"scene_{i:02d}.png")
        if Path(image_path).exists():
            print(f"  [image {i+1}/{len(script)}] exists, skipping")
        else:
            scene_prompt = block.get("image_prompt") or creature_design
            best, n = generate_scene(scene_prompt, reference_path, creature_design, image_path, i)
            picked = f"chose candidate {best} of {n}" if n else "fell back to reference"
            print(f"  [image {i+1}/{len(script)}] {picked} → {image_path}")
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
