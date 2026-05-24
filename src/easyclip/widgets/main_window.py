"""MainWindow thin shell: manages tabs, menus, shortcuts, and global settings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QTabWidget,
    QWidget,
)
from PySide6.QtMultimedia import QMediaPlayer

from easyclip.core.settings import AppSettings, StartupBehavior
from easyclip.core.theme import on_theme_changed, WidgetColors
from easyclip.i18n.strings import tr
from easyclip.widgets.slicing_page import SlicingPage


# ── MainWindow shell ─────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """Thin shell that hosts a QTabWidget with SlicingPage and AnnotationPage."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(tr("app.title"))
        self.resize(1440, 880)

        # Global settings (shared by all pages)
        self._settings = AppSettings()

        # Build the tab widget
        self._tab_widget = QTabWidget(self)
        self._tab_widget.setDocumentMode(True)

        # Slicing page (the existing video slicing functionality)
        self._slicing_page = SlicingPage(self._settings, self)
        self._tab_widget.addTab(self._slicing_page, tr("tab.slicing"))

        # Annotation page (lazy import to avoid circular dependency)
        from easyclip.annotation.annotation_page import AnnotationPage as AP
        self._annotation_page = AP(self)
        self._tab_widget.addTab(self._annotation_page, tr("tab.annotation"))

        self.setCentralWidget(self._tab_widget)

        # Status bar
        self._status_label = QLabel("")
        self.statusBar().addPermanentWidget(self._status_label)

        # Track whether startup is complete.  During __init__ the tab restore
        # may fire _on_tab_changed, but we don't want to trigger project
        # restore there — showEvent handles the initial restore instead.
        # After startup, user-initiated tab switches DO trigger restore.
        self._startup_complete = False

        # Track whether each page has had its startup project restore attempted.
        # On the first switch to a page, trigger restore if not yet attempted.
        self._slicing_restore_attempted = False
        self._annotation_restore_attempted = False

        # Wire page signals
        self._slicing_page.status_message.connect(self._status_label.setText)
        self._slicing_page.window_title_changed.connect(self.setWindowTitle)
        self._annotation_page.status_message.connect(self._status_label.setText)
        self._annotation_page.window_title_changed.connect(self.setWindowTitle)

        # Initial menu and shortcut setup for the default active tab (slicing).
        # Must always run: slicing page methods like _load_video_path rely on
        # menu actions created by setup_menus(), even when the annotation tab
        # is restored as active below.
        self._active_page = self._slicing_page
        self._rebuild_all_menus()
        self._slicing_page.install_shortcuts()

        # Connect tab switch handler BEFORE restoring last tab so
        # _on_tab_changed fires and correctly updates _active_page,
        # menus, and shortcuts when switching to a non-zero tab.
        self._tab_widget.currentChanged.connect(self._on_tab_changed)

        # Restore last active tab
        last_tab = max(0, min(self._tab_widget.count() - 1, self._settings.last_active_tab_index()))
        if last_tab != 0:
            self._tab_widget.setCurrentIndex(last_tab)

        # Global theme callback
        on_theme_changed(self._on_theme_changed)

        # Initial setup
        QTimer.singleShot(0, self._refresh_min_window_size)

    # ── Tab switching ────────────────────────────────────────────────

    def _on_tab_changed(self, index: int) -> None:
        """Handle tab switch: deactivate old page, activate new page."""
        new_page = self._tab_widget.widget(index)
        if new_page is self._active_page:
            return

        # Deactivate old
        self._active_page.uninstall_shortcuts()
        self._active_page.on_tab_deactivated()

        # Activate new
        self._active_page = new_page
        new_page.on_tab_activated()
        self._rebuild_all_menus()
        new_page.install_shortcuts()

        # Remember for next launch
        self._settings.set_last_active_tab_index(index)

        # Trigger startup-style project restore on first user-initiated
        # switch to this page.  Skip during __init__ — showEvent handles
        # the initial restore.
        if self._startup_complete:
            if new_page is self._slicing_page and not self._slicing_restore_attempted:
                self._slicing_restore_attempted = True
                QTimer.singleShot(0, self._maybe_restore_last_project_on_startup)
            elif new_page is self._annotation_page and not self._annotation_restore_attempted:
                self._annotation_restore_attempted = True
                QTimer.singleShot(50, self._annotation_page.maybe_restore_last_session)

    # ── Menu management ──────────────────────────────────────────────

    def _rebuild_all_menus(self) -> None:
        """Clear menu bar and rebuild: page menus first, then shell menus."""
        mb = self.menuBar()
        mb.clear()

        # Active page provides File and Edit menus
        self._active_page.setup_menus(mb)

        # Shell adds Settings and Help menus
        self._add_settings_menu(mb)
        self._add_help_menu(mb)

    def _add_settings_menu(self, mb: QMenuBar) -> None:
        m_settings = mb.addMenu(tr("menu.settings"))
        act_set = QAction(tr("menu.preferences"), self)
        act_set.setMenuRole(QAction.MenuRole.NoRole)
        act_set.triggered.connect(self._open_settings)
        m_settings.addAction(act_set)

    def _add_help_menu(self, mb: QMenuBar) -> None:
        m_help = mb.addMenu(tr("menu.help"))
        act_about = QAction(tr("menu.about"), self)
        act_about.triggered.connect(self._about)
        m_help.addAction(act_about)

    # ── Settings dialog ──────────────────────────────────────────────

    def _open_settings(self) -> None:
        """Open the active page's settings dialog, then refresh shell UI."""
        if hasattr(self._active_page, 'show_settings_dialog'):
            self._active_page.show_settings_dialog()
            self.refresh_ui_language()
            self._refresh_min_window_size()

    # ── About ────────────────────────────────────────────────────────

    def _about(self) -> None:
        from easyclip import __version__

        QMessageBox.about(
            self,
            tr("menu.about"),
            tr("dialog.about_text", version=__version__),
        )

    # ── Language refresh ─────────────────────────────────────────────

    def refresh_ui_language(self) -> None:
        """Rebuild menus and update all visible UI strings after language change."""
        self.setWindowTitle(tr("app.title"))
        self._tab_widget.setTabText(0, tr("tab.slicing"))
        self._tab_widget.setTabText(1, tr("tab.annotation"))
        self._rebuild_all_menus()
        self._active_page.refresh_language()

    # ── Theme ────────────────────────────────────────────────────────

    def _on_theme_changed(self, wc: WidgetColors) -> None:
        """Propagate theme change to active page."""
        self._active_page.apply_theme(wc)

    # ── Startup restore ──────────────────────────────────────────────

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._startup_complete = True

        # Trigger startup-style project restore for the initially active tab
        if self._active_page is self._slicing_page:
            self._slicing_restore_attempted = True
            QTimer.singleShot(0, self._maybe_restore_last_project_on_startup)
        else:
            self._annotation_restore_attempted = True
            QTimer.singleShot(50, self._annotation_page.maybe_restore_last_session)

    def _maybe_restore_last_project_on_startup(self) -> None:
        if self._active_page is not self._slicing_page:
            return  # annotation tab is active; slicing restore is skipped
        source_path = self._settings.last_open_source_path().strip()
        if not source_path:
            return
        source = Path(source_path).resolve()
        if not source.is_file():
            self._settings.set_last_open_source_path("")
            return
        behavior = self._settings.startup_behavior()
        if behavior == StartupBehavior.DO_NOTHING:
            return
        should_load = behavior == StartupBehavior.AUTO_LOAD_LAST_PROJECT
        if behavior == StartupBehavior.ASK:
            should_load, remember_choice = self._ask_load_last_project_at_startup(source)
            if remember_choice:
                next_behavior = (
                    StartupBehavior.AUTO_LOAD_LAST_PROJECT
                    if should_load
                    else StartupBehavior.DO_NOTHING
                )
                self._settings.set_startup_behavior(next_behavior)
        if not should_load:
            return
        try:
            self._slicing_page._load_video_path(source)
        except Exception as e:
            QMessageBox.warning(
                self,
                tr("startup.restore.title"),
                tr("startup.restore.failed", detail=str(e)),
            )

    def _ask_load_last_project_at_startup(self, source: Path) -> tuple[bool, bool]:
        msg = QMessageBox(self)
        msg.setWindowTitle(tr("startup.restore.title"))
        msg.setText(tr("startup.restore.body", filename=source.name))
        cb_remember = QCheckBox(tr("startup.restore.remember"), msg)
        msg.setCheckBox(cb_remember)
        btn_load = msg.addButton(tr("startup.restore.load"), QMessageBox.ButtonRole.AcceptRole)
        msg.addButton(tr("startup.restore.skip"), QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_load)
        msg.exec()
        should_load = msg.clickedButton() == btn_load
        return should_load, cb_remember.isChecked()

    # ── Window management ────────────────────────────────────────────

    def _refresh_min_window_size(self) -> None:
        mb = self.menuBar()
        mb_h = mb.sizeHint().height() if mb.isVisible() else 0
        self.setMinimumSize(750, 400 + mb_h)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        # SlicingPage handles its own splitter init on resize

    # ── Close ────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._slicing_page.cleanup_on_close()
        self._annotation_page.cleanup_on_close()
        event.accept()
