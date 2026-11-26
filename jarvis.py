import argparse
import json
import os
import re
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from uuid import uuid4

from agent import CodexAgent
from recording import record_transcript
from wake import WakeDetector, wake_loop

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".jarvis"
VOICE = STATE / "voices" / "en_GB-jarvis-high.onnx"


def speech_text(text):
    text = re.sub(r"```[^\n]*\n.*?```", " ", text, flags=re.S)
    text = re.sub(r"!?\[([^\]\n]+)\]\((?:[^()\n]|\([^()\n]*\))*\)", r"\1", text)
    text = re.sub(r"\(\s*<?(?:https?://|www\.)[^\s<>]+?>?\s*\)", "", text, flags=re.I)
    text = re.sub(r"<?(?:[a-z][a-z0-9+.-]*://|www\.)[^\s<>]+>?",
                  lambda match: match[0][-1] if match[0][-1] in ".,!?;" else "", text, flags=re.I)
    text = re.sub(r"^\s*(?:#{1,6}\s+|>\s*|[-+*]\s+)", "", text, flags=re.M)
    text = re.sub(r"[_*`~]", "", text)
    return re.sub(r"\s+([.,!?;])", r"\1", " ".join(text.split()))


def speak(text, muted=lambda: False):
    text = speech_text(text)
    if not text or muted():
        return
    with TemporaryDirectory(dir=STATE) as directory:
        audio = Path(directory) / "reply.wav"
        subprocess.run(
            [sys.executable, "-m", "piper", "-m", str(VOICE), "-f", str(audio)],
            input=text + "\n", text=True, check=True, timeout=60,
        )
        boosted = Path(directory) / "reply-louder.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(audio),
             "-af", "volume=3dB,alimiter=limit=0.97:level=0:latency=1", str(boosted)],
            check=True, timeout=30,
        )
        if muted():
            return
        with subprocess.Popen(["pw-play", str(boosted)]) as player:
            try:
                for _ in range(1200):
                    if muted():
                        return
                    try:
                        status = player.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        continue
                    if status:
                        raise subprocess.CalledProcessError(status, player.args)
                    return
                raise subprocess.TimeoutExpired(player.args, 120)
            finally:
                if player.poll() is None:
                    player.kill()


@contextmanager
def overlay(initial="Idle", service_controls=False, single_turn=False):
    if not os.environ.get("WAYLAND_DISPLAY"):
        yield (lambda phase: None), None, (lambda reply: None)
        return
    with TemporaryDirectory(dir=STATE) as directory:
        status = Path(directory) / "phase"
        approval = Path(directory) / "approval"
        decision = Path(directory) / "decision"
        reply = Path(directory) / "reply"
        approval.write_text("{}")
        decision.touch()
        reply.touch()

        def update(phase):
            status.write_text(phase)

        update(initial)
        try:
            panel = subprocess.Popen(
                ["qs", "-p", str(ROOT / "overlay.qml")],
                env={**os.environ, "JARVIS_PHASE_FILE": str(status),
                     "JARVIS_SERVICE_CONTROLS": "1" if service_controls else "0",
                     "JARVIS_PID": str(os.getpid()),
                     "JARVIS_SINGLE_TURN": "1" if single_turn else "0"},
            )
        except OSError as error:
            print(f"Jarvis overlay: {error}", file=sys.stderr, flush=True)
            yield update, None, (lambda reply: None)
        else:
            def confirm(title, details, accept_label):
                token = uuid4().hex
                approval.write_text(json.dumps(
                    {"token": token, "title": title, "details": details, "acceptLabel": accept_label}
                ))
                try:
                    deadline = monotonic() + 60
                    while monotonic() < deadline and panel.poll() is None:
                        response = decision.read_text().strip()
                        if response == f"{token} accept":
                            return True
                        if response == f"{token} decline":
                            return False
                        sleep(0.05)
                    return False
                finally:
                    approval.write_text("{}")

            with panel:
                try:
                    yield update, confirm, reply.write_text
                    if single_turn and reply.stat().st_size:
                        update("Idle")
                        panel.wait()
                finally:
                    if panel.poll() is None:
                        panel.kill()


def answer(transcript, agent, update, muted, confirm=None, show_reply=lambda reply: None):
    if not transcript.strip():
        return

    def say(text):
        if not muted() and speech_text(text):
            update("Speaking")
            speak(text, muted)

    update("Thinking")
    with ThreadPoolExecutor(max_workers=1) as voice:
        progress = None

        def announce(text):
            nonlocal progress

            def speak_progress():
                try:
                    say(text)
                finally:
                    update("Silenced" if muted() else "Thinking")

            progress = voice.submit(speak_progress)

        reply = agent.reply(transcript, on_progress=announce, on_approval=confirm)
        show_reply(reply)
        if progress is not None:
            try:
                progress.result()
            except (OSError, subprocess.SubprocessError) as error:
                print(f"Jarvis progress audio: {error}", file=sys.stderr, flush=True)
        say(reply)


def main():
    parser = argparse.ArgumentParser(description="Ask Jarvis by text or microphone and hear the reply.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("prompt", nargs="?", help="Text to send to Codex")
    mode.add_argument("--listen", action="store_true", help="Record one hands-free request")
    mode.add_argument("--wake", action="store_true", help="Wait for Jarvis and handle spoken requests")
    args = parser.parse_args()
    if args.prompt is not None and not args.prompt.strip():
        parser.error("the prompt must contain text")
    if not VOICE.is_file():
        parser.error("the Piper voice is missing. See README.md for setup.")
    STATE.mkdir(mode=0o700, exist_ok=True)
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    agent = CodexAgent(STATE / f"codex-session-{boot}")

    phase = "idle"
    manual_requested = stopped = muted = False

    def toggle_recording(signum, frame):
        nonlocal manual_requested, stopped, muted
        if phase in ("idle", "follow-up"):
            manual_requested = True
            stopped = phase == "follow-up"
        elif phase == "recording":
            stopped = True
        else:
            muted = True

    def record_turn(follow_up=False):
        nonlocal phase, manual_requested, stopped, muted
        if follow_up and manual_requested:
            return False
        manual = manual_requested
        manual_requested = stopped = muted = False
        phase = "follow-up" if follow_up else "recording"

        def transcribing():
            nonlocal phase
            phase = "reply"
            update("Transcribing")

        try:
            if not follow_up:
                show_reply("")
            update("Listening for follow-up" if follow_up else "Listening")
            transcript = record_transcript(
                STATE, manual, lambda: stopped, transcribing, follow_up=follow_up
            )
            if transcript:
                answer(transcript, agent, update, lambda: muted, confirm, show_reply)
                return True
            if not follow_up:
                print("No speech detected.", flush=True)
        finally:
            phase = "idle"

    def cancel_follow_up(signum, frame):
        nonlocal stopped
        if phase == "follow-up":
            stopped = True

    try:
        service_controls = args.wake and os.environ.get("JARVIS_DESKTOP_STATUS") == "1"
        if args.wake:
            signal.signal(signal.SIGUSR1, toggle_recording)
            signal.signal(signal.SIGUSR2, cancel_follow_up)
        with overlay(service_controls=service_controls, single_turn=not args.wake) as (update, confirm, show_reply):
            if args.wake:
                wake_loop(WakeDetector(STATE / "wake-models"), record_turn,
                          lambda: manual_requested, lambda: record_turn(follow_up=True),
                          on_idle=lambda: update("Idle"))
            elif args.listen:
                record_turn()
            else:
                answer(args.prompt, agent, update, lambda: False, confirm, show_reply)
    except KeyboardInterrupt:
        return
    except (ImportError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Jarvis: {error}\n")


if __name__ == "__main__":
    main()
