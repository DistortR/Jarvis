import tempfile
from contextlib import contextmanager
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent import CodexAgent
from jarvis import answer


class JarvisTest(unittest.TestCase):
    def test_voice_pipeline_accepts_stub_agent(self):
        agent = Mock()
        agent.reply.return_value = "Hola, soy Jarvis."
        muted = lambda: False
        with patch("jarvis.speak") as voice:
            answer("Preséntate.", agent, Mock(), muted)
        self.assertEqual(agent.reply.call_args.args, ("Preséntate.",))
        voice.assert_called_once_with("Hola, soy Jarvis.", muted)

    def test_codex_resumes_only_jarvis_session(self):
        session = "d4d86357-0f91-4b26-8a1b-258f009c2788"
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "session"

            calls = []

            @contextmanager
            def connect():
                response = {
                    "thread": {"id": session},
                    "approvalPolicy": "on-request",
                    "approvalsReviewer": "user",
                    "sandbox": {"type": "readOnly", "networkAccess": False},
                }
                events = iter([
                    {"id": 1, "result": {}},
                    {"id": 2, "result": response},
                    {"id": 3, "result": {"turn": {"id": "turn"}}},
                    {"method": "item/started", "params": {"threadId": session, "turnId": "turn",
                        "item": {"type": "fileChange", "id": "patch", "changes": [
                            {"path": "hello.txt", "diff": "+Hello", "kind": {"type": "add"}}]}}},
                    {"id": "request", "method": "item/fileChange/requestApproval",
                     "params": {"threadId": session, "turnId": "turn", "itemId": "patch"}},
                    {"method": "item/completed", "params": {"threadId": session, "turnId": "turn",
                        "item": {"type": "agentMessage", "phase": "commentary", "text": "Checking, sir."}}},
                    {"method": "item/completed", "params": {"threadId": session, "turnId": "turn",
                        "item": {"type": "agentMessage", "phase": "final_answer", "text": "Hello, Ana."}}},
                    {"method": "turn/completed", "params": {"threadId": session,
                        "turn": {"id": "turn", "status": "completed"}}},
                ])
                yield calls.append, lambda: next(events)

            with patch("agent.connection", connect):
                state.touch()
                progress = Mock()
                approve = Mock(return_value=True)
                self.assertEqual(CodexAgent(state).reply("Me llamo Ana.", progress, approve), "Hello, Ana.")
                progress.assert_called_once_with("Checking, sir.")
                self.assertIn("+Hello", approve.call_args.args[1])
                self.assertIn({"id": "request", "result": {"decision": "accept"}}, calls)
                self.assertIn("thread/start", [call.get("method") for call in calls])
                calls.clear()
                CodexAgent(state).reply("¿Cómo me llamo?")
                resume = next(call for call in calls if call.get("method") == "thread/resume")
                self.assertEqual(resume["params"]["threadId"], session)
                self.assertEqual(resume["params"]["sandbox"], "read-only")
                self.assertIn({"id": "request", "result": {"decision": "decline"}}, calls)


class RecordingTest(unittest.TestCase):
    def test_cancelled_follow_up_discards_partial_speech(self):
        from contextlib import nullcontext
        from recording import record_transcript

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("recording.open_microphone", return_value=nullcontext()),
            patch("recording.microphone_frames", return_value=iter([b"\x00\x04" * 480] * 10)),
            patch("recording.subprocess.run") as transcribe,
        ):
            stopped = Mock(side_effect=[False] * 6 + [True, True])
            self.assertEqual(record_transcript(Path(directory), stopped=stopped, follow_up=True), "")
        transcribe.assert_not_called()

    def test_follow_up_accepts_speech_started_before_deadline(self):
        from recording import capture_utterance

        quiet = bytes(960)
        speech = b"\x00\x04" * 480
        self.assertIn(speech, capture_utterance(iter([quiet] * 65 + [speech] * 10), onset_frames=67))
        self.assertEqual(capture_utterance(iter([quiet] * 67 + [speech] * 10), onset_frames=67), b"")

    def test_short_pause_keeps_both_parts_and_trailing_silence_stops(self):
        from recording import capture_utterance

        quiet = bytes(960)
        speech = b"\x00\x04" * 480
        frames = [quiet] * 4 + [speech] * 12 + [quiet] * 15 + [speech] * 12
        stream = iter(frames + [quiet] * 34 + [speech])
        self.assertEqual(capture_utterance(stream), b"".join(frames))
        self.assertEqual(next(stream), speech)

    def test_silence_does_not_send_a_request(self):
        from jarvis import answer

        agent = Mock()
        voice = Mock()
        answer("", agent, voice, lambda: False)
        agent.reply.assert_not_called()
        voice.assert_not_called()

    def test_silence_and_short_noise_are_ignored(self):
        from recording import capture_utterance

        quiet = bytes(960)
        noise = b"\x00\x04" * 480
        self.assertEqual(capture_utterance(iter([quiet, noise] + [quiet] * 100)), b"")

    def test_continuous_sound_stops_at_recording_limit(self):
        from itertools import repeat

        from recording import capture_utterance

        speech = b"\x00\x04" * 480
        captured = capture_utterance(repeat(speech))
        self.assertLessEqual(len(captured) / (16_000 * 2), 20)
        self.assertGreater(len(captured) / (16_000 * 2), 19)

    def test_voxtype_status_lines_are_not_in_the_transcript(self):
        from recording import transcript_text

        status = 'Loading audio file: "request.wav"\nAudio format: 16000 Hz\nProcessing 480 samples...\n'
        self.assertEqual(transcript_text(status + "\nHello there.\n"), "Hello there.")
        self.assertEqual(transcript_text(status), "")

    def test_transcription_failure_does_not_reach_codex(self):
        from contextlib import redirect_stderr
        from io import StringIO
        from subprocess import CalledProcessError

        from jarvis import main

        with (
            patch("sys.argv", ["jarvis.py", "--listen"]),
            patch("jarvis.VOICE") as model,
            patch("jarvis.record_transcript", side_effect=CalledProcessError(1, "voxtype")),
            patch("jarvis.CodexAgent"),
            patch("jarvis.answer") as answer,
            redirect_stderr(StringIO()),
            self.assertRaises(SystemExit) as error,
        ):
            model.is_file.return_value = True
            main()
        self.assertEqual(error.exception.code, 1)
        answer.assert_not_called()


class WakeTest(unittest.TestCase):
    def test_returns_to_idle_only_after_follow_ups_finish(self):
        from contextlib import nullcontext
        from wake import wake_loop

        detector = Mock()
        detector.detect.return_value = True
        states = []
        turn = Mock(side_effect=lambda: states.append("reply") or True)
        follow_up = Mock(side_effect=[True, False, False])
        with (
            patch("wake.open_microphone", side_effect=lambda: nullcontext()),
            patch("wake.microphone_frames", side_effect=[iter([b"wake"]), iter([b"next wake"]), iter([])]),
        ):
            wake_loop(detector, turn, follow_up=lambda: states.append("follow-up") or follow_up(),
                      on_idle=lambda: states.append("idle"))
        self.assertEqual(turn.call_count, 2)
        self.assertEqual(follow_up.call_count, 3)
        self.assertEqual(states, ["idle", "reply", "follow-up", "follow-up",
                                  "idle", "reply", "follow-up", "idle"])

    def test_wake_scores_need_recent_audible_input_and_three_hits(self):
        import numpy
        from collections import deque
        from wake import WakeDetector, WAKE_MODEL

        detector = WakeDetector.__new__(WakeDetector)
        detector.numpy = numpy
        detector.model = Mock()
        detector.model.predict.return_value = {WAKE_MODEL: 0.99}
        detector.recent_audio = deque(maxlen=10)
        detector.reset()
        quiet = bytes(2560)
        loud = b"\x00\x04" * 1280
        self.assertFalse(any(detector.detect(quiet) for _ in range(10)))
        self.assertFalse(detector.detect(loud))
        self.assertFalse(detector.detect(loud))
        self.assertFalse(detector.detect(quiet))
        self.assertTrue(detector.detect(quiet))

    def test_listener_reopens_only_after_each_turn(self):
        from contextlib import contextmanager
        from wake import wake_loop

        events = []
        detector = Mock()
        detector.detect.side_effect = [False, True, True, KeyboardInterrupt]

        @contextmanager
        def microphone():
            events.append("open")
            try:
                yield object()
            finally:
                events.append("close")

        def turn():
            events.append("turn")

        with (
            patch("wake.open_microphone", microphone),
            patch("wake.microphone_frames", return_value=iter([b"ambient", b"wake", b"wake", b"stop"])),
            self.assertRaises(KeyboardInterrupt),
        ):
            wake_loop(detector, turn)
        self.assertEqual(events, ["open", "close", "turn", "open", "close", "turn", "open", "close"])
        self.assertEqual(detector.reset.call_count, 3)


class VoiceFeaturesTest(unittest.TestCase):
    def test_muted_answer_still_displays_the_complete_text(self):
        agent = Mock()
        agent.reply.return_value = "Ready, sir. [Details](https://example.com)."
        display = Mock()
        with patch("jarvis.speak") as voice:
            answer("What happened?", agent, Mock(), lambda: True, show_reply=display)
        display.assert_called_once_with(agent.reply.return_value)
        voice.assert_not_called()

    def test_muting_progress_suppresses_the_final_audio(self):
        from threading import Event

        from jarvis import answer

        muted = Event()

        def reply(transcript, on_progress, on_approval=None):
            on_progress("Checking, sir.")
            self.assertTrue(muted.wait(2))
            return "Done, sir."

        agent = Mock()
        agent.reply.side_effect = reply
        with patch("jarvis.speak", side_effect=lambda text, stopped: muted.set()) as voice:
            answer("Check the result.", agent, Mock(), muted.is_set)
        self.assertEqual([call.args[0] for call in voice.call_args_list], ["Checking, sir."])

    def test_speech_removes_formatting_and_link_targets(self):
        from jarvis import speech_text

        self.assertEqual(
            speech_text("## **Ready**, sir. [The guide](https://example.com/a_(b)) "
                        "(https://example.com) and `Piper`."),
            "Ready, sir. The guide and Piper.",
        )

    def test_manual_capture_sends_without_trailing_silence(self):
        from recording import capture_recording

        frame = b"\xff\x03" * 480
        self.assertEqual(capture_recording(iter([frame]), manual=True), frame)

    def test_progress_requires_commentary_or_explicit_marker(self):
        from agent import progress_text

        self.assertEqual(progress_text({"type": "agent_message", "text": "Final answer"}), "")
        self.assertEqual(progress_text({"type": "agent_message", "text": "JARVIS_PROGRESS: Checking, sir."}),
                         "Checking, sir.")
        self.assertEqual(progress_text({"type": "agent_message", "phase": "commentary", "text": "Checking."}),
                         "Checking.")
