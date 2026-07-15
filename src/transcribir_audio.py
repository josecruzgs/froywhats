#!/usr/bin/env python3
"""Transcribe audios en el SERVIDOR (Linux) con faster-whisper (CPU, gratis, en español).

Distinto de `transcribir.py` (ese usa mlx-whisper, solo para la Mac). Este corre en el VPS.
El modelo se descarga la primera vez a /opt/froy/models (o WHISPER_CACHE).
"""
import os

AUDIO_EXTS = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aac", ".amr", ".aiff", ".flac", ".webm"}
_modelo = None

def es_audio(nombre):
    return os.path.splitext(nombre or "")[1].lower() in AUDIO_EXTS

def _cargar():
    global _modelo
    if _modelo is None:
        from faster_whisper import WhisperModel
        nombre = os.environ.get("WHISPER_MODELO", "small")
        cache = os.environ.get("WHISPER_CACHE", os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models"))
        os.makedirs(cache, exist_ok=True)
        _modelo = WhisperModel(nombre, device="cpu", compute_type="int8", download_root=cache)
    return _modelo

def transcribir(ruta):
    """Devuelve el texto transcrito de un archivo de audio (en español)."""
    segmentos, _ = _cargar().transcribe(ruta, language="es")
    return " ".join(s.text.strip() for s in segmentos).strip()
