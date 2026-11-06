"""Frozen-app entry point for PyInstaller.

Running ``email_sender/app.py`` directly would break its package-relative
imports, so the launcher imports the package properly and calls ``main()``.
"""

import sys

from email_sender.app import main

if __name__ == "__main__":
    sys.exit(main())
