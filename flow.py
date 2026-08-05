"""
Flow-Local (Ubuntu/Linux Edition)
A free, fully local transcription and text-cleanup daemon[cite: 1, 3].
"""

import atexit
import os
import signal
import subprocess
import sys
import threading
import time
import traceback

import numpy as np
import sounddevice as sd
import pyperclip
import requests
from faster_whisper import WhisperModel

# ---------------------------------------------------------------------------
# CONFIG - tweak these
# ---------------------------------------------------------------------------

SAMPLE_RATE = 16000
CLEANUP_ENABLED = True       
OLLAMA_MODEL = "qwen2.5:3b" # Best balance of speed/quality for CPU[cite: 1, 3]
OLLAMA_URL = "http://localhost:11434/api/generate"[cite: 1]

WHISPER_MODEL_SIZE = "base"   # Optimized for CPU usage[cite: 1, 3]
WHISPER_DEVICE = "cpu"        # Stick to CPU for Intel integrated graphics[cite: 1, 3]
WHISPER_COMPUTE_TYPE = "int8" # Fastest option on CPU[cite: 1, 3]

LANGUAGE = None  

# Expanded hints to improve recognition for specific technical jargon.
VOCABULARY_HINTS = "Ollama, Whisper, faster-whisper, Flow, Wispr, GPU, Python, C++, VHDL, Verilog, INSA"[cite: 1]

DEBUG_MODE = True  
MIN_WORDS_FOR_CLEANUP = 4[cite: 1]

PID_FILE = "/tmp/flow_local.pid"

# ---------------------------------------------------------------------------

def log(msg):
    if not DEBUG_MODE:
        return
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# --- Singleton & Daemon Setup ------------------------------------------------
def release_lock():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception:
        pass

def acquire_lock_or_exit():
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            # Check if process is actually running
            os.kill(old_pid, 0)
            log(f"Flow is already running (PID {old_pid}). Exiting.")
            sys.exit(0)
        except OSError:
            log("Stale PID file found. Taking over.")
        except Exception as e:
            log(f"PID read error: {e}")

    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(release_lock)
    log(f"Daemon lock acquired (PID {os.getpid()}).")

acquire_lock_or_exit()

# --- Audio & Model Setup -----------------------------------------------------
try:
    default_input = sd.default.device[0]
    log(f"Default input device index: {default_input}")
except Exception as e:
    log(f"!!! ERROR querying audio devices: {e}")

log(f"Loading Whisper model '{WHISPER_MODEL_SIZE}' (device={WHISPER_DEVICE})...")[cite: 1]
try:
    model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE)[cite: 1]
    log("Whisper model loaded OK.")
except Exception as e:
    log(f"!!! FATAL: Whisper model failed to load: {e}")
    sys.exit(1)

recording = False
audio_frames = []
callback_count = 0

def audio_callback(indata, frames, time_info, status):
    global callback_count
    if recording:
        audio_frames.append(indata.copy())
        callback_count += 1

try:
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=audio_callback[cite: 1]
    )
    stream.start()
    log("Audio input stream started OK.")
except Exception as e:
    log(f"!!! FATAL: could not start audio stream: {e}")
    sys.exit(1)

# --- Processing Pipeline -----------------------------------------------------
def process_audio_data(frames_to_process):
    audio = np.concatenate(frames_to_process, axis=0)[cite: 1]
    audio_float = (audio.astype(np.float32) / 32768.0).flatten()[cite: 1]
    duration = len(audio_float) / SAMPLE_RATE[cite: 1]
    
    if duration < 0.3:[cite: 1]
        log("Recording too short, skipped.")
        subprocess.run(["notify-send", "Flow", "Recording too short."])
        return

    log("Transcribing...")
    try:
        segments, info = model.transcribe(
            audio_float, language=LANGUAGE, beam_size=5, initial_prompt=VOCABULARY_HINTS[cite: 1]
        )
        segments = list(segments)
    except Exception as e:
        log(f"Transcription error: {e}")
        return

    text = " ".join(seg.text.strip() for seg in segments).strip()[cite: 1]
    if not text:
        return
    log(f"[RAW]: {text}")

    if CLEANUP_ENABLED and len(text.split()) >= MIN_WORDS_FOR_CLEANUP:[cite: 1]
        text = cleanup_text(text, detected_language=info.language)[cite: 1]
        log(f"[CLEANED]: {text}")

    paste_text(text)

LANGUAGE_NAMES = {
    "en": "English", "fr": "French", "es": "Spanish", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ar": "Arabic",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ru": "Russian",
}[cite: 1]

def cleanup_text(raw_text: str, detected_language: str = None) -> str:
    lang_name = LANGUAGE_NAMES.get(detected_language, detected_language or "the original language")[cite: 1]
    prompt = (
        f"CRITICAL RULE: the text below is in {lang_name}. You MUST reply ONLY in {lang_name}. "
        f"Never translate to English or any other language, even partially.\n\n"
        "Task: lightly clean up this dictated speech. Be CONSERVATIVE - when in doubt, "
        "leave the words unchanged. You may ONLY:\n"
        "1. Remove pure disfluency sounds like 'um', 'uh', 'euh' (not real words)\n"
        "2. Remove a false start ONLY if the speaker clearly restarted the same sentence "
        "(e.g. 'I want- I need' -> 'I need')\n"
        "3. Fix punctuation and capitalization\n\n"
        "You must NEVER remove, merge, or shorten real words - including greetings, "
        "repeated words, or short phrases that might look redundant. If the speaker said "
        "two real words like 'Hi, hello', KEEP BOTH - that is content, not filler. "
        "Do not add new information. Do not summarize or paraphrase. "
        "Output ONLY the cleaned text, nothing else - no preamble, no quotes, no translation.\n\n"
        f"Dictated text ({lang_name}): {raw_text}\n\n"
        f"Reminder: your entire response must be in {lang_name}, and must preserve every "
        f"real word from the input."
    )[cite: 1]
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0}},
            timeout=20,
        )[cite: 1]
        resp.raise_for_status()
        return resp.json().get("response", "").strip() or raw_text[cite: 1]
    except Exception as e:
        log(f"Ollama error: {e}")
        return raw_text

def paste_text(text: str):
    try:
        pyperclip.copy(text)[cite: 1]
        time.sleep(0.1)
        
        # Check if running under Wayland or X11 to use the correct automation tool
        if os.environ.get('WAYLAND_DISPLAY'):
            subprocess.run(["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"], check=True)
        else:
            subprocess.run(["xdotool", "key", "ctrl+v"], check=True)
        log(">>> Pasted text via automated Ctrl+V.")
    except Exception as e:
        log(f"Paste automation failed: {e}. Text is in clipboard.")
        subprocess.run(["notify-send", "Flow", "Paste failed. Text copied to clipboard."])

# --- Signal Handling (The Toggle) --------------------------------------------
def handle_toggle(signum, frame):
    global recording, audio_frames, callback_count
    if not recording:
        audio_frames = []
        callback_count = 0
        recording = True
        log(">>> RECORDING STARTED")
        subprocess.run(["notify-send", "-t", "1000", "Flow-Local", "🔴 Recording Started"])
    else:
        recording = False
        log("<<< RECORDING STOPPED, processing...")
        subprocess.run(["notify-send", "-t", "1000", "Flow-Local", "⏳ Processing..."])
        # Process in a thread so the signal handler returns immediately
        frames_copy = list(audio_frames)
        threading.Thread(target=process_audio_data, args=(frames_copy,)).start()

signal.signal(signal.SIGUSR1, handle_toggle)

def main():
    log(f"Daemon ready. Send SIGUSR1 to PID {os.getpid()} to toggle recording.")
    # Keep the main thread alive
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()