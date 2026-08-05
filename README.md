# Flow-Local

A free, fully local clone of Wispr Flow for Windows. Hold a hotkey, talk,
release — it transcribes with a local Whisper model, cleans the text up
with a local LLM, and pastes it wherever your cursor is. No cloud, no
subscription, no data leaving your machine.

## 1. Install Python dependencies

Requires Python 3.9+ (3.10/3.11 recommended).

```
pip install -r requirements.txt
```

Note: `keyboard` needs to run with **administrator privileges** on Windows
to catch global hotkeys system-wide. Run your terminal (or the script) as admin.

## 2. Install Ollama (for AI cleanup)

Download and install from https://ollama.com (Windows installer).

Then pull a small, fast local model:

```
ollama pull llama3.2:3b
```

This is a ~2GB model that runs comfortably on CPU. If you have a decent
GPU and want better quality, try `ollama pull llama3.1:8b` instead and
update `OLLAMA_MODEL` in `flow.py`.

Ollama runs a local server automatically at `http://localhost:11434` —
you don't need to keep a terminal open for it once it's installed as a
service, but the app must be running (check the tray icon).

## 3. First run

```
python flow.py
```

The first run will download the Whisper model (`small` by default, ~500MB).
After that it loads instantly from cache.

## 4. Use it

- Hold **Right Ctrl** (default hotkey — change `HOTKEY` in `flow.py`)
- Speak
- Release the key
- Cleaned-up text gets pasted into whatever field currently has focus

## Tuning

All the knobs are at the top of `flow.py`:

| Setting | What it does |
|---|---|
| `HOTKEY` | Which key to hold. Try `"f13"` or a rarely-used key if Right Ctrl conflicts with something. |
| `WHISPER_MODEL_SIZE` | `tiny`/`base`/`small`/`medium`/`large-v3`. Bigger = more accurate, slower. `base` is the default here since it's tuned for CPU-only systems. |
| `WHISPER_DEVICE` / `WHISPER_COMPUTE_TYPE` | Leave as `"cpu"` / `"int8"` on Intel integrated graphics (Iris Xe, UHD, etc.) — `faster-whisper` only accelerates on NVIDIA CUDA GPUs, so an Intel iGPU won't help here and there's nothing to configure for it. |
| `CLEANUP_ENABLED` | Set `False` to skip the LLM step entirely (raw transcription only, faster). |
| `OLLAMA_MODEL` | Any model you've pulled with `ollama pull`. Bigger models clean up text better but respond slower. |

## Performance notes (Intel iGPU / CPU-only systems)

Since Intel Iris Xe and similar integrated graphics aren't supported for
acceleration by either `faster-whisper` or Ollama, everything here runs
on the CPU. To keep things responsive:

- `WHISPER_MODEL_SIZE = "base"` is the sweet spot for CPU — noticeably
  faster than `small`, still solid accuracy for clear speech.
- `OLLAMA_MODEL = "llama3.2:3b"` is a good CPU-friendly cleanup model.
  Avoid 8B+ models unless you don't mind a few extra seconds per phrase.
- Expect roughly 1-3 second turnaround per sentence, more on longer
  dictations. This is slower than Wispr Flow's cloud pipeline, but free
  and fully private.
- If it still feels too slow: drop to `WHISPER_MODEL_SIZE = "tiny"`,
  or set `CLEANUP_ENABLED = False` to skip the LLM step entirely.
- If you want the "auto-submit on pause" behavior Wispr Flow has, that's
  a further step (voice activity detection to auto-stop recording after
  silence) — ask if you want that added.

## Running it in the background permanently

Once you're happy with it, you can:
- Add a shortcut to `flow.py` (via a `.bat` file calling `pythonw.exe flow.py`)
  to your Windows Startup folder so it runs on login without a console window.
- Or wrap it as a proper background service with `pystray` for a tray icon
  and on/off toggle — happy to build that next if you want it.

## One-click launch (no manual venv activation)

A `run_flow.bat` file is included. It activates the venv and starts Flow
with `pythonw.exe` (no console window), logging any output to `flow_log.txt`
in the same folder for troubleshooting.

**Double-click `run_flow.bat` and Flow starts.** That's the whole manual
workflow reduced to one click.

Because the global hotkey needs admin rights, right-click the `.bat` and
choose **"Run as administrator"** the first few times, or set it up to
always run elevated (see below) so you never get the UAC prompt.

### Make a desktop shortcut

1. Right-click `run_flow.bat` → **Create shortcut**
2. Move the shortcut to your Desktop, rename it to "Flow"
3. Right-click the shortcut → **Properties** → **Advanced** → check
   **"Run as administrator"**
4. (Optional) Properties → **Change Icon** to give it a proper app icon

Now double-clicking that desktop icon launches Flow like any installed app.

### Auto-start on login (fully automatic, no clicking at all)

Use Windows Task Scheduler so it starts silently and elevated at login,
with no UAC prompt:

1. Open **Task Scheduler** → **Create Task** (not "Create Basic Task")
2. **General tab**: name it "Flow", check **"Run with highest privileges"**,
   set "Configure for" to your Windows version
3. **Triggers tab** → **New** → **"At log on"** → OK
4. **Actions tab** → **New** → Action: "Start a program" →
   Program/script: browse to `run_flow.bat` → OK
5. **Conditions tab**: uncheck "Start the task only if the computer is on AC power"
   if this is a laptop
6. Save. You may be prompted once for your Windows password to store the
   elevated credentials.

From now on, Flow starts automatically and silently every time you log in —
no double-clicking, no console window, no UAC prompt. Check `flow_log.txt`
in the project folder if you ever need to confirm it's running or debug.

To stop it, open Task Manager and end the `pythonw.exe` process, or disable
the scheduled task.
