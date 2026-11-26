# Jarvis

Jarvis accepts typed requests or spoken English, sends the text to Codex, and plays a brief English reply with Piper’s Jarvis voice. Jarvis addresses you as “sir” and skips Markdown formatting and URLs when speaking.

## Setup

Install from a normal desktop session as your own user. You need `uv`, `curl`, the Codex CLI signed in to ChatGPT, Voxtype, PipeWire, FFmpeg, Quickshell, and systemd user services.

```sh
sh install.sh
```

The installer creates the Python 3.12 environment, downloads missing voice and wake models and Whisper `base.en`, checks the installation, and enables Jarvis at graphical login. It also restarts Jarvis if a graphical session is active or the service is already running.

After pulling updates, rerun the same installer from the checkout.

```sh
git pull
sh install.sh
```

Existing models and conversation state are reused. Codex needs internet access and receives the request as text.

## Run

```sh
.wake-venv/bin/python jarvis.py 'Preséntate en una frase.'
```

For a hands-free turn, run this from your normal PipeWire desktop session, wait for the chime and say your request in English. Jarvis stops after about one second of silence, with a 20-second recording limit.

```sh
.wake-venv/bin/python jarvis.py --listen
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

The single-word model comes from [Tater Wake Words](https://github.com/TaterTotterson/Tater-Wake-Words/issues/403). The installer uses the ONNX version of OpenWakeWord.

## Start at desktop login

The installed `jarvis.service` starts with the systemd user graphical session and stops when that session ends. It opens no terminal. On Omarchy, Quickshell shows a small desktop overlay during recording, transcription, and replies.

- Press **Shift+F1** to start recording. Press it again or click **Send** to submit immediately. Wake-word recording also submits automatically after a pause.
- Press **Shift+F1** or click **Silence** during a reply to stop playback and mute the rest of that turn. Codex can finish the request in the background.
- The window displays the full reply even when audio is silenced. It stays visible through follow-up listening and after Jarvis goes idle. Click **Close** to dismiss it or start a new request.

To add the optional Shift+F1 shortcut on another Omarchy machine, bind it in Hyprland to `systemctl --user kill --kill-whom=main --signal=USR1 jarvis.service`.

```sh
systemctl --user status jarvis.service
systemctl --user stop jarvis.service    # Stop listening until started or next login
systemctl --user start jarvis.service   # Resume listening
journalctl --user -u jarvis.service -n 30 --no-pager
```

To turn off startup and stop the listener, run `systemctl --user disable --now jarvis.service`. To enable it again, run `systemctl --user enable --now "$PWD/jarvis.service"` from the Jarvis folder. The unit uses the checkout it is linked to. If you move the folder, disable the old service before moving it, then run the installer from the new checkout.
