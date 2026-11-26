# Jarvis

Jarvis accepts typed requests or spoken English, sends the text to Codex, and plays a brief English reply with Piper’s Jarvis voice. Jarvis addresses you as “sir” and skips Markdown formatting and URLs when speaking.

## Setup

From this folder, create the environment and download the voice.

```sh
uv venv .venv
UV_CACHE_DIR="$PWD/.uv-cache" uv pip install -r requirements.txt
mkdir -p .jarvis/voices
base=https://huggingface.co/vishalrao8/piper-jarvis-high/resolve/main
for name in en_GB-jarvis-high.onnx en_GB-jarvis-high.onnx.json; do
    curl -fLS "$base/$name" -o ".jarvis/voices/$name" || break
done
```

You need the Codex CLI signed in to ChatGPT and a running PipeWire desktop session with `pw-play`, plus FFmpeg for reply loudness. Piper runs locally. Codex needs internet access and receives the request as text.

## Run

```sh
.venv/bin/python jarvis.py 'Preséntate en una frase.'
```

For a hands-free turn, run this from your normal PipeWire desktop session, wait for the chime and say your request in English. Jarvis stops after about one second of silence, with a 20-second recording limit.

```sh
.venv/bin/python jarvis.py --listen
```

Microphone mode needs `pw-record` and Voxtype with Whisper `base.en` installed. Check with `voxtype info models`. The English-only model applies to microphone input. Typed Spanish prompts still work.

Jarvis shares a conversation across requests during the same boot. The first request after reboot starts a new conversation with the Jarvis identity and voice instructions. To reset during a boot, move `.jarvis/codex-session-$(cat /proc/sys/kernel/random/boot_id)` aside. Codex stores previous conversations in its usual session storage.

For longer tasks, Jarvis can speak the first short progress message while Codex continues working. Codex uses low reasoning effort to reduce reply latency.

Each chat starts read-only using Codex's on-request approval policy. When Codex requests permission, the Jarvis window shows the proposed file changes, command, or access and lets you approve or deny it. **Approve once** applies to the displayed action. If Codex asks for a permission grant, **Approve for turn** applies only to the current request. Closing the overlay, losing the desktop connection, or leaving a prompt unanswered for 60 seconds causes it to be denied. With no Wayland desktop available, approval requests are declined.

Run the tests with `python -B -m unittest -v`.

## Wake phrase

Start the listener with:

```sh
.wake-venv/bin/python jarvis.py --wake
```

Say **“Jarvis”** when the listener is ready, then wait for the chime before speaking your English request. Press Ctrl+C to stop. Wake detection runs locally and only the transcribed request goes to Codex.

After a reply, start speaking within **two seconds** to ask a follow-up without saying “Jarvis” again. If you start in time, Jarvis lets you finish. Wake-word detection resumes as soon as the follow-up window closes. **Shift+F1** still starts the next request immediately.

To recreate the wake environment, install Python 3.12 and OpenWakeWord in this folder. The existing text and `--listen` commands can still use `.venv/`. Install the ONNX dependencies explicitly because OpenWakeWord 0.6.0 also declares a TFLite dependency that has no Python 3.12 Linux wheel.

```sh
export UV_CACHE_DIR="$PWD/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/.jarvis/python"
uv python install 3.12
uv venv --python 3.12 .wake-venv
uv pip install --python .wake-venv/bin/python -r requirements.txt scipy scikit-learn tqdm requests
uv pip install --python .wake-venv/bin/python --no-deps openwakeword==0.6.0

mkdir -p .jarvis/wake-models
base=https://github.com/dscripka/openWakeWord/releases/download/v0.5.1
for name in melspectrogram embedding_model; do
    curl -fLS "$base/$name.onnx" -o ".jarvis/wake-models/$name.onnx" || break
done
```

The single-word model comes from [Tater Wake Words](https://github.com/TaterTotterson/Tater-Wake-Words/issues/403).

```sh
curl -fLS https://raw.githubusercontent.com/TaterTotterson/Tater-Wake-Words/main/microWakeWordsV9/jarvis.oww.onnx -o .jarvis/wake-models/jarvis.oww.onnx
```

## Start at desktop login

The installed `jarvis.service` starts with the systemd user graphical session and stops when that session ends. It opens no terminal. On Omarchy, Quickshell shows a small desktop overlay during recording, transcription, and replies.

- Press **Shift+F1** to start recording. Press it again or click **Send** to submit immediately. Wake-word recording also submits automatically after a pause.
- Press **Shift+F1** or click **Silence** during a reply to stop playback and mute the rest of that turn. Codex can finish the request in the background.
- The window displays the full reply even when audio is silenced. It stays visible through follow-up listening and after Jarvis goes idle. Click **Close** to dismiss it or start a new request.

The Omarchy Hyprland binding runs `systemctl --user kill --kill-whom=main --signal=USR1 jarvis.service`.

```sh
systemctl --user status jarvis.service
systemctl --user stop jarvis.service    # Stop listening until started or next login
systemctl --user start jarvis.service   # Resume listening
journalctl --user -u jarvis.service -n 30 --no-pager
```

To turn off startup and stop the listener, run `systemctl --user disable --now jarvis.service`. To enable it again, run `systemctl --user enable --now "$PWD/jarvis.service"` from the Jarvis folder. The unit points to this checkout. Update its paths and reinstall it if the folder moves.
