import os
import subprocess
import asyncio
import requests
from pathlib import Path

JAMENDO_CLIENT_ID = os.environ.get("JAMENDO_CLIENT_ID", "")

# Dark/atmospheric tags for background music
JAMENDO_TAGS = "dark,ambient,horror,atmospheric"

# Voice (edge-tts, free). en-GB-RyanNeural is a serious storytelling voice that
# isn't the ubiquitous default heard on every AI channel; slowed slightly for a
# slow-burn dread delivery. Both overridable via env.
VOICE = os.environ.get("TTS_VOICE", "en-GB-RyanNeural")
VOICE_RATE = os.environ.get("TTS_RATE", "-8%")

# Vertical Shorts canvas (9:16). The crude art sits on a white "sketchbook" page
# with big burned-in captions below it — both fill the phone screen natively
# instead of the old 16:9 letterbox.
VIDEO_W = 1080
VIDEO_H = 1920
ART_TOP = 300            # top offset of the artwork on the canvas, in px
FPS = 24
WORDS_PER_CAPTION = 4    # how many words show at once in the caption line


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


def _mp3_duration(path: str) -> float:
    """Actual playback duration of an audio file via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


async def _synth_block(text: str):
    """Synthesize one narration block.

    Returns (mp3_bytes, words, sentences) where words/sentences are lists of
    (start, end, text) in seconds relative to this block's audio. edge-tts
    (7.x) only emits SentenceBoundary events for these voices, so `words` is
    usually empty and captions are derived from sentence spans.
    """
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=VOICE, rate=VOICE_RATE)
    audio = bytearray()
    words, sentences = [], []
    async for chunk in communicate.stream():
        ctype = chunk["type"]
        if ctype == "audio":
            audio.extend(chunk["data"])
        elif ctype in ("WordBoundary", "SentenceBoundary"):
            start = chunk["offset"] / 1e7        # edge-tts uses 100-ns ticks
            end = start + chunk["duration"] / 1e7
            (words if ctype == "WordBoundary" else sentences).append(
                (start, end, chunk["text"])
            )
    return bytes(audio), words, sentences


def _caption_cues(words, sentences, offset, fallback_text, block_dur):
    """Build short word-grouped caption cues for one block, shifted by `offset`.

    Prefers real word timings; falls back to slicing each sentence's time span
    evenly across its words (edge-tts gives only sentence-level timing); finally
    falls back to showing the whole block across its duration.
    """
    cues = []
    if words:
        for j in range(0, len(words), WORDS_PER_CAPTION):
            grp = words[j:j + WORDS_PER_CAPTION]
            cues.append([offset + grp[0][0], offset + grp[-1][1],
                         " ".join(w[2] for w in grp)])
    elif sentences:
        for s, e, text in sentences:
            toks = text.split()
            if not toks:
                continue
            span = max(e - s, 0.1)
            n = len(toks)
            for j in range(0, n, WORDS_PER_CAPTION):
                grp = toks[j:j + WORDS_PER_CAPTION]
                gs = s + (j / n) * span
                ge = s + (min(j + WORDS_PER_CAPTION, n) / n) * span
                cues.append([offset + gs, offset + ge, " ".join(grp)])
    else:
        cues.append([offset, offset + block_dur, fallback_text])
    return cues


def _concat_audio(parts, output_path, work_dir):
    """Concatenate per-block mp3s into one voiceover track."""
    list_file = Path(work_dir) / "voice_concat.txt"
    list_file.write_text(
        "\n".join(f"file '{os.path.abspath(p)}'" for p in parts), encoding="utf-8"
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c", "copy", output_path],
        check=True, capture_output=True,
    )


async def _generate_voice_and_captions(script, voice_path, work_dir):
    """Synthesize each block, stitch into one voiceover, and return
    (durations, captions) where durations[i] is the on-screen time for image i
    and captions is a list of [start, end, text] cues for the whole video."""
    parts, durations, captions = [], [], []
    cursor = 0.0
    for i, block in enumerate(script):
        audio, words, sentences = await _synth_block(block["text"])
        bpath = Path(work_dir) / f"voice_{i:02d}.mp3"
        bpath.write_bytes(audio)
        dur = _mp3_duration(str(bpath))
        parts.append(str(bpath))
        durations.append(dur)
        captions.extend(_caption_cues(words, sentences, cursor, block["text"], dur))
        cursor += dur

    # Keep each caption on screen until the next one appears (no flicker in pauses).
    for k in range(len(captions) - 1):
        captions[k][1] = captions[k + 1][0]

    _concat_audio(parts, voice_path, work_dir)
    print(f"  [voice] saved → {voice_path} ({cursor:.1f}s)")
    return durations, captions


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _write_captions_ass(captions, path):
    """Write burned-in caption styling + cues as an ASS subtitle file.

    Big bold uppercase white text with a heavy black outline, centered in the
    lower third — the standard high-retention Shorts caption look, readable even
    over the white sketchbook background.
    """
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Arial Black,84,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,1,7,4,2,80,80,560,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for start, end, text in captions:
        safe = text.strip().upper().replace("\n", " ").replace("{", "(").replace("}", ")")
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{safe}"
        )
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def _ff_path(p: str) -> str:
    """Absolute path with forward slashes — safe inside ffmpeg concat lists on
    both Windows and Linux."""
    return os.path.abspath(p).replace("\\", "/")


def build_video(script, durations, output_path, work_dir) -> str:
    """Assemble images onto a vertical white canvas, each held for the actual
    spoken duration of its narration block.

    Each image is rendered to its own clip with -loop/-t and the clips are then
    concatenated. (The concat demuxer's per-still `duration` directive is honored
    inconsistently across ffmpeg builds — it silently collapsed every image after
    the first to ~0s — so we never rely on it for stills.)
    """
    # Each still gets a slow Ken Burns zoom (alternating in/out per scene) so the
    # video never sits static — the main thing that made the reused-creature
    # composite feel repetitive. Scale to width, pad onto the white 1080x1920
    # page, then zoompan.
    clip_paths = []
    for i, block in enumerate(script):
        duration = max(durations[i], 0.5)
        frames = max(1, int(round(duration * FPS)))
        inc = 0.14 / frames  # total zoom travel over the clip
        if i % 2 == 0:
            z = f"min(1.0+{inc:.6f}*in,1.14)"      # zoom in
        else:
            z = f"max(1.14-{inc:.6f}*in,1.0)"      # zoom out
        vf = (
            f"scale={VIDEO_W}:-2,"
            f"pad={VIDEO_W}:{VIDEO_H}:(ow-iw)/2:{ART_TOP}:white,"
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d=1:s={VIDEO_W}x{VIDEO_H}:fps={FPS},setsar=1"
        )
        clip = Path(work_dir) / f"clip_{i:02d}.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-framerate", str(FPS), "-t", f"{duration:.3f}",
            "-i", os.path.abspath(block["image_path"]),
            "-vf", vf,
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-r", str(FPS),
            str(clip),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        clip_paths.append(str(clip))

    concat_file = Path(work_dir) / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{_ff_path(p)}'" for p in clip_paths), encoding="utf-8"
    )
    video_only = Path(work_dir) / "video_only.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
         "-c", "copy", str(video_only)],
        check=True, capture_output=True,
    )
    print(f"  [video] silent vertical video built ({len(clip_paths)} clips)")
    return str(video_only)


def finalize(video_path, voice_path, music_path, ass_name, output_path, work_dir):
    """Burn in captions and mix voiceover + music in a single pass.

    Run with cwd=work_dir so the ASS filter can reference the subtitle file by a
    plain relative name — this avoids ffmpeg's painful Windows drive-colon
    escaping inside filtergraph arguments.
    """
    has_music = music_path and Path(music_path).exists()
    video_abs = os.path.abspath(video_path)
    voice_abs = os.path.abspath(voice_path)

    if has_music:
        cmd = [
            "ffmpeg", "-y",
            "-i", video_abs,
            "-i", voice_abs,
            "-i", os.path.abspath(music_path),
            "-filter_complex",
            f"[0:v]ass={ass_name}[v];"
            "[1:a]volume=1.0[voice];[2:a]volume=0.15[music];"
            "[voice][music]amix=inputs=2:duration=first[a]",
            "-map", "[v]", "-map", "[a]",
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", video_abs,
            "-i", voice_abs,
            "-filter_complex", f"[0:v]ass={ass_name}[v]",
            "-map", "[v]", "-map", "1:a",
        ]
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        os.path.abspath(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, cwd=work_dir)
    print(f"  [final] captions burned + audio mixed → {output_path}")


def assemble_video(script: list[dict], topic: str, output_dir: str) -> str:
    """Full assembly pipeline. Returns path to final video."""
    work_dir = Path(output_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    voice_path = str(work_dir / "voiceover.mp3")
    music_path = str(work_dir / "music.mp3")
    ass_name = "captions.ass"
    ass_path = str(work_dir / ass_name)
    final_path = str(work_dir / "final.mp4")

    # Voiceover + word-synced captions + per-image durations.
    print("  [voice] generating + timing captions...")
    durations, captions = asyncio.run(
        _generate_voice_and_captions(script, voice_path, str(work_dir))
    )
    _write_captions_ass(captions, ass_path)
    print(f"  [captions] {len(captions)} cues → {ass_path}")

    # Background music.
    print("  [music] fetching from Jamendo...")
    if not fetch_music(music_path):
        music_path = None

    # Silent vertical video, then burn captions + mix audio.
    print("  [video] assembling images...")
    video_only = build_video(script, durations, final_path, str(work_dir))

    print("  [final] burning captions + mixing audio...")
    finalize(video_only, voice_path, music_path, ass_name, final_path, str(work_dir))

    print(f"  [done] final video → {final_path}")
    return final_path


if __name__ == "__main__":
    import json, sys
    topic = sys.argv[1] if len(sys.argv) > 1 else "Skinwalker"

    with open(f"output/{topic.lower().replace(' ', '_')}/script_with_images.json") as f:
        script = json.load(f)

    out = assemble_video(script, topic, f"output/{topic.lower().replace(' ', '_')}")
    print(f"Final: {out}")