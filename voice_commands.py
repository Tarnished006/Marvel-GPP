"""
Aegis-Touch voice controller.

Vosk runs in a separate Python process so that a native Vosk crash
cannot crash the main PyQt/MediaPipe application.
"""

import os
import sys
import subprocess

from PyQt6.QtCore import QThread, pyqtSignal


MODEL_PATH = "vosk-model-small-en-us-0.15"


class VoiceCommandWorker(QThread):
    voice_result = pyqtSignal(str)
    """
    Runs the Vosk engine in a completely separate Python process.

    The main Aegis application only receives recognized text.
    """

    def __init__(self, model_path: str = MODEL_PATH):
        super().__init__()

        self.model_path = model_path
        self._armed = False
        self._running = True
        self.process = None

    def run(self):
        project_root = os.path.dirname(os.path.abspath(__file__))
        process_script = os.path.join(project_root, "voice_process.py")

        python_executable = sys.executable

        env = os.environ.copy()

        try:
            self.process = subprocess.Popen(
                [
                    python_executable,
                    process_script,
                ],
                cwd=project_root,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
                bufsize=1,
            )

            print("[voice_commands] Voice process started.")

            while self._running:

                line = self.process.stdout.readline()

                if not line:
                    break

                line = line.strip()

                if line == "LOADING":
                    print("[voice_commands] Loading Vosk model...")

                elif line == "READY":
                    print("[voice_commands] Vosk model ready.")

                elif line.startswith("RESULT:"):
                    phrase = line[len("RESULT:"):].strip()

                    if phrase and self._armed:
                        print(
                            f"[voice_commands] Recognized: '{phrase}'"
                        )

                        self.voice_result.emit(phrase)

            print("[voice_commands] Voice process stopped.")

        except Exception as e:
            print(f"[voice_commands] Error: {e}")

        finally:
            self.process = None

    def set_armed(self, armed: bool):
        self._armed = armed

        if self.process is None:
            return

        try:
            if armed:
                self.process.stdin.write("ARM\n")
            else:
                self.process.stdin.write("DISARM\n")

            self.process.stdin.flush()

        except (BrokenPipeError, OSError):
            pass

    def stop(self):
        self._running = False
        self._armed = False

        if self.process is not None:
            try:
                self.process.stdin.write("EXIT\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
