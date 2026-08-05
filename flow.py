"""
Flow-Local: a free, fully local clone of Wispr Flow.

Hold a hotkey -> records mic -> transcribes with local Whisper model
-> cleans up text with a local LLM (via Ollama) -> pastes into whatever
app/field currently has focus.

Everything runs on your machine. No cloud calls, no accounts, no cost.

DEBUG BUILD: prints verbose status at every step so you can see exactly
where things break.
"""

import atexit
import os
import subprocess
import sys
import time
import traceback

import numpy as np
import sounddevice as sd
import keyboard
import pyperclip
import requests
from faster_whisper import WhisperModel

# ---------------------------------------------------------------------------
# CONFIG - tweak these
# ---------------------------------------------------------------------------

HOTKEY = "ctrl droite"       # key to hold down to dictate
# NOTE: on Windows, the `keyboard` library reports LOCALIZED key names pulled
# from your OS keyboard layout, not fixed English ones. On this French layout,
# right Ctrl reports as "ctrl droite" (confirmed via debug run) - left Ctrl
# reports as plain "ctrl", a different string, so this won't fire on left Ctrl.
# If you ever change layouts or keys, re-enable the debug hook below to see
# the exact name your OS reports before updating this value.
SAMPLE_RATE = 16000

CLEANUP_ENABLED = True       # set False for raw transcription only
OLLAMA_MODEL = "qwen2.5:3b" # change this to match whatever model you `ollama pull`,
                              # e.g. "qwen2.5:7b" or "qwen2.5:3b" - must match the exact
                              # name shown by `ollama list`
OLLAMA_URL = "http://localhost:11434/api/generate"

WHISPER_MODEL_SIZE = "base"   # tiny / base / small / medium / large-v3
WHISPER_DEVICE = "cpu"        # Intel iGPUs (Iris Xe) aren't supported by faster-whisper - stick to cpu
WHISPER_COMPUTE_TYPE = "int8" # int8 is the fastest option on CPU

LANGUAGE = None  # None = auto-detect per recording. Set to "fr" to force French,
                 # "en" to force English, etc. Auto-detect is more flexible if you
                 # switch languages, but pinning it is more reliable for short clips.

# Whisper often mishears uncommon proper nouns / technical terms (e.g. "Ollama")
# and substitutes a phonetically similar common word instead. Listing likely terms
# here as a vocabulary hint measurably improves recognition of them, in EITHER
# language you use. Add anything project-specific, names, jargon, etc.
VOCABULARY_HINTS = "Ollama, Whisper, faster-whisper, Flow, Wispr, GPU, Python"

DEBUG_LOG_ALL_KEYS = False    # if True, logs raw press/release events for the
                              # HOTKEY only (not every key) - useful to confirm
                              # the global hook is actually catching it

DEBUG_MODE = False  # master switch: True = log everything (all the [HH:MM:SS] lines),
                    # False = completely silent, logs nothing at all

# ---------------------------------------------------------------------------


def log(msg):
    """Timestamped, flushed print - does nothing at all if DEBUG_MODE is False."""
    if not DEBUG_MODE:
        return
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


log("=== Flow-Local starting ===")
log(f"Python: {sys.version}")

# --- Singleton lock ----------------------------------------------------------
# Windows Task Scheduler's "At log on" trigger also fires when you UNLOCK your
# screen, not just on a true fresh login. Without this check, every lock/unlock
# would spawn a second full instance (duplicate audio stream, duplicate hotkey
# hook, competing clipboard writes). This makes sure only one instance ever runs.

LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "flow.lock")


def is_pid_running(pid: int) -> bool:
    try:
        output = subprocess.check_output(
            ["tasklist", "/FI", f"PID eq {pid}"], text=True, stderr=subprocess.DEVNULL
        )
        return str(pid) in output
    except Exception:
        return False


def release_lock():
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass


def acquire_lock_or_exit():
    if os.path.exists(LOCK_FILE):
        try:
            with open(LOCK_FILE, "r") as f:
                old_pid = int(f.read().strip())
            if is_pid_running(old_pid):
                log(f"Flow is already running (PID {old_pid}). This is a duplicate "
                    f"launch (likely a screen lock/unlock re-triggering the scheduled "
                    f"task) - exiting immediately without loading anything.")
                sys.exit(0)
            else:
                log(f"Found a stale lock file (PID {old_pid} is no longer running) - "
                    f"taking over as the active instance.")
        except Exception as e:
            log(f"(lock file unreadable, taking over: {e})")

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(release_lock)
    log(f"Singleton lock acquired (PID {os.getpid()}).")


acquire_lock_or_exit()

# --- Audio device check -----------------------------------------------------
try:
    devices = sd.query_devices()
    default_input = sd.default.device[0]
    log(f"Default input device index: {default_input}")
    if default_input is None or default_input == -1:
        log("!!! WARNING: no default input device detected. Mic may not be picked up.")
    else:
        log(f"Default input device: {devices[default_input]['name']}")
except Exception as e:
    log(f"!!! ERROR querying audio devices: {e}")
    traceback.print_exc()

# --- Whisper model load ------------------------------------------------------
log(f"Loading Whisper model '{WHISPER_MODEL_SIZE}' (device={WHISPER_DEVICE}, compute={WHISPER_COMPUTE_TYPE})...")
log("First run downloads the model, this can take a few minutes depending on connection.")
try:
    model = WhisperModel(WHISPER_MODEL_SIZE, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE)
    log("Whisper model loaded OK.")
except Exception as e:
    log(f"!!! FATAL: Whisper model failed to load: {e}")
    traceback.print_exc()
    sys.exit(1)

recording = False
audio_frames = []
callback_count = 0


def audio_callback(indata, frames, time_info, status):
    global callback_count
    if status:
        log(f"!!! Audio callback status flag: {status}")
    if recording:
        audio_frames.append(indata.copy())
        callback_count += 1
        if callback_count % 20 == 0:  # don't spam, print every ~20 chunks
            level = np.abs(indata).mean()
            log(f"  ...capturing audio (chunk {callback_count}, avg level {level:.1f})")


try:
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=audio_callback
    )
    stream.start()
    log("Audio input stream started OK.")
except Exception as e:
    log(f"!!! FATAL: could not start audio input stream: {e}")
    traceback.print_exc()
    sys.exit(1)


def start_recording():
    global recording, audio_frames, callback_count
    if recording:
        return
    audio_frames = []
    callback_count = 0
    recording = True
    log(">>> HOTKEY PRESSED - recording started")


def stop_recording_and_process():
    global recording
    if not recording:
        return
    recording = False
    log("<<< HOTKEY RELEASED - recording stopped, processing...")

    if not audio_frames:
        log("!!! No audio frames captured at all. Mic input is not reaching the callback.")
        return

    audio = np.concatenate(audio_frames, axis=0)
    audio_float = (audio.astype(np.float32) / 32768.0).flatten()
    duration = len(audio_float) / SAMPLE_RATE
    peak = np.abs(audio_float).max() if len(audio_float) else 0.0
    log(f"Captured {duration:.2f}s of audio, peak amplitude {peak:.4f} (near 0 = mic likely muted/silent)")

    if duration < 0.3:
        log("!!! Recording too short (<0.3s), skipped. Hold the key a bit longer.")
        return

    if peak < 0.01:
        log("!!! WARNING: audio is almost silent. Check Windows mic permissions / correct input device / mic not muted.")

    log("Sending audio to Whisper for transcription...")
    t0 = time.time()
    try:
        segments, info = model.transcribe(
            audio_float, language=LANGUAGE, beam_size=5, initial_prompt=VOCABULARY_HINTS
        )
        segments = list(segments)  # force evaluation so we can log it
    except Exception as e:
        log(f"!!! ERROR during Whisper transcription: {e}")
        traceback.print_exc()
        return
    log(f"Whisper finished in {time.time() - t0:.2f}s, {len(segments)} segment(s). "
        f"Detected language: {info.language} (confidence {info.language_probability:.2f})")

    text = " ".join(seg.text.strip() for seg in segments).strip()

    if not text:
        log("!!! Whisper returned empty text (no speech detected in audio).")
        return

    log(f"[RAW TRANSCRIPT]: {text}")

    if CLEANUP_ENABLED:
        word_count = len(text.split())
        if word_count < MIN_WORDS_FOR_CLEANUP:
            log(f"Utterance is short ({word_count} words < {MIN_WORDS_FOR_CLEANUP}), skipping cleanup to avoid over-editing.")
        else:
            text = cleanup_text(text, detected_language=info.language)
            log(f"[CLEANED TEXT]: {text}")
    else:
        log("Cleanup disabled, using raw transcript.")

    paste_text(text)


LANGUAGE_NAMES = {
    "en": "English", "fr": "French", "es": "Spanish", "de": "German",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ar": "Arabic",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "ru": "Russian",
}


MIN_WORDS_FOR_CLEANUP = 4  # utterances shorter than this skip cleanup entirely -
                            # short phrases are where small models most often
                            # over-edit and delete real words by mistake


def cleanup_text(raw_text: str, detected_language: str = None) -> str:
    lang_name = LANGUAGE_NAMES.get(detected_language, detected_language or "the original language")
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
    )
    log(f"Calling Ollama at {OLLAMA_URL} with model '{OLLAMA_MODEL}' (language: {lang_name})...")
    t0 = time.time()
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=20,
        )
        resp.raise_for_status()
        cleaned = resp.json().get("response", "").strip()
        log(f"Ollama responded in {time.time() - t0:.2f}s.")
        return cleaned if cleaned else raw_text
    except requests.exceptions.ConnectionError as e:
        log(f"!!! ERROR: could not connect to Ollama at {OLLAMA_URL}. Is Ollama running? ({e})")
        return raw_text
    except Exception as e:
        log(f"!!! ERROR calling Ollama, falling back to raw text: {e}")
        traceback.print_exc()
        return raw_text


def paste_text(text: str):
    # Save whatever the user currently has on their clipboard so we can restore
    # it afterward - otherwise every dictation permanently overwrites their
    # clipboard and their next manual Ctrl+V pastes our text instead of theirs.
    previous_clipboard = None
    try:
        previous_clipboard = pyperclip.paste()
    except Exception as e:
        log(f"(could not read previous clipboard, will not restore it: {e})")

    try:
        pyperclip.copy(text)
        time.sleep(0.05)
        # Force-release modifiers first. Since we just handled a physical Ctrl
        # release event, the OS can occasionally think Ctrl is still held down
        # by the time we simulate Ctrl+V, leaving it "stuck" for whatever you
        # type next. Explicitly releasing clears that before AND after pasting.
        for mod in ("ctrl", "left ctrl", "right ctrl", HOTKEY, "shift", "alt"):
            try:
                keyboard.release(mod)
            except Exception:
                pass
        time.sleep(0.03)
        keyboard.send("ctrl+v")
        time.sleep(0.03)
        for mod in ("ctrl", "left ctrl", "right ctrl", HOTKEY):
            try:
                keyboard.release(mod)
            except Exception:
                pass
        log(">>> Pasted text via Ctrl+V into focused window.")
    except Exception as e:
        log(f"!!! ERROR while pasting: {e}")
        traceback.print_exc()
    finally:
        # Give the target app a moment to actually read the clipboard before
        # we swap it back, then restore the user's original clipboard content.
        if previous_clipboard is not None:
            time.sleep(0.15)
            try:
                pyperclip.copy(previous_clipboard)
                log("(clipboard restored to what it was before dictation)")
            except Exception as e:
                log(f"(could not restore previous clipboard: {e})")


def hotkey_filter(event):
    if not event.name or event.name.lower() != HOTKEY.lower():
        return
    if DEBUG_LOG_ALL_KEYS:
        log(f"  (debug) '{HOTKEY}' event seen: {event.event_type} (scan_code={event.scan_code})")
    if event.event_type == "down":
        start_recording()
    elif event.event_type == "up":
        stop_recording_and_process()


def main():
    try:
        keyboard.key_to_scan_codes(HOTKEY)
    except (ValueError, KeyError) as e:
        log(f"!!! FATAL: '{HOTKEY}' is not a key name the keyboard library recognizes: {e}")
        log("!!! Key names on Windows are LOCALIZED to your keyboard layout (e.g. French")
        log("!!! layouts report 'ctrl droite', not 'right ctrl'). Temporarily set")
        log("!!! DEBUG_LOG_ALL_KEYS's debug_any_key to log event.name for every key to find")
        log("!!! the exact string your OS reports, then use that exact string here.")
        sys.exit(1)

    try:
        # NOTE: we deliberately do NOT use keyboard.on_press_key/on_release_key here.
        # Those match by scan_code internally, and left/right Ctrl (and often left/right
        # Alt and Shift) share the SAME scan_code on most keyboards - only the resolved
        # `name` distinguishes them (e.g. "ctrl" vs "ctrl droite"). So we use a raw hook
        # and filter on event.name ourselves to correctly target only one specific key.
        keyboard.hook(hotkey_filter)
        log(f"Hotkey hook registered for '{HOTKEY}' (filtered by name, not scan_code).")
    except Exception as e:
        log(f"!!! FATAL: could not register hotkey hook: {e}")
        log("!!! This usually means the terminal is NOT running as Administrator.")
        traceback.print_exc()
        sys.exit(1)

    log(f"Ready. Hold '{HOTKEY}' to dictate, release to transcribe + paste.")
    log("Press Ctrl+C in this window to quit.")
    keyboard.wait()


if __name__ == "__main__":
    main()
