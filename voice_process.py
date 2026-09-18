import json
import os
import sys
import time
import queue
import threading

import sounddevice as sd
from vosk import Model, KaldiRecognizer


MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "vosk-model-small-en-us-0.15",
)

SAMPLE_RATE = 16000
RECORD_SECONDS = 2.5

# Keep this list synchronized with the commands supported by the viewer.
COMMAND_PHRASES = [
    "anterior",
    "posterior",
    "left lateral",
    "right lateral",
    "lateral",
    "top",
    "superior",
    "bottom",
    "inferior",
    "reset",
    "reset view",
    "start spin",
    "stop spin",
    "zoom in",
    "zoom out",
    "turn left",
    "turn right",
]

audio_q = queue.Queue()


def audio_callback(indata, frames, time_info, status):
    """Receive microphone audio chunks."""
    if status:
        print(f"[voice_process] Audio status: {status}", flush=True)

    audio_q.put(bytes(indata))


def record_and_recognize(model):
    """
    Record a short audio segment and recognize one command.
    """

    global audio_q
    audio_q = queue.Queue()

    recognizer = KaldiRecognizer(
        model,
        SAMPLE_RATE,
        json.dumps(COMMAND_PHRASES + ["[unk]"]),
    )

    try:
        with sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=2000,
            dtype="int16",
            channels=1,
            callback=audio_callback,
        ):
            start_time = time.time()

            while time.time() - start_time < RECORD_SECONDS:
                try:
                    data = audio_q.get(timeout=0.1)
                    recognizer.AcceptWaveform(data)
                except queue.Empty:
                    continue

    except Exception as exc:
        print(f"[voice_process] Microphone error: {exc}", flush=True)
        return ""

    try:
        result = json.loads(recognizer.FinalResult())
        return result.get("text", "").strip().lower()
    except Exception as exc:
        print(f"[voice_process] Recognition error: {exc}", flush=True)
        return ""


def stdin_reader(command_queue):
    """
    Read ARM, DISARM, and EXIT commands from the parent process.
    """
    while True:
        try:
            line = sys.stdin.readline()

            if not line:
                command_queue.put("EXIT")
                return

            command_queue.put(line.strip().upper())

        except Exception:
            command_queue.put("EXIT")
            return


def main():
    if not os.path.isdir(MODEL_PATH):
        print(
            f"ERROR: Vosk model folder not found: {MODEL_PATH}",
            flush=True,
        )
        return

    print("LOADING", flush=True)

    try:
        model = Model(MODEL_PATH)
    except Exception as exc:
        print(f"ERROR: Could not load Vosk model: {exc}", flush=True)
        return

    print("READY", flush=True)

    command_queue = queue.Queue()

    reader_thread = threading.Thread(
        target=stdin_reader,
        args=(command_queue,),
        daemon=True,
    )
    reader_thread.start()

    armed = False
    should_exit = False

    while not should_exit:

        # Read commands without blocking when armed.
        try:
            incoming = command_queue.get(
                timeout=0.1 if armed else 0.5
            )

            if incoming == "ARM":
                armed = True
                print("ARMED", flush=True)

            elif incoming == "DISARM":
                armed = False
                print("DISARMED", flush=True)

            elif incoming == "EXIT":
                should_exit = True
                break

        except queue.Empty:
            pass

        if not armed:
            continue

        phrase = record_and_recognize(model)

        if phrase:
            print(f"RESULT:{phrase}", flush=True)

        # Process any commands received while recording.
        while not command_queue.empty():
            try:
                incoming = command_queue.get_nowait()

                if incoming == "DISARM":
                    armed = False

                elif incoming == "EXIT":
                    should_exit = True
                    break

            except queue.Empty:
                break

    try:
        print("STOPPED", flush=True)
    except (OSError, BrokenPipeError):
        pass
    except Exception:
        pass

    try:
        sys.stdout.flush()
        # Redirect stdout to devnull to avoid OSError during interpreter finalization
        import os
        sys.stdout = open(os.devnull, "w")
    except Exception:
        pass


if __name__ == "__main__":
    main()