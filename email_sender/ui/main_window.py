"""The main window: four tabs, the tray icon, and campaign orchestration.

Only one campaign runs at a time. The window never sends email itself — it
builds a :class:`~email_sender.core.campaign.Campaign`, hands it to a
:class:`~email_sender.worker.CampaignWorker`, and reacts to the worker's signals.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QTabWidget,
)

from .. import __version__
from ..core import campaigns, config, progress as P
from ..core.senders.factory import NotConnectedError
from ..worker import CampaignWorker
from .campaign_page import CampaignPage
from .history_page import HistoryPage
from .icon import app_icon
from .progress_page import (
    UI_CAP,
    UI_COMPLETED,
    UI_PAUSED,
    UI_READY,
    UI_RUNNING,
    UI_STOPPED,
    ProgressPage,
)
from .settings_page import SettingsPage


class MainWindow(QMainWindow):
    def __init__(self, settings: config.Settings) -> None:
        super().__init__()
        self.settings = settings
        self.campaign = None
        self.worker: CampaignWorker | None = None
        self._really_quitting = False
        self._tray_hint_shown = False

        self.setWindowTitle("EmailSender")
        self.setWindowIcon(app_icon())
        self.resize(760, 640)

        self.tabs = QTabWidget()
        self.settings_page = SettingsPage(settings)
        self.campaign_page = CampaignPage(settings)
        self.progress_page = ProgressPage()
        self.history_page = HistoryPage()
        self.tabs.addTab(self.settings_page, "Settings")
        self.tabs.addTab(self.campaign_page, "New campaign")
        self.tabs.addTab(self.progress_page, "Progress")
        self.tabs.addTab(self.history_page, "History")
        self.setCentralWidget(self.tabs)

        # Wire page signals.
        self.campaign_page.startRequested.connect(self.start_campaign)
        self.progress_page.pauseClicked.connect(self.pause)
        self.progress_page.resumeClicked.connect(self.resume)
        self.progress_page.stopClicked.connect(self.stop)
        self.progress_page.reloadClicked.connect(self.reload_sheet)
        self.history_page.continueRequested.connect(self.continue_campaign)
        self.history_page.exportRequested.connect(self.export_results)

        self.history_page.refresh()
        self._build_tray()

        # Credit line, shown on every tab.
        self.statusBar().showMessage(f"EmailSender v{__version__}  ·  Created by Musa Sarfraz")

    # --- tray ----------------------------------------------------------------
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("EmailSender — Created by Musa Sarfraz")
        menu = QMenu()
        self.act_open = QAction("Open", self)
        self.act_open.triggered.connect(self._show_from_tray)
        self.act_pause = QAction("Pause", self)
        self.act_pause.triggered.connect(self._tray_pause_resume)
        self.act_pause.setEnabled(False)
        self.act_quit = QAction("Quit", self)
        self.act_quit.triggered.connect(self.quit_app)
        menu.addAction(self.act_open)
        menu.addAction(self.act_pause)
        menu.addSeparator()
        menu.addAction(self.act_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger or reason == QSystemTrayIcon.DoubleClick:
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_pause_resume(self) -> None:
        if self._ui_state == UI_PAUSED:
            self.resume()
        else:
            self.pause()

    # --- campaign lifecycle --------------------------------------------------
    def start_campaign(self, prog: P.Progress) -> None:
        if self._worker_busy():
            QMessageBox.information(self, "Already running", "A campaign is already running. Stop it first.")
            return
        if not self._build_campaign(prog):
            return
        self._begin_progress_view()
        self.tabs.setCurrentWidget(self.progress_page)
        self._launch_worker()

    def continue_campaign(self, prog: P.Progress) -> None:
        if self._worker_busy():
            QMessageBox.information(self, "Already running", "Stop the running campaign first.")
            return
        if not self._build_campaign(prog):
            return
        self._begin_progress_view()
        # Continue lands in Paused; the user presses Resume to send (spec §7).
        self._set_ui_state(UI_PAUSED)
        self.tabs.setCurrentWidget(self.progress_page)

    def _build_campaign(self, prog: P.Progress) -> bool:
        try:
            self.campaign = campaigns.make_campaign(prog, settings=self.settings)
        except NotConnectedError as exc:
            QMessageBox.warning(self, "Connect a mailbox", str(exc))
            self.tabs.setCurrentWidget(self.settings_page)
            return False
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Could not open campaign", str(exc))
            return False
        self.prog = prog
        return True

    def _begin_progress_view(self) -> None:
        addr = self.settings.gmail_address if self.settings.sender_type == "gmail" else self.settings.outlook_address
        self.progress_page.begin(self.prog.name, addr or "(mailbox)", self.prog.count_by_status(), self.settings.daily_cap)

    def _launch_worker(self) -> None:
        self.worker = CampaignWorker(self.campaign)
        self.worker.event.connect(self.progress_page.handle_event)
        self.worker.finished_reason.connect(self._on_finished)
        self.worker.crashed.connect(self._on_crashed)
        self._set_ui_state(UI_RUNNING)
        self.worker.start()

    def pause(self) -> None:
        if self._worker_busy():
            self.progress_page.status_label.setText("PAUSING — finishing the current email…")
            self.progress_page.pause_btn.setEnabled(False)
            self.progress_page.stop_btn.setEnabled(False)
            self.worker.request_pause()

    def stop(self) -> None:
        if self._worker_busy():
            self.progress_page.status_label.setText("STOPPING — finishing the current email…")
            self.progress_page.pause_btn.setEnabled(False)
            self.progress_page.stop_btn.setEnabled(False)
            self.worker.request_stop()

    def resume(self) -> None:
        if self.campaign is None or self._worker_busy():
            return
        self._silent_reload()  # spec §7: Resume re-reads the sheet
        self._begin_counts_refresh()
        self.tabs.setCurrentWidget(self.progress_page)
        self._launch_worker()

    def reload_sheet(self) -> None:
        if self.campaign is None or self._worker_busy():
            return
        try:
            result = campaigns.reload_source(self.prog)
        except FileNotFoundError as exc:
            QMessageBox.warning(self, "File not found", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Could not reload", str(exc))
            return
        if not result.ok:
            QMessageBox.warning(
                self, "Sheet problem", f"Missing columns: {', '.join(result.missing_columns)}"
            )
            return
        diff = self.campaign.diff(result)
        if diff.is_empty:
            QMessageBox.information(self, "No changes", "The sheet has no changes to apply.")
            return
        answer = QMessageBox.question(
            self, "Apply changes?", f"{diff.summary()}\n\nApply these changes?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.campaign.apply_reload(result)
            self.progress_page.set_counts(self.campaign.counts())

    def _silent_reload(self) -> None:
        try:
            result = campaigns.reload_source(self.prog)
            if result.ok:
                self.campaign.apply_reload(result)
        except Exception:  # noqa: BLE001 - resume with the existing copy if reload fails
            pass

    def _begin_counts_refresh(self) -> None:
        self.progress_page.set_counts(self.campaign.counts(), self.settings.daily_cap)

    def _on_finished(self, reason: str) -> None:
        mapping = {
            "paused": UI_PAUSED,
            "auth_paused": UI_PAUSED,
            "stopped": UI_STOPPED,
            "completed": UI_COMPLETED,
        }
        self._set_ui_state(mapping.get(reason, UI_PAUSED))
        self.history_page.refresh()
        if reason == "completed":
            QMessageBox.information(self, "Campaign complete", "All rows have been processed.")
        elif reason == "auth_paused":
            QMessageBox.warning(
                self,
                "Mailbox needs reconnecting",
                "Sending was paused after repeated sign-in failures. Open Settings, reconnect "
                "your mailbox, then press Resume.",
            )
        self.worker = None

    def _on_crashed(self, message: str) -> None:
        self._set_ui_state(UI_PAUSED)
        self.worker = None
        QMessageBox.critical(self, "Unexpected error", f"The campaign stopped unexpectedly:\n{message}")

    def export_results(self, prog: P.Progress) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export results", f"{prog.name}_results.xlsx", "Excel files (*.xlsx)"
        )
        if not path:
            return
        try:
            campaigns.export_results(prog, path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Export failed", str(exc))
            return
        QMessageBox.information(self, "Exported", f"Results saved to:\n{path}")

    # --- startup auto-resume -------------------------------------------------
    def resume_running_on_startup(self) -> None:
        """Auto-resume a campaign that was running when the app last closed."""
        for prog in campaigns.list_campaigns():
            if campaigns.should_auto_resume(prog):
                if self._build_campaign(prog):
                    self._begin_progress_view()
                    self.tabs.setCurrentWidget(self.progress_page)
                    self._launch_worker()
                return

    # --- state helpers -------------------------------------------------------
    _ui_state = UI_READY

    def _set_ui_state(self, state: str) -> None:
        self._ui_state = state
        self.progress_page.set_ui_state(state)
        running_or_paused = state in (UI_RUNNING, UI_PAUSED, UI_CAP)
        self.act_pause.setEnabled(running_or_paused)
        self.act_pause.setText("Resume" if state == UI_PAUSED else "Pause")

    def _worker_busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    # --- window close / quit -------------------------------------------------
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._really_quitting:
            event.accept()
            return
        # ✕ hides to tray and keeps sending.
        event.ignore()
        self.hide()
        if not self._tray_hint_shown and self.tray.supportsMessages():
            self.tray.showMessage(
                "EmailSender is still running",
                "Sending continues in the background. Use the tray icon to open or quit.",
            )
            self._tray_hint_shown = True

    def quit_app(self) -> None:
        if self._worker_busy():
            answer = QMessageBox.question(
                self,
                "Quit EmailSender?",
                "A campaign is running. Quitting will pause it until you open the app again.\n\nQuit now?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
            # Ask the worker to stop at the next safe point and wait briefly.
            self.worker.request_pause()
            self.worker.wait(5000)
        self._really_quitting = True
        from PySide6.QtWidgets import QApplication

        self.tray.hide()
        QApplication.instance().quit()
