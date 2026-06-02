"""Shared dark theme — colors and palette/stylesheet setup for PyQt6."""
from __future__ import annotations

from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import QApplication


class _C:
    bg          = "#1e1e1e"   # window / scrollbar / dialog background
    card        = "#252525"   # credential card background (slightly above bg)
    surface     = "#2d2d2d"   # input, tooltip background
    surface_alt = "#363636"   # alternate row background
    btn         = "#3a3a3a"   # button background
    hover       = "#484848"   # button hover, selected card
    pressed     = "#555555"   # button pressed, progress fill
    border      = "#4a4a4a"   # borders, separators, scrollbar handles
    selection   = "#505050"   # text selection highlight
    mid         = "#666666"   # disabled text, scrollbar hover
    text        = "#f0f0f0"   # primary text
    text_dim    = "#888888"   # placeholder text, slider handle
    accent      = "#1a4a4a"   # group header (dark cyan)
    white       = "#ffffff"   # text on accent backgrounds
    link        = "#aaaaff"   # hyperlinks


def apply_dark_theme(app: QApplication) -> None:
    app.setStyle("Fusion")

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window,          QColor(_C.bg))
    pal.setColor(QPalette.ColorRole.WindowText,      QColor(_C.text))
    pal.setColor(QPalette.ColorRole.Base,            QColor(_C.surface))
    pal.setColor(QPalette.ColorRole.AlternateBase,   QColor(_C.surface_alt))
    pal.setColor(QPalette.ColorRole.Text,            QColor(_C.text))
    pal.setColor(QPalette.ColorRole.BrightText,      QColor(_C.white))
    pal.setColor(QPalette.ColorRole.Button,          QColor(_C.btn))
    pal.setColor(QPalette.ColorRole.ButtonText,      QColor(_C.text))
    pal.setColor(QPalette.ColorRole.Highlight,       QColor(_C.selection))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(_C.white))
    pal.setColor(QPalette.ColorRole.Link,            QColor(_C.link))
    pal.setColor(QPalette.ColorRole.ToolTipBase,     QColor(_C.surface))
    pal.setColor(QPalette.ColorRole.ToolTipText,     QColor(_C.text))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(_C.text_dim))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(_C.mid))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(_C.mid))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(_C.mid))
    app.setPalette(pal)

    c = _C
    app.setStyleSheet(f"""
        QWidget {{
            background-color: {c.bg};
            color: {c.text};
        }}
        QPushButton {{
            padding: 4px 10px;
            background-color: {c.btn};
            color: {c.text};
            border: 1px solid {c.border};
            border-radius: 4px;
        }}
        QPushButton:hover   {{ background-color: {c.hover}; }}
        QPushButton:pressed {{ background-color: {c.pressed}; }}
        QPushButton:disabled {{ color: {c.mid}; border-color: {c.bg}; }}
        QLineEdit, QComboBox, QTextEdit {{
            background-color: {c.surface};
            color: {c.text};
            border: 1px solid {c.border};
            border-radius: 4px;
            padding: 3px 6px;
            selection-background-color: {c.selection};
        }}
        QComboBox::drop-down {{ border: none; }}
        QComboBox QAbstractItemView {{
            background-color: {c.surface};
            color: {c.text};
            selection-background-color: {c.selection};
        }}
        QScrollBar:vertical {{
            background: {c.bg};
            width: 10px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {c.border};
            border-radius: 5px;
            min-height: 20px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {c.mid}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        QScrollBar:horizontal {{
            background: {c.bg};
            height: 10px;
            margin: 0;
        }}
        QScrollBar::handle:horizontal {{
            background: {c.border};
            border-radius: 5px;
            min-width: 20px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {c.mid}; }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
        QLabel {{ background: transparent; }}
        QSlider::groove:horizontal {{
            height: 4px;
            background: {c.border};
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            width: 14px; height: 14px;
            margin: -5px 0;
            background: {c.text_dim};
            border-radius: 7px;
        }}
        QProgressBar {{
            background: {c.surface};
            border: 1px solid {c.border};
            border-radius: 4px;
            text-align: center;
            color: {c.text};
        }}
        QProgressBar::chunk {{ background: {c.pressed}; border-radius: 3px; }}
        QFrame[frameShape="4"], QFrame[frameShape="5"] {{
            color: {c.border};
        }}
        QMessageBox {{ background-color: {c.bg}; }}
        QToolTip {{
            background-color: {c.surface};
            color: {c.text};
            border: 1px solid {c.border};
        }}
        QFrame#credential_card {{ background-color: {c.card}; }}
        QWidget#group_header {{ background-color: {c.accent}; }}
        QWidget#group_header QLabel {{ color: {c.white}; background: transparent; }}
        QWidget#group_header QPushButton {{ color: {c.white}; }}
        QFrame#credential_group {{ border: none; }}
    """)
