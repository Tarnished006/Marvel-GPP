import json
import os
import sys
import time
import queue

import sounddevice as sd
from vosk import Model, KaldiRecognizer


MODEL_PATH = "vosk-model-small-en-us-0.15"
SAMPLE_RATE = 16000
RECORD_SECONDS = 2.5

COMMAND_PHRASES = [
    "anterior",
    "posterior",
    "lateral",
    "top",
    "bottom",
    "reset",
    "reset view",

    "start spin",
    "stop spin",
]


audio_q = queue.Queue()


def audio_callback(indata, frames, time_info, status):
    audio_q.put(bytes(indata))


def record_and_recognize(model):
    global audio_q
    audio_q = queue.Queue()

    stream = sd.RawInputStream(
        samplerate=SAMPLE_RATE,
        blocksize=2000,
        dtype="int16",
        channels=1,
        callback=audio_callback,
    )

    stream.start()

    time.sleep(RECORD_SECONDS)

    stream.stop()
    stream.close()

    recognizer = KaldiRecognizer(
        model,
        SAMPLE_RATE,
        json.dumps(COMMAND_PHRASES + ["[unk]"])
    )

    while not audio_q.empty():
        recognizer.AcceptWaveform(audio_q.get())

    result = json.loads(recognizer.FinalResult())
    return result.get("text", "").strip()


def main():
    if not os.path.isdir(MODEL_PATH):
        print("ERROR: Vosk model folder not found", flush=True)
        return

    print("LOADING", flush=True)

    model = Model(MODEL_PATH)

    print("READY", flush=True)

    armed = False

    while True:
        command = sys.stdin.readline()

        if not command:
            break

        command = command.strip().upper()

        if command == "ARM":
            armed = True

            while armed:
                phrase = record_and_recognize(model)

                if phrase:
                    try:
                        print(f"RESULT:{phrase}", flush=True)
                    except BrokenPipeError:
                        return

                # Check whether the parent sent DISARM/EXIT.
                import select

                if select.select([sys.stdin], [], [], 0)[0]:
                    incoming = sys.stdin.readline().strip().upper()

                    if incoming == "DISARM":
                        armed = False
                    elif incoming == "EXIT":
                        return

        elif command == "DISARM":
            armed = False

        elif command == "EXIT":
            break


if __name__ == "__main__":
    main()