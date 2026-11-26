import os
import subprocess
import sys
from collections import deque

from recording import microphone_frames, open_microphone

WAKE_FRAME_BYTES = 1280 * 2
WAKE_MODEL = "jarvis.oww"


class WakeDetector:
    def __init__(self, models):
        import numpy
        from openwakeword.model import Model

        self.numpy = numpy
        self.hit_count = 0
        self.recent_audio = deque(maxlen=10)
        self.model = Model(
            wakeword_models=[str(models / f"{WAKE_MODEL}.onnx")],
            inference_framework="onnx",
            melspec_model_path=str(models / "melspectrogram.onnx"),
            embedding_model_path=str(models / "embedding_model.onnx"),
        )

    def reset(self):
        self.hit_count = 0
        self.recent_audio.clear()
        self.model.reset()

    def detect(self, frame):
        samples = self.numpy.frombuffer(frame, dtype="<i2")
        score = self.model.predict(samples)[WAKE_MODEL]
        amplitude = samples.astype(self.numpy.float32)
        self.recent_audio.append(self.numpy.mean(amplitude ** 2) >= 300**2)
        confident = score >= 0.96 and sum(self.recent_audio) >= 2
        self.hit_count = self.hit_count + 1 if confident else 0
        return self.hit_count >= 3


def desktop_status(message):
    if os.environ.get("JARVIS_DESKTOP_STATUS") != "1":
        return
    try:
        subprocess.run(
            ["notify-send", "--app-name=Jarvis", "--expire-time=2000", "Jarvis", message],
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def wake_loop(detector, turn, manual_requested=lambda: False, follow_up=None, on_idle=lambda: None):
    while True:
        on_idle()
        detector.reset()
        print('Waiting for “Jarvis”…', flush=True)
        desktop_status("Listening for Jarvis")
        with open_microphone() as stream:
            for frame in microphone_frames(stream, WAKE_FRAME_BYTES):
                if manual_requested():
                    print("Jarvis activated by shortcut.", flush=True)
                    break
                if detector.detect(frame):
                    print("Jarvis activated by wake word.", flush=True)
                    break
            else:
                return
        # Closing the stream drops queued audio before any chime or reply plays.
        try:
            answered = turn()
            while answered and follow_up is not None:
                answered = follow_up()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            print(f"Jarvis: {error}", file=sys.stderr, flush=True)
