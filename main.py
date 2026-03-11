"""Application entry point."""

from __future__ import annotations

import os
import queue
import signal
import sys

from PySide6 import QtWidgets

from cli import build_arg_parser, start_runtime_thread
from gui import MainWindow
from models import ChartState


def main() -> None:
    """Program entry point."""

    parser = build_arg_parser()
    args = parser.parse_args()

    args.api_key = os.getenv("APCA_API_KEY_ID", "")
    args.api_secret = os.getenv("APCA_API_SECRET_KEY", "")

    if not args.api_key or not args.api_secret:
        raise SystemExit(
            "Set APCA_API_KEY_ID and APCA_API_SECRET_KEY before running the app."
        )

    gui_queue: queue.Queue[ChartState] = queue.Queue(maxsize=8)
    runtime_thread, engine, loop = start_runtime_thread(args=args, gui_queue=gui_queue)

    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow(gui_queue=gui_queue, args=args)
    window.show()

    def shutdown() -> None:
        loop.call_soon_threadsafe(engine.stop)

    def handle_signal(*_) -> None:
        shutdown()
        app.quit()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    app.aboutToQuit.connect(shutdown)

    exit_code = app.exec()

    shutdown()
    runtime_thread.join(timeout=5)
    raise SystemExit(exit_code)