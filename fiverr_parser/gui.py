"""Native desktop GUI for Fiverr Radar (PySide6 / Qt).

A polished dark-themed desktop application that drives the same backend as
the web dashboard (SQLite storage, scraper, scheduler). Run it with::

    python run_gui.py

The window shows live stat cards, a sortable listings table, filter
controls (search, query, no-reviews, New Seller), a live log console and a
settings dialog. Scraping happens on the background scheduler thread; the
UI polls the DB on a timer, so it stays responsive.
"""

from __future__ import annotations

import sys
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .config import CONFIG, Settings
from .db import Database
from .fetcher import _HAS_CFFI
from .scheduler import SchedulerController

GREEN = "#1dbf73"

QSS = f"""
QWidget {{
    background: #0f141b;
    color: #e6edf3;
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
}}
QFrame#Card {{
    background: #161d28;
    border: 1px solid #233044;
    border-radius: 14px;
}}
QFrame#TopBar {{
    background: #0b0f14;
    border-bottom: 1px solid #233044;
}}
QLabel#Brand {{ font-size: 18px; font-weight: 800; }}
QLabel#StatValue {{ font-size: 24px; font-weight: 800; }}
QLabel#StatLabel {{ color: #8b97a7; font-size: 11px; }}
QLabel#Muted {{ color: #8b97a7; }}
QPushButton {{
    background: #1b2433; border: 1px solid #2c3c55; border-radius: 9px;
    padding: 8px 14px; font-weight: 600;
}}
QPushButton:hover {{ border-color: #3a4f6e; }}
QPushButton#Primary {{ background: {GREEN}; color: #04130b; border: none; }}
QPushButton#Primary:hover {{ background: #19a463; }}
QPushButton#Danger {{ background: #2a1620; border: 1px solid #6e2c3a; color: #ff8a96; }}
QLineEdit, QComboBox, QSpinBox {{
    background: #0f141b; border: 1px solid #233044; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {GREEN};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {GREEN}; }}
QCheckBox {{ color: #c3ccd6; spacing: 6px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid #3a4f6e; background: #0f141b; }}
QCheckBox::indicator:checked {{ background: {GREEN}; border-color: {GREEN}; }}
QTableWidget {{
    background: #121821; border: 1px solid #233044; border-radius: 12px;
    gridline-color: #1c2533; selection-background-color: #1e2a3a;
}}
QHeaderView::section {{
    background: #0f141b; color: #8b97a7; border: none;
    border-bottom: 1px solid #233044; padding: 8px; font-weight: 700;
}}
QPlainTextEdit {{
    background: #0a0e13; border: 1px solid #233044; border-radius: 12px;
    font-family: 'Cascadia Mono','Consolas',monospace; font-size: 12px; color: #c3ccd6;
}}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #233044; border-radius: 5px; }}
"""


class StatCard(QFrame):
    def __init__(self, icon: str, label: str):
        super().__init__()
        self.setObjectName("Card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        ico = QLabel(icon)
        ico.setFont(QFont("Segoe UI Emoji", 20))
        col = QVBoxLayout()
        col.setSpacing(0)
        self.value = QLabel("0")
        self.value.setObjectName("StatValue")
        lbl = QLabel(label)
        lbl.setObjectName("StatLabel")
        col.addWidget(self.value)
        col.addWidget(lbl)
        lay.addWidget(ico)
        lay.addSpacing(6)
        lay.addLayout(col)
        lay.addStretch(1)

    def set(self, n: int) -> None:
        self.value.setText(f"{n:,}")


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(440)
        self.settings = settings
        form = QFormLayout(self)

        self.queries = QLineEdit(", ".join(settings.queries))
        self.interval = QSpinBox()
        self.interval.setRange(30, 86_400)
        self.interval.setValue(settings.interval_seconds)
        self.maxq = QSpinBox()
        self.maxq.setRange(1, 400)
        self.maxq.setValue(settings.max_per_query)
        self.concurrency = QSpinBox()
        self.concurrency.setRange(1, 32)
        self.concurrency.setValue(settings.concurrency)
        self.pages = QSpinBox()
        self.pages.setRange(1, 10)
        self.pages.setValue(settings.pages_per_query)
        self.proxy = QLineEdit(settings.proxy)
        self.proxy.setPlaceholderText("http://user:pass@host:port  (rotating proxy)")
        self.demo = QCheckBox("Demo mode (offline sample data)")
        self.demo.setChecked(settings.demo_mode)
        self.autostart = QCheckBox("Autostart scraping on launch")
        self.autostart.setChecked(settings.autostart)

        form.addRow("Queries (comma-separated)", self.queries)
        form.addRow("Interval (seconds)", self.interval)
        form.addRow("Max per query", self.maxq)
        form.addRow("Parallel workers (speed)", self.concurrency)
        form.addRow("Pages per query", self.pages)
        form.addRow("Proxy", self.proxy)
        form.addRow("", self.demo)
        form.addRow("", self.autostart)

        engine = "curl_cffi (Chrome TLS)" if _HAS_CFFI else "httpx (install curl_cffi for fewer 403s)"
        note = QLabel(f"HTTP engine: {engine}")
        note.setObjectName("Muted")
        form.addRow("", note)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def apply_to(self) -> Settings:
        data = self.settings.to_dict()
        data["queries"] = [q.strip() for q in self.queries.text().split(",") if q.strip()]
        data["interval_seconds"] = self.interval.value()
        data["max_per_query"] = self.maxq.value()
        data["concurrency"] = self.concurrency.value()
        data["pages_per_query"] = self.pages.value()
        data["proxy"] = self.proxy.text().strip()
        data["demo_mode"] = self.demo.isChecked()
        data["autostart"] = self.autostart.isChecked()
        return Settings.from_dict(data)


COLUMNS = ["", "Title", "Seller", "Level", "Country", "Price", "Rating", "Reviews", "Query"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fiverr Radar — new-listings parser")
        self.resize(1180, 760)

        self.db = Database(CONFIG.db_path)
        persisted = self.db.load_setting("settings")
        self.settings = Settings.from_dict(persisted) if persisted else Settings().sanitized()
        self.scheduler = SchedulerController(self.db, self.settings)

        self._build_ui()
        if self.settings.autostart:
            self.scheduler.start()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1500)
        self.refresh()

    # -- UI construction ---------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Top bar
        top = QFrame()
        top.setObjectName("TopBar")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(20, 12, 20, 12)
        brand = QLabel("🛰️  Fiverr Radar")
        brand.setObjectName("Brand")
        self.status_lbl = QLabel("● idle")
        self.status_lbl.setObjectName("Muted")
        tl.addWidget(brand)
        tl.addSpacing(14)
        tl.addWidget(self.status_lbl)
        tl.addStretch(1)
        self.btn_run = QPushButton("⚡ Run now")
        self.btn_toggle = QPushButton("▶ Start")
        self.btn_toggle.setObjectName("Primary")
        self.btn_settings = QPushButton("⚙ Settings")
        self.btn_run.clicked.connect(self.run_now)
        self.btn_toggle.clicked.connect(self.toggle_scheduler)
        self.btn_settings.clicked.connect(self.open_settings)
        tl.addWidget(self.btn_run)
        tl.addWidget(self.btn_toggle)
        tl.addWidget(self.btn_settings)
        root.addWidget(top)

        body = QVBoxLayout()
        body.setContentsMargins(20, 18, 20, 18)
        body.setSpacing(16)
        root.addLayout(body)

        # Stat cards
        stats = QHBoxLayout()
        stats.setSpacing(14)
        self.card_gigs = StatCard("📦", "Listings")
        self.card_sellers = StatCard("👤", "Unique sellers")
        self.card_pro = StatCard("⭐", "Pro sellers")
        self.card_new = StatCard("✨", "New · 24h")
        for c in (self.card_gigs, self.card_sellers, self.card_pro, self.card_new):
            stats.addWidget(c)
        body.addLayout(stats)

        # Filter bar
        filt = QHBoxLayout()
        filt.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔎  Search title or seller…")
        self.search.textChanged.connect(self.refresh)
        self.query_combo = QComboBox()
        self.query_combo.addItem("All queries", "")
        self.query_combo.currentIndexChanged.connect(self.refresh)
        self.cb_new = QCheckBox("New only")
        self.cb_noreviews = QCheckBox("Без отзывов")
        self.cb_newseller = QCheckBox("New Seller")
        for cb in (self.cb_new, self.cb_noreviews, self.cb_newseller):
            cb.stateChanged.connect(self.refresh)
        self.btn_export = QPushButton("⬇ Export .txt")
        self.btn_export.clicked.connect(self.export_txt)
        filt.addWidget(self.search, 2)
        filt.addWidget(self.query_combo, 1)
        filt.addWidget(self.cb_new)
        filt.addWidget(self.cb_noreviews)
        filt.addWidget(self.cb_newseller)
        filt.addStretch(1)
        filt.addWidget(self.btn_export)
        body.addLayout(filt)

        # Table
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.doubleClicked.connect(self._open_row)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in (0, 3, 4, 5, 6, 7, 8):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        body.addWidget(self.table, 3)

        # Log console
        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(150)
        body.addWidget(self.console, 1)

        self._rows: list[dict] = []

    # -- actions -----------------------------------------------------------

    def toggle_scheduler(self) -> None:
        if self.scheduler.running:
            self.scheduler.stop()
        else:
            self.scheduler.start()
        self._update_status()

    def run_now(self) -> None:
        self.scheduler.run_now()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self)
        if dlg.exec() == QDialog.Accepted:
            new = dlg.apply_to()
            for k, v in new.to_dict().items():
                setattr(self.settings, k, v)
            self.db.save_setting("settings", self.settings.to_dict())
            self.scheduler._wake.set()
            self._update_status()

    def export_txt(self) -> None:
        urls = [r["url"] for r in self._rows if r.get("url")]
        if not urls:
            QMessageBox.information(self, "Export", "Нет URL для экспорта при текущих фильтрах.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export URLs", "fiverr_urls.txt", "Text (*.txt)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(urls) + "\n")
        QMessageBox.information(self, "Export", f"Сохранено {len(urls)} URL →\n{path}")

    def _open_row(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            url = self._rows[row].get("url")
            if url:
                webbrowser.open(url)

    # -- refresh -----------------------------------------------------------

    def _update_status(self) -> None:
        st = self.scheduler.status()
        if st["busy"]:
            text, color = "● scraping…", "#f5b94a"
        elif st["running"]:
            text, color = "● running", GREEN
        else:
            text, color = "● idle", "#8b97a7"
        mode = "demo" if st["demo_mode"] else "live"
        self.status_lbl.setText(f"{text}   ·   {mode}")
        self.status_lbl.setStyleSheet(f"color: {color};")
        self.btn_toggle.setText("⏸ Stop" if st["running"] else "▶ Start")

    def _sync_query_combo(self, by_query: list[dict]) -> None:
        current = self.query_combo.currentData()
        want = [""] + [q["query"] for q in by_query if q["query"]]
        have = [self.query_combo.itemData(i) for i in range(self.query_combo.count())]
        if want != have:
            self.query_combo.blockSignals(True)
            self.query_combo.clear()
            self.query_combo.addItem("All queries", "")
            for q in by_query:
                if q["query"]:
                    self.query_combo.addItem(f"{q['query']} ({q['c']})", q["query"])
            idx = max(0, self.query_combo.findData(current))
            self.query_combo.setCurrentIndex(idx)
            self.query_combo.blockSignals(False)

    def refresh(self) -> None:
        stats = self.db.stats()
        self.card_gigs.set(stats["gigs"])
        self.card_sellers.set(stats["sellers"])
        self.card_pro.set(stats["pro_sellers"])
        self.card_new.set(stats["new_24h"])
        self._sync_query_combo(stats["by_query"])
        self._update_status()

        rows = self.db.recent_gigs(
            limit=300,
            query=self.query_combo.currentData() or "",
            search=self.search.text().strip(),
            only_new=self.cb_new.isChecked(),
            no_reviews=self.cb_noreviews.isChecked(),
            new_seller=self.cb_newseller.isChecked(),
        )
        self._rows = rows
        self._fill_table(rows)

        # Log console (only update when changed to keep scrollback usable)
        lines = self.scheduler.logs(120)
        text = "\n".join(f"{l['ts'][11:19]}  {l['level'].upper():5}  {l['message']}" for l in lines)
        if text != self.console.toPlainText():
            at_bottom = self.console.verticalScrollBar().value() >= self.console.verticalScrollBar().maximum() - 4
            self.console.setPlainText(text)
            if at_bottom:
                self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

    def _fill_table(self, rows: list[dict]) -> None:
        self.table.setRowCount(len(rows))
        for r, g in enumerate(rows):
            price = g["price_cents"] / 100.0
            rating = f"{g['rating']:.1f}" if g["reviews_count"] else "—"
            cells = [
                "🟢" if g["is_new"] else "",
                g["title"],
                f"@{g.get('seller_username') or '—'}" + ("  PRO" if g.get("seller_pro") else ""),
                g.get("seller_level") or "—",
                g.get("seller_country") or "—",
                f"${price:,.2f}",
                rating,
                str(g["reviews_count"]),
                g.get("query") or "",
            ]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(val)
                if c in (5, 6, 7):
                    item.setTextAlignment(Qt.AlignCenter)
                if g["is_new"] and c == 1:
                    item.setForeground(QColor(GREEN))
                self.table.setItem(r, c, item)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt signature)
        self.scheduler.stop()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)
    # Dark base palette so native widgets match the QSS.
    pal = app.palette()
    pal.setColor(QPalette.Window, QColor("#0f141b"))
    pal.setColor(QPalette.Base, QColor("#121821"))
    pal.setColor(QPalette.Text, QColor("#e6edf3"))
    pal.setColor(QPalette.WindowText, QColor("#e6edf3"))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
