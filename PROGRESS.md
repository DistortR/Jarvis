# Jarvis progress

## Approval requests

Jarvis now uses Codex App Server with read-only access and on-request approval. The overlay shows the proposed file changes, command, or requested access, with a Deny button and an approval button that states its scope. A live test denied a request to create a temporary file, verified that it stayed absent, and resumed the same conversation successfully.

## Voice and desktop controls

Jarvis now addresses the user as “sir,” speaks plain English without Markdown or URLs, and can read the first progress message while Codex works. Requests within the same PC boot share a conversation. The first request after reboot starts a new conversation with the Jarvis instructions.

Shift+F1 starts recording, submits the active recording immediately, or silences the active reply. The Quickshell overlay displays the current phase and a Send or Silence button. Wake-word recording still supports automatic submission after a pause.

Hyprland loaded the new binding without a conflict. A virtual microphone test with synthetic speech and a Codex stub completed transcription in 0.62 seconds after submission and confirmed that silencing stopped ongoing PipeWire playback. A separate live Codex arithmetic request returned its first progress message in 3.01 seconds and final text in 5.92 seconds. These are single-run timings.

## Current voice

Spoken replies get up to 3 dB of gain through FFmpeg, with a peak limiter to prevent clipping.

The default is now `en_GB-jarvis-high`, the British English Jarvis voice. The previous Cori and Claude files are still in `.jarvis/voices/`. See the [README](README.md) for download instructions. Verified local synthesis and PipeWire playback using the wake environment.

## Earlier English voice update

The original Codex conversation retained its Spanish instruction on resume. English mode now uses `.jarvis/codex-session-en` so its first turn starts with English instructions. The earlier conversation remains available through the original session file. A live test with the original English prompt and a Spanish follow-up produced two English replies and completed playback with Cori.

Jarvis now requests English replies even when the input is in Spanish. Piper uses Cori (British English, high), installed under `.jarvis/voices/`. The original Claude files are still available locally. The notes below describe the initial Spanish playback test.

## 2026-11-25 · Step 1 · Codex speaks

The first build stage is implemented in this folder. Jarvis takes a text prompt and speaks the reply in Mexican Spanish. See the [build plan](docs/superpowers/plans/2026-11-25-jarvis.md) and [setup instructions](README.md).

- `jarvis.py` accepts the prompt, calls the agent, synthesizes a temporary WAV with local Piper, and plays it through PipeWire using `pw-play`.
- `agent.py` sends the prompt to headless Codex through stdin and reads its final reply. Codex uses the existing ChatGPT sign-in and a read-only sandbox. Jarvis keeps a dedicated conversation ID in `.jarvis/codex-session`. Codex keeps its own session records in its usual storage.
- The voice pipeline accepts any backend that provides `reply(transcript)`. The command-line entry point currently creates a Codex backend. Configuration-based switching and an Ollama/Qwen backend are planned.
- Piper 1.8.0 is installed in `.venv/`. The `es_MX-claude-high` voice and its JSON configuration are in `.jarvis/voices/`. Setup required no sudo or API keys.

## Reproduce and debug

Run these from the Jarvis folder in your normal desktop session.

```sh
python -B -m unittest -v
.venv/bin/python jarvis.py 'Preséntate en una frase.'
```

Both unit tests passed. They cover passing a stub agent's reply to the voice pipeline and resuming the dedicated Codex session. Two live runs through a transient `systemd-run --user` service also exited successfully after playback. The second reply correctly recalled “luciérnaga” from the first. PipeWire accepted playback, but actual speaker audibility still needs confirmation.

- If Codex fails, run `codex login status` and check internet access. The CLI needs to run as the signed-in desktop user.
- If the current model is missing, check both `en_GB-jarvis-high.onnx` and `en_GB-jarvis-high.onnx.json` under `.jarvis/voices/`. The README has the download command.
- If playback succeeds but you hear nothing, check the default sink and mute state with `wpctl status` and `wpctl get-volume @DEFAULT_AUDIO_SINK@`. The restricted execution sandbox could not connect to PipeWire, while the desktop user service succeeded.
- If a stored session stops resuming, see the [README](README.md#run) for the current per-boot session filename and reset instructions.

## Hands-free recording

Run `.venv/bin/python jarvis.py --listen` from the desktop session. Wait for the chime and speak in English. Jarvis records 16 kHz mono PCM through PipeWire, trims the trailing silence, and passes a private temporary WAV to Voxtype. It explicitly uses local Whisper `base.en`, per the updated requirement. Typed Spanish input still works. Both modes use the current English Jarvis voice.

Recording stops after roughly one second of trailing silence, with a 20-second cap. Silence or transcription failure skips the Codex request. The current silence detector uses a fixed audio-level threshold. If a noisy room causes false starts, it may need a speech-specific detector.

The eight unit tests cover the original pipeline, session resumption, a mid-sentence pause, silence and short noise, an empty transcript, transcription failure, the recording cap, and filtering Voxtype’s WAV status lines out of the prompt. A desktop user service test used a temporary PipeWire input to verify silence and a spoken English fixture with a pause. Voxtype loaded `ggml-base.en.bin`, Codex answered, and Cori playback completed. The physical microphone still needs a short test with the user’s voice.

## Current wake phrase

Say **“Jarvis”** on its own. The listener uses the single-word Tater Wake Words OpenWakeWord model, with two consecutive detections at the published 0.89 threshold. It runs locally and requires no API key. Tested two full turns through a virtual microphone. The “Hey Jarvis” sample and ordinary speech did not activate it, and playback overlap did not trigger an extra turn. Recognition with the physical microphone still needs a real-voice check.

## Original wake phrase implementation

Run `.wake-venv/bin/python jarvis.py --wake`. Say “Hey Jarvis,” wait for the chime and ask your question in English. Press Ctrl+C to stop. The OpenWakeWord ONNX model runs locally. The idle microphone stream closes before the chime and opens again only after Cori finishes, so buffered audio from the previous turn is discarded.

Python 3.12, the wake virtual environment and all three ONNX models are stored within Jarvis. The [README](README.md#wake-phrase) documents the ONNX-only setup and model downloads. The installed OpenWakeWord version is 0.6.0.

All nine unit tests passed. On the desktop, a temporary PipeWire input exercised two complete wake turns with Codex and Cori. Ordinary speech did not activate the listener. A wake sample fed into the input during reply playback did not cause an extra turn. The physical microphone’s recognition at normal desk distance still needs a real-voice check.

## Desktop login

`jarvis.service` lives in the project and is enabled for the Hyprland desktop's `graphical-session.target` through systemd user symlinks. It runs `jarvis.py --wake` in the existing wake environment, starts after PipeWire and WirePlumber, and restarts on failure. The listener posts brief desktop status notifications while waiting and processing.

Verified that systemd loads the enabled unit from the project, that it stops and starts on demand, and that the restarted process waits for “Jarvis” with its PipeWire capture stream linked to the HyperX QuadCast S. The service was left running. A fresh login was not exercised during the test.

Use `systemctl --user stop jarvis.service` to stop listening and `systemctl --user start jarvis.service` to resume. See the [README](README.md#start-at-desktop-login) for disabling autostart and reading logs.
