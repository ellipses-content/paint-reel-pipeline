import os
import subprocess
import asyncio
import tempfile
import requests
from pathlib import Path

JAMENDO_CLIENT_ID = os.environ.get("JAMENDO_CLIENT_ID", "")

# Dark/atmospheric tags for background music
JAMENDO_TAGS = "dark,ambient,horror,atmospheric"


def fetch_music(output_path: str) -> bool:
    """Download a free atmospheric track from Jamendo."""
    try:
        url = (
            f"https://api.jamendo.com/v3.0/tracks/?client_id={JAMENDO_CLIENT_ID}"
            f"&format=json&limit=1&tags={JAMENDO_TAGS}&audioformat=mp32"
            f"&order=popularity_total&license_ccby=true"
        )
        r = requests.get(url, timeout=30)
        data = r.json()
        tracks = data.get("results", [])
        if not tracks:
            return False
        audio_url = tracks[0]["audio"]
        audio_r = requests.get(audio_url, timeout=60)
        with open(output_path, "wb") as f:
            f.write(audio_r.content)
        print(f"  [music] downloaded → {output_path}")
        return True
    except Exception as e:
        print(f"  [music] failed: {e}")
        return False


async def generate_voiceover(script: list[dict], output_path: str):
    """Generate voiceover using edge-tts."""
    import edge_tts
    full_text = " ".join(block["text"] for block in script)
    communicate = edge_tts.Communicate(full_text, voice="en-US-ChristopherNeural")
    await communicate.save(output_path)
    print(f"  [voice] saved → {output_path}")


def build_video(script: list[dict], output_path: str, work_dir: str) -> str:
    """
    Assemble images into video using FFmpeg concat demuxer.
    Each image is shown for the duration until the next timestamp.
    Total video = 60 seconds.
    """
    concat_file = Path(work_dir) / "concat.txt"
    total_duration = 60

    lines = []
    for i, block in enumerate(script):
        start = block["time"]
        if i + 1 < len(script):
            duration = script[i + 1]["time"] - start
        else:
            duration = total_duration - start

        duration = max(duration, 1)  # minimum 1 second per image
        lines.append(f"file '{os.path.abspath(block['image_path'])}'")
        lines.append(f"duration {duration}")

    # FFmpeg needs last file repeated without duration
    lines.append(f"file '{os.path.abspath(script[-1]['image_path'])}'")

    with open(concat_file, "w") as f:
        f.write("\n".join(lines))

    video_only = Path(work_dir) / "video_only.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-vf", "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:white",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-r", "24",
        str(video_only)
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    print(f"  [video] silent video built")
    return str(video_only)


def mix_audio(video_path: str, voice_path: str, music_path: str, output_path: str):
    """Mix voiceover + background music into the video."""
    has_music = music_path and Path(music_path).exists()

    if has_music:
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", voice_path,
            "-i", music_path,
            "-filter_complex",
            "[1:a]volume=1.0[voice];[2:a]volume=0.15,atrim=0:60[music];[voice][music]amix=inputs=2:duration=first[audio]",
            "-map", "0:v",
            "-map", "[audio]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_path
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", voice_path,
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_path
        ]

    subprocess.run(cmd, check=True, capture_output=True)
    print(f"  [audio] mixed → {output_path}")


def assemble_video(script: list[dict], topic: str, output_dir: str) -> str:
    """Full assembly pipeline. Returns path to final video."""
    work_dir = Path(output_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    voice_path = str(work_dir / "voiceover.mp3")
    music_path = str(work_dir / "music.mp3")
    final_path = str(work_dir / "final.mp4")

    # Generate voiceover
    print("  [voice] generating...")
    asyncio.run(generate_voiceover(script, voice_path))

    # Fetch music
    print("  [music] fetching from Jamendo...")
    music_ok = fetch_music(music_path)
    if not music_ok:
        music_path = None

    # Build silent video
    print("  [video] assembling images...")
    video_only = build_video(script, final_path, str(work_dir))

    # Mix audio
    print("  [audio] mixing...")
    mix_audio(video_only, voice_path, music_path, final_path)

    print(f"  [done] final video → {final_path}")
    return final_path


if __name__ == "__main__":
    import json, sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "Skinwalker"

    with open(f"output/{topic.lower().replace(' ', '_')}/script_with_images.json") as f:
        script = json.load(f)

    out = assemble_video(script, topic, f"output/{topic.lower().replace(' ', '_')}")
    print(f"Final: {out}")
