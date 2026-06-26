import os
import json
import sys
from pathlib import Path

from script_writer import generate_script
from image_prompter import generate_all_prompts
from image_generator import generate_all_images
from video_assembler import assemble_video
from uploader import upload_video

TOPICS_FILE = "topics.txt"
PROGRESS_FILE = "topics_progress.txt"


def load_topics() -> list[str]:
    with open(TOPICS_FILE, "r") as f:
        return [line.strip() for line in f if line.strip()]


def get_current_index() -> int:
    if not Path(PROGRESS_FILE).exists():
        return 0
    try:
        return int(Path(PROGRESS_FILE).read_text().strip())
    except ValueError:
        return 0


def save_progress(index: int):
    Path(PROGRESS_FILE).write_text(str(index) + "\n", encoding="utf-8")


def get_output_dir(topic: str) -> str:
    slug = topic.lower().replace(" ", "_").replace("(", "").replace(")", "")
    return f"output/{slug}"


def run():
    topics = load_topics()
    index = get_current_index()

    if index >= len(topics):
        print("All topics completed! Add more to topics.txt.")
        sys.exit(0)

    topic = topics[index]
    output_dir = get_output_dir(topic)
    print(f"\n{'='*50}")
    print(f"  CRYPTID FILES PIPELINE")
    print(f"  Topic [{index+1}/{len(topics)}]: {topic}")
    print(f"{'='*50}\n")

    # --- STEP 1: Generate script ---
    print("[1/5] Writing script...")
    script_path = Path(output_dir) / "script.json"
    if script_path.exists():
        print("  Script already exists, loading...")
        script = json.loads(script_path.read_text())
    else:
        script = generate_script(topic)
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        script_path.write_text(json.dumps(script, indent=2))
    print(f"  {len(script)} scenes written\n")

    # --- STEP 2: Generate image prompts ---
    print("[2/5] Generating image prompts...")
    prompts_path = Path(output_dir) / "script_with_prompts.json"
    if prompts_path.exists():
        print("  Prompts already exist, loading...")
        enriched = json.loads(prompts_path.read_text())
    else:
        enriched = generate_all_prompts(script, topic)
        prompts_path.write_text(json.dumps(enriched, indent=2))
    print()

    # --- STEP 3: Generate images ---
    print("[3/5] Generating images (Hugging Face)...")
    images_dir = str(Path(output_dir) / "images")
    full_script_path = Path(output_dir) / "script_with_images.json"
    if full_script_path.exists():
        print("  Images already exist, loading...")
        full_script = json.loads(full_script_path.read_text())
    else:
        full_script = generate_all_images(enriched, images_dir)
        full_script_path.write_text(json.dumps(full_script, indent=2))
    print()

    # --- STEP 4: Assemble video ---
    print("[4/5] Assembling video...")
    final_video = str(Path(output_dir) / "final.mp4")
    if Path(final_video).exists():
        print("  Video already assembled, skipping...")
    else:
        assemble_video(full_script, topic, output_dir)
    print()

    # --- STEP 5: Upload to YouTube ---
    print("[5/5] Uploading to YouTube...")
    video_id = upload_video(final_video, topic)
    print(f"  Published: https://youtube.com/shorts/{video_id}\n")

    # --- Advance progress ---
    save_progress(index + 1)
    print(f"Progress saved. Next up: {topics[index+1] if index+1 < len(topics) else 'All done!'}")
    print(f"\n{'='*50}\n")


if __name__ == "__main__":
    run()
