# voice_commands.py
"""
Offline fixed-phrase voice recognition for Aegis-Touch, using Vosk.

Push-to-talk design: continuous listening picks up ambient OR chatter as
false "commands" (see the '[unk]' noise hits from the first test run).
So instead, audio is only captured between an explicit START and STOP
trigger -- Vosk only ever processes what was said in that window.

Trigger source is swappable on purpose:
  - Today: Enter key (press to start speaking, press again to stop).
  - Later: USB foot pedal, once we know what keystroke/event it sends --
    swap `keyboard_trigger_*` for a `pedal_trigger_*` and nothing else changes.

Week 2 scope: recognize a small closed set of phrases reliably (the
preset camera views -- Anterior / Lateral / Reset). NOT free-form
dictation -- that's Week 3+.

Setup (one-time, on your own machine):
  1. pip install vosk sounddevice
  2. Download "vosk-model-small-en-us-0.15" from
     https://alphacephei.com/vosk/models
  3. Unzip it into this repo as ./vosk-model-small-en-us-0.15/
"""

import json
import os
import queue

import sounddevice as sd
from vosk import Model, KaldiRecognizer

MODEL_PATH = "vosk-model-small-en-us-0.15"
SAMPLE_RATE = 16000

# The fixed grammar. Vosk will only ever return one of these (or silence) --
# it will not hallucinate other words.
COMMAND_PHRASES = [
    "anterior",
    "posterior",
    "lateral",
    "reset",
    "reset view",
]


def keyboard_trigger_start():
    """Blocks until the user presses Enter to begin speaking."""
    input("\nPress ENTER, then say a command...")


def keyboard_trigger_stop():
    """Blocks until the user presses Enter again to end the recording window."""
    input("(listening -- press ENTER again to stop) ")


# --- Swap point for the foot pedal, once we know its trigger event ---
# def pedal_trigger_start():
#     wait_for_pedal_press()
# def pedal_trigger_stop():
#     wait_for_pedal_release()


class PushToTalkListener:
    """
    Records exactly one audio window per call to listen_once(), bounded by
    trigger_start()/trigger_stop(), and runs it through Vosk's fixed grammar.
    """

    def __init__(self, trigger_start=keyboard_trigger_start,
                 trigger_stop=keyboard_trigger_stop, model_path: str = MODEL_PATH):
        if not os.path.isdir(model_path):
            raise FileNotFoundError(
                f"Vosk model folder '{model_path}' not found.\n"
                f"Download it from https://alphacephei.com/vosk/models "
                f"(grab 'vosk-model-small-en-us-0.15') and unzip it into "
                f"this repo's root, next to this script, so the folder "
                f"'{model_path}' exists here."
            )
        self.trigger_start = trigger_start
        self.trigger_stop = trigger_stop
        self.model = Model(model_path)
        self._grammar = json.dumps(COMMAND_PHRASES + ["[unk]"])
        self._audio_q: queue.Queue = queue.Queue()

    def _audio_callback(self, indata, frames, time_info, status):
        self._audio_q.put(bytes(indata))

    def listen_once(self) -> str:
        """Runs one full start -> record -> stop -> recognize cycle. Returns the recognized phrase (or '')."""
        self.trigger_start()

        self._audio_q = queue.Queue()
        stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE, blocksize=2000, dtype="int16",
            channels=1, callback=self._audio_callback,
        )
        stream.start()

        self.trigger_stop()

        stream.stop()
        stream.close()

        # Fresh recognizer per utterance keeps this stateless between commands.
        recognizer = KaldiRecognizer(self.model, SAMPLE_RATE, self._grammar)
        while not self._audio_q.empty():
            recognizer.AcceptWaveform(self._audio_q.get())

        result = json.loads(recognizer.FinalResult())
        text = result.get("text", "").strip()
        return text


if __name__ == "__main__":
    # Manual test: press ENTER, say "anterior" / "lateral" / "posterior" /
    # "reset", press ENTER again, see it recognized. Ctrl+C to stop.
    listener = PushToTalkListener()
    print(f"Fixed phrases: {COMMAND_PHRASES}")
    try:
        while True:
            phrase = listener.listen_once()
            if phrase:
                print(f"  -> Recognized command: '{phrase}'")
            else:
                print("  -> (nothing recognized)")
    except KeyboardInterrupt:
        print("\nStopped.")