import math
import os
import select
import struct
import subprocess
import time
import wave
from collections import deque
from contextlib import contextmanager
from itertools import islice, takewhile
from pathlib import Path
from tempfile import TemporaryDirectory

RATE = 16_000
FRAME_SAMPLES = 480
FRAME_BYTES = FRAME_SAMPLES * 2
MAX_FRAMES = 20 * RATE // FRAME_SAMPLES
FOLLOW_UP_FRAMES = round(2 * RATE / FRAME_SAMPLES)


def capture_utterance(frames, onset_frames=MAX_FRAMES):
    pre_roll = deque(maxlen=10)
    speech_run = silence = 0
    captured = bytearray()

    for index, frame in enumerate(islice(frames, MAX_FRAMES)):
        samples = struct.unpack("<480h", frame)
        # shortcut: fixed RMS threshold for quiet rooms, use speech VAD if background noise triggers it.
        loud = sum(sample * sample for sample in samples) >= len(samples) * 300**2
        if not captured:
            if index >= onset_frames and (not speech_run or not loud):
                break
            pre_roll.append(frame)
            speech_run = speech_run + 1 if loud else 0
            if speech_run == 5:
                captured.extend(b"".join(pre_roll))
            continue
        captured.extend(frame)
        silence = 0 if loud else silence + 1
        if silence == 34:
            break

    if silence:
        del captured[-silence * FRAME_BYTES:]
    return bytes(captured)


@contextmanager
def open_microphone():
    with subprocess.Popen(
        ["pw-record", "--raw", "--rate", str(RATE), "--channels", "1",
         "--format", "s16", "--latency", "30ms", "-"],
        stdout=subprocess.PIPE,
    ) as recorder:
        try:
            yield recorder.stdout
        finally:
            recorder.kill()


def microphone_frames(stream, frame_bytes=FRAME_BYTES, timeout=None):
    deadline = None if timeout is None else time.monotonic() + timeout
    pending = bytearray()
    while True:
        wait = 3
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            wait = min(remaining, wait)
        if not select.select([stream], [], [], wait)[0]:
            if deadline is not None and time.monotonic() >= deadline:
                return
            raise RuntimeError("PipeWire stopped delivering microphone audio.")
        chunk = os.read(stream.fileno(), frame_bytes - len(pending))
        if not chunk:
            raise RuntimeError("PipeWire microphone recording ended unexpectedly.")
        pending.extend(chunk)
        if len(pending) == frame_bytes:
            yield bytes(pending)
            pending.clear()


def write_wav(path, pcm):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        audio.writeframes(pcm)


def transcript_text(stdout):
    # Voxtype prints WAV metadata before a blank line and the transcript.
    if stdout.startswith("Loading audio file:"):
        stdout = stdout.partition("\n\n")[2]
    return stdout.strip()


def capture_recording(frames, manual):
    if manual:
        return b"".join(islice(frames, MAX_FRAMES))
    return capture_utterance(frames)


def record_transcript(state: Path, manual=False, stopped=None, on_transcribing=None, follow_up=False) -> str:
    with TemporaryDirectory(dir=state) as directory:
        folder = Path(directory)
        if not manual and not follow_up:
            chime = folder / "ready.wav"
            tone = (
                int(3500 * math.sin(2 * math.pi * 880 * sample / RATE)
                    * math.sin(math.pi * sample / 1600))
                for sample in range(1600)
            )
            write_wav(chime, struct.pack("<1600h", *tone))
            subprocess.run(["pw-play", str(chime)], check=True, timeout=5)
        print("Listening…", flush=True)
        with open_microphone() as stream:
            frames = microphone_frames(stream, timeout=21)
            if stopped is not None:
                frames = takewhile(lambda frame: not stopped(), frames)
            if follow_up:
                pcm = capture_utterance(frames, onset_frames=FOLLOW_UP_FRAMES)
            else:
                pcm = capture_recording(frames, manual)
        if not pcm or (follow_up and stopped is not None and stopped()):
            return ""
        if on_transcribing is not None:
            on_transcribing()
        request = folder / "request.wav"
        write_wav(request, pcm)
        result = subprocess.run(
            ["voxtype", "--model", "base.en", "--language", "en",
             "transcribe", "--engine", "whisper", str(request)],
            stdout=subprocess.PIPE, text=True, check=True, timeout=60,
        )
        return transcript_text(result.stdout)
