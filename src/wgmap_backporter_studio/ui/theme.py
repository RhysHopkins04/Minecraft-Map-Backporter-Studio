APP_STYLESHEET = r"""
QWidget {
    font-family: "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", sans-serif;
    font-size: 13px;
}
QMainWindow, QWidget#rootWindow { background: #11151b; color: #e7edf5; }
QLabel { color: #e7edf5; }
QLabel#muted { color: #9ca9b8; }
QLabel#pageTitle { font-size: 24px; font-weight: 700; }
QLabel#sectionTitle { font-size: 16px; font-weight: 650; }
QFrame#card, QGroupBox {
    background: #171d25;
    border: 1px solid #27313d;
    border-radius: 10px;
}
QGroupBox { margin-top: 11px; padding: 15px 12px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #dce6f2; }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTableWidget, QListWidget {
    background: #0f141a;
    color: #e7edf5;
    border: 1px solid #303b48;
    border-radius: 6px;
    padding: 7px;
    selection-background-color: #2b6fbb;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #4c92dc; }
QComboBox QAbstractItemView { background: #141a22; color: #e7edf5; selection-background-color: #2b6fbb; }
QPushButton {
    background: #283342;
    color: #edf4fc;
    border: 1px solid #38475a;
    border-radius: 7px;
    padding: 8px 13px;
    font-weight: 600;
}
QPushButton:hover { background: #334257; }
QPushButton:pressed { background: #222c39; }
QPushButton:disabled { color: #738091; background: #1b222c; border-color: #27313d; }
QPushButton#primary { background: #2677c9; border-color: #3386d7; }
QPushButton#primary:hover { background: #2d86dc; }
QPushButton#danger { background: #7b3030; }
QTabWidget::pane { border: 1px solid #27313d; border-radius: 8px; top: -1px; }
QTabBar::tab { background: #151b23; color: #aeb9c6; padding: 10px 16px; border: 1px solid #27313d; }
QTabBar::tab:selected { background: #1e2732; color: #ffffff; border-bottom-color: #1e2732; }
QHeaderView::section { background: #1d2530; color: #dfe7f1; padding: 7px; border: 0; border-right: 1px solid #303b48; }
QProgressBar { background: #0e1319; border: 1px solid #303b48; border-radius: 6px; text-align: center; color: #dfe7f1; }
QProgressBar::chunk { background: #2677c9; border-radius: 5px; }
QCheckBox { spacing: 8px; }
QStatusBar { background: #0d1117; color: #9ca9b8; border-top: 1px solid #27313d; }
QScrollBar:vertical { background: #11161d; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #384555; min-height: 30px; border-radius: 5px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
