"""QThread worker that runs a :class:`~email_sender.core.campaign.Campaign`.

The UI never sends email itself. It builds a campaign, hands it to this worker,
and reacts to Qt signals. Pause/Stop are forwarded to the campaign's thread-safe
controls; the campaign's own ``on_event`` callbacks are re-emitted as the
:attr:`CampaignWorker.event` signal (queued across the thread boundary).
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from .core.campaign import Campaign


class CampaignWorker(QThread):
    """Runs ``campaign.run()`` off the GUI thread."""

    #: (event_kind, data) for every campaign event.
    event = Signal(str, dict)
    #: The reason run() ended: "paused" | "stopped" | "completed" | "auth_paused".
    finished_reason = Signal(str)
    #: Emitted if the campaign raised unexpectedly (message for the user).
    crashed = Signal(str)

    def __init__(self, campaign: Campaign, parent=None) -> None:
        super().__init__(parent)
        self._campaign = campaign
        campaign._on_event = self._forward_event

    @property
    def campaign(self) -> Campaign:
        return self._campaign

    def _forward_event(self, kind: str, data: dict) -> None:
        # Called from this worker thread; Qt queues it to the GUI thread.
        self.event.emit(kind, dict(data))

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            reason = self._campaign.run()
        except Exception as exc:  # noqa: BLE001 - surface to the UI, don't crash the app
            self.crashed.emit(str(exc))
            return
        self.finished_reason.emit(reason)

    # --- controls (safe to call from the GUI thread) -------------------------
    def request_pause(self) -> None:
        self._campaign.request_pause()

    def request_stop(self) -> None:
        self._campaign.request_stop()
