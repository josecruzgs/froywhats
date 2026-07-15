#!/usr/bin/env python3
"""Transcribe los videos de Froy (audio -> texto) con mlx-whisper, local y en español."""
import os, sys, glob, subprocess, tempfile, time
import soundfile as sf
import numpy as np
import mlx_whisper

MODEL = "mlx-community/whisper-large-v3-turbo"
VIDEOS_DIR = "videos"
OUT_DIR = "transcripts"
FFMPEG = "ffmpeg"  # symlink en .venv/bin

os.makedirs(OUT_DIR, exist_ok=True)

# Videos únicos: ignorar duplicados "(1)"
videos = sorted(p for p in glob.glob(os.path.join(VIDEOS_DIR, "*.MOV")) if "(1)" not in p)
print(f"{len(videos)} videos a transcribir\n", flush=True)

def load_audio(path):
    """Decodifica el audio a 16kHz mono float32 usando ffmpeg."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav = tmp.name
    subprocess.run([FFMPEG, "-y", "-i", path, "-ac", "1", "-ar", "16000",
                    "-vn", wav], check=True, capture_output=True)
    data, _ = sf.read(wav, dtype="float32")
    os.remove(wav)
    return data

combined = []
for i, vid in enumerate(videos, 1):
    name = os.path.splitext(os.path.basename(vid))[0]
    out_txt = os.path.join(OUT_DIR, f"{name}.txt")
    if os.path.exists(out_txt) and os.path.getsize(out_txt) > 0:
        print(f"[{i}/{len(videos)}] {name} — ya existe, saltando", flush=True)
        with open(out_txt) as f:
            combined.append(f"### {name}\n{f.read().strip()}\n")
        continue
    t0 = time.time()
    print(f"[{i}/{len(videos)}] {name} — transcribiendo...", flush=True)
    try:
        audio = load_audio(vid)
        res = mlx_whisper.transcribe(audio, path_or_hf_repo=MODEL,
                                     language="es", fp16=True)
        text = res["text"].strip()
        with open(out_txt, "w") as f:
            f.write(text + "\n")
        combined.append(f"### {name}\n{text}\n")
        print(f"    OK ({time.time()-t0:.0f}s, {len(text)} chars)", flush=True)
    except Exception as e:
        print(f"    ERROR: {e}", flush=True)

# Archivo combinado para lectura
with open(os.path.join(OUT_DIR, "_TODAS.md"), "w") as f:
    f.write("# Transcripciones de los videos de Froy\n\n")
    f.write("\n".join(combined))
print("\nListo. Transcripciones en transcripts/", flush=True)
