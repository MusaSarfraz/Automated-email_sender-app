"""EmailSender application entry point.

Responsibilities:
* Single-instance guard — a second launch focuses the existing window.
* Create the Qt application, main window, and tray icon.
* ``--minimized`` (used by the Windows Startup entry): start hidden in the tray.
* On startup, auto-resume a campaign that was running when the app last closed.
"""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from .core import config
from .ui.main_window import MainWindow

_SINGLETON_KEY = "EmailSender-singleton-v1"


def _already_running_and_focused() -> bool:
    """If another instance is up, tell it to show itself and return True."""
    socket = QLocalSocket()
    socket.connectToServer(_SINGLETON_KEY)
    if socket.waitForConnected(300):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return True
    return False


def _install_singleton_server(window: MainWindow) -> QLocalServer:
    """Create the local server that future launches connect to."""
    QLocalServer.removeServer(_SINGLETON_KEY)  # clear a stale socket from a crash
    server = QLocalServer()
    server.listen(_SINGLETON_KEY)

    def _on_new_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(lambda: None)
            window._show_from_tray()

    server.newConnection.connect(_on_new_connection)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="EmailSender", add_help=True)
    parser.add_argument("--minimized", action="store_true", help="Start hidden in the system tray")
    args, _ = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    app = QApplication(sys.argv)
    app.setApplicationName("EmailSender")
    app.setOrganizationName("EmailSender")
    # Closing the window hides to tray; the app keeps running until Quit.
    app.setQuitOnLastWindowClosed(False)

    if _already_running_and_focused():
        return 0

    settings = config.load_settings()
    window = MainWindow(settings)
    server = _install_singleton_server(window)
    app._singleton_server = server  # keep a reference alive

    if not args.minimized:
        window.show()

    # Resume a running campaign shortly after the event loop starts.
    QTimer.singleShot(0, window.resume_running_on_startup)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
