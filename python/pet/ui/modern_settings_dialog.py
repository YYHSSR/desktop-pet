# -*- coding: utf-8 -*-
"""Modern-inspired sidebar settings panel used by the modern context menu."""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

import shiboken6

from PySide6.QtCore import QEvent, QFileInfo, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QDialog,
    QColorDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFileIconProvider,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from pet.infrastructure import autostart as autostart_mod
from pet.infrastructure import catalog
from pet.services.agent_link import AgentLinkManager
from pet.infrastructure.config import (
    DEFAULT_CONTEXT_MENU_APPEARANCE,
    DEFAULT_MENU_EASTER_EGG,
    DEFAULT_QUICK_LAUNCH_APPS,
    DEFAULT_QUICK_WEBSITES,
    DEFAULT_SELF_TALK_BUBBLE_STYLE,
    DEFAULT_SELF_TALK_DURATION_SECONDS,
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
    DEFAULT_SELF_TALK_TEXTS,
    _float_or_default,
)
from pet.ui.context_menus.icons import vector_widget_icon
from pet.ui.context_menus.quick_launch import fitted_application_icon
from pet.ui.fun_image_popup import oijingjing_image_path, resolve_fun_asset, store_fun_asset
from pet.ui.speech_bubble import BUBBLE_STYLE_PRESETS


def _system_font_families() -> tuple[str, ...]:
    """缓存系统字体族列表。

    macOS 上 QFontDatabase.families() 走 CoreText 枚举，首次调用可达数百 ms，
    设置窗口每次打开都重建实例，同步枚举会明显拖慢打开速度。
    """
    if _system_font_families._cache is None:
        _system_font_families._cache = tuple(QFontDatabase.families())
    return _system_font_families._cache


_system_font_families._cache = None


BROWSER_CONTROL_SPEC = {
    "field_height": 32,
    "border": "#cfd4da",
    "border_hover": "#aeb6c0",
    "focus": "#0a84ff",
    "radius": 7,
    "scrollbar_width": 8,
}

BROWSER_CONTROL_STYLESHEET = """
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: #ffffff;
    color: #202124;
    border: 1px solid #cfd4da;
    border-radius: 7px;
    padding: 4px 8px;
    selection-background-color: #0a84ff;
    selection-color: #ffffff;
}
QLineEdit, QSpinBox, QDoubleSpinBox { min-height: 20px; }
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QPlainTextEdit:hover {
    border-color: #aeb6c0;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {
    border: 2px solid #0a84ff;
    padding: 3px 7px;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    width: 18px;
    border: none;
    border-left: 1px solid #e3e5e8;
    border-bottom: 1px solid #eceef0;
    border-top-right-radius: 6px;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    width: 18px;
    border: none;
    border-left: 1px solid #e3e5e8;
    border-bottom-right-radius: 6px;
}
QScrollBar:vertical {
    width: 8px;
    margin: 0;
    background: transparent;
}
QScrollBar::handle:vertical {
    min-height: 24px;
    margin: 1px;
    background: #c4c8cc;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover { background: #9fa5ab; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal {
    height: 8px;
    margin: 0;
    background: transparent;
}
QScrollBar::handle:horizontal {
    min-width: 24px;
    margin: 1px;
    background: #c4c8cc;
    border-radius: 4px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""


class ToggleSwitch(QAbstractButton):
    """Small native-looking toggle used by settings cards."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 22)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = "#0a84ff" if self.isChecked() else ("#3a3a42" if _system_dark() else "#dedede")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(track))
        painter.drawRoundedRect(QRectF(0, 1, 38, 20), 10, 10)
        knob_x = 19.0 if self.isChecked() else 2.0
        painter.setBrush(QColor("#ffffff"))
        painter.setPen(QPen(QColor("#c9c9c9"), 0.5))
        painter.drawEllipse(QRectF(knob_x, 2, 18, 18))


IMAGE_NAME_FILTER = "图片文件 (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.tif *.tiff)"


class ResourcePathPicker(QWidget):
    """Absolute-path field with a native file or directory chooser."""

    def __init__(self, value: str, *, directory: bool = False, name_filter: str = IMAGE_NAME_FILTER, parent=None):
        super().__init__(parent)
        self.directory = bool(directory)
        self.name_filter = name_filter
        self.edit = QLineEdit(self)
        self.edit.setMinimumWidth(250)
        self.edit.setText(str(value))
        self.button = QPushButton("选择…", self)
        self.button.setFixedWidth(66)
        self.button.clicked.connect(self.choose)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.edit, 1)
        layout.addWidget(self.button)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:  # noqa: N802
        self.edit.setText(str(value))

    def choose(self) -> None:
        current = self.text()
        start = current if current else str(Path.home())
        if self.directory:
            selected = QFileDialog.getExistingDirectory(self, "选择图片目录", start)
        else:
            selected, _ = QFileDialog.getOpenFileName(self, "选择图片", start, self.name_filter)
        if selected:
            self.setText(str(Path(selected).expanduser().resolve()))


class ColorSwatchButton(QAbstractButton):
    """Compact painted color well that does not depend on native button CSS."""

    def __init__(self, value: str, parent=None):
        super().__init__(parent)
        self._color = QColor(value)
        self.setFixedSize(36, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("选择颜色")

    def color(self) -> QColor:
        return QColor(self._color)

    def setColor(self, value) -> None:  # noqa: N802
        color = QColor(value)
        self._color = color if color.isValid() else QColor("#ffffff")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#aeb3b8"), 1.0))
        painter.setBrush(self._color)
        painter.drawRoundedRect(QRectF(3.5, 3.5, self.width() - 7.0, self.height() - 7.0), 6, 6)


class ColorPicker(QWidget):
    """Editable #RRGGBB field paired with the native color panel."""

    def __init__(self, value: str, parent=None):
        super().__init__(parent)
        self.edit = QLineEdit(str(value), self)
        self.edit.setFixedWidth(96)
        self.button = ColorSwatchButton(value, self)
        self.button.clicked.connect(self.choose)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.edit)
        layout.addWidget(self.button)
        self.edit.textChanged.connect(self._sync_swatch)
        self._sync_swatch(self.edit.text())

    def text(self) -> str:
        return self.edit.text().strip()

    def choose(self) -> None:
        initial = QColor(self.text())
        color = QColorDialog.getColor(initial if initial.isValid() else QColor("#ffffff"), self, "选择颜色")
        if color.isValid():
            self.edit.setText(color.name(QColor.NameFormat.HexRgb))

    def _sync_swatch(self, value: str) -> None:
        color = QColor(value)
        if color.isValid():
            self.button.setColor(color)


def _draw_chevron(widget, center_y: float, *, down: bool) -> None:
    painter = QPainter(widget)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    color = QColor("#a8adb4" if _system_dark() else "#62676d") if widget.isEnabled() else QColor("#aeb2b7")
    painter.setPen(QPen(color, 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    center_x = widget.width() - 10.0
    offset = 1.8 if down else -1.8
    painter.drawLine(QPointF(center_x - 2.6, center_y - offset), QPointF(center_x, center_y + offset))
    painter.drawLine(QPointF(center_x, center_y + offset), QPointF(center_x + 2.6, center_y - offset))


MODERN_SELECT_POPUP_STYLESHEET = """
QMenu#ModernSelectPopup {
    background: #ffffff;
    color: #202020;
    border: 1px solid #d8d8d8;
    border-radius: 10px;
    padding: 6px;
    font-size: 13px;
}
QMenu#ModernSelectPopup::item {
    min-height: 22px;
    padding: 4px 28px 4px 12px;
    border-radius: 7px;
}
QMenu#ModernSelectPopup::item:selected { background: #eeeeee; }
QMenu#ModernSelectPopup::indicator { width: 0; height: 0; }
"""


class ModernSelect(QAbstractButton):
    """Custom-painted selector with a Modern-style popover, not a QComboBox."""

    currentIndexChanged = Signal(int)
    aboutToShowPopup = Signal()

    def __init__(self, parent=None, *, width: int = 132):
        super().__init__(parent)
        self._items: list[tuple[str, object]] = []
        self._index = -1
        self._hovered = False
        self._popup: QMenu | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedHeight(BROWSER_CONTROL_SPEC["field_height"])
        self.setFixedWidth(width)
        self.clicked.connect(self.showPopup)

    def addItem(self, text: str, data=None) -> None:  # noqa: N802
        self._items.append((str(text), data))
        if self._index < 0:
            self.setCurrentIndex(0)

    def count(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()
        self._index = -1
        self.setText("")
        self.update()

    def itemData(self, index: int):  # noqa: N802
        return self._items[index][1] if 0 <= index < len(self._items) else None

    def itemText(self, index: int) -> str:  # noqa: N802
        return self._items[index][0] if 0 <= index < len(self._items) else ""

    def setItemData(self, index: int, value, role=None) -> None:  # noqa: N802
        # Foreground roles are unnecessary because the custom popup owns its
        # palette; other calls update the stored data payload.
        if role is None and 0 <= index < len(self._items):
            text, _old = self._items[index]
            self._items[index] = (text, value)

    def findData(self, data) -> int:  # noqa: N802
        for index, (_, item_data) in enumerate(self._items):
            if item_data == data:
                return index
        return -1

    def setCurrentData(self, data) -> None:  # noqa: N802
        index = self.findData(data)
        if index >= 0:
            self.setCurrentIndex(index)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if not 0 <= index < len(self._items) or index == self._index:
            return
        self._index = index
        self.setText(self._items[index][0])
        self.currentIndexChanged.emit(index)
        self.update()

    def currentIndex(self) -> int:  # noqa: N802
        return self._index

    def currentData(self):  # noqa: N802
        return self._items[self._index][1] if 0 <= self._index < len(self._items) else None

    def currentText(self) -> str:  # noqa: N802
        return self._items[self._index][0] if 0 <= self._index < len(self._items) else ""

    @staticmethod
    def popupStyleSheet() -> str:  # noqa: N802
        if _system_dark():
            return MODERN_SELECT_POPUP_STYLESHEET + _DARK_POPUP_OVERRIDE
        return MODERN_SELECT_POPUP_STYLESHEET

    def showPopup(self) -> None:  # noqa: N802
        self.aboutToShowPopup.emit()
        popup = self._popup
        if popup is None:
            popup = QMenu(self)
            popup.setObjectName("ModernSelectPopup")
            popup.setStyleSheet(self.popupStyleSheet())
            self._popup = popup
        else:
            # Reuse one native popup instead of retaining a new child QMenu on
            # every open. Deleting on close is unsafe here because Qt performs
            # that deletion asynchronously while Python still owns the wrapper.
            popup.clear()
        popup.setMinimumWidth(self.width())
        for index, (text, _) in enumerate(self._items):
            action = QWidgetAction(popup)
            option = ModernSelectOption(text, index == self._index, popup)
            option.clicked.connect(lambda checked=False, index=index: self.setCurrentIndex(index))
            option.clicked.connect(popup.close)
            action.setDefaultWidget(option)
            popup.addAction(action)
        popup.popup(self.mapToGlobal(QPoint(0, self.height() + 4)))

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dark = _system_dark()
        bg, border_idle, fg = ("#2e2e35", "#4a4a54", "#e4e4e9") if dark else ("#ffffff", "#cfd4da", "#202124")
        hover_border = "#56565f" if dark else "#aeb6c0"
        border = "#0a84ff" if self.hasFocus() else (hover_border if self._hovered else border_idle)
        painter.setBrush(QColor(bg))
        painter.setPen(QPen(QColor(border), 1.5 if self.hasFocus() else 1.0))
        painter.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0), 8, 8)
        painter.setPen(QColor(fg))
        painter.drawText(QRectF(10, 0, self.width() - 34, self.height()), Qt.AlignmentFlag.AlignVCenter, self.currentText())
        painter.end()
        _draw_chevron(self, self.height() / 2.0, down=True)


class ModernSelectOption(QAbstractButton):
    def __init__(self, text: str, selected: bool, parent=None):
        super().__init__(parent)
        self._text = text
        self._selected = selected
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self.setMinimumWidth(116)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dark = _system_dark()
        hover_bg, fg, check = (
            ("#3a3a46", "#e4e4e9", "#a0a6b0") if dark else ("#eeeeee", "#202020", "#454545")
        )
        if self._hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(hover_bg))
            painter.drawRoundedRect(QRectF(2, 1, self.width() - 4, self.height() - 2), 7, 7)
        painter.setPen(QColor(fg))
        painter.drawText(QRectF(10, 0, self.width() - 36, self.height()), Qt.AlignmentFlag.AlignVCenter, self._text)
        if self._selected:
            pen = QPen(QColor(check), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            x = self.width() - 17.0
            painter.drawLine(QPointF(x - 4, 15), QPointF(x - 1, 18))
            painter.drawLine(QPointF(x - 1, 18), QPointF(x + 5, 10))


class BrowserSpinBox(QSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(92)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        _draw_chevron(self, self.height() * 0.29, down=False)
        _draw_chevron(self, self.height() * 0.71, down=True)


class BrowserDoubleSpinBox(QDoubleSpinBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(92)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        _draw_chevron(self, self.height() * 0.29, down=False)
        _draw_chevron(self, self.height() * 0.71, down=True)


class SettingRow(QFrame):
    """A label and hint on the left, with one control aligned to the right."""

    def __init__(self, key: str, title: str, hint: str, control: QWidget, parent=None, *, stacked: bool = False):
        super().__init__(parent)
        self.setObjectName(f"settingRow_{key}")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setProperty("stackedControl", stacked)
        label = QLabel(title, self)
        label.setObjectName("settingLabel")
        hint_label = QLabel(hint, self)
        hint_label.setObjectName("settingHint")
        hint_label.setWordWrap(True)
        if stacked:
            row = QVBoxLayout(self)
            row.setContentsMargins(14, 9, 14, 9)
            row.setSpacing(0)
            row.addWidget(label)
            row.addWidget(hint_label)
            row.addSpacing(7)
            row.addWidget(control)
        else:
            row = QHBoxLayout(self)
            row.setContentsMargins(14, 9, 14, 9)
            row.setSpacing(18)
            copy = QVBoxLayout()
            copy.setContentsMargins(0, 0, 0, 0)
            copy.setSpacing(2)
            copy.addWidget(label)
            copy.addWidget(hint_label)
            copy.addStretch(1)
            row.addLayout(copy, 1)
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.label = label
        self.hint_label = hint_label
        self.control = control


class SettingsCard(QFrame):
    def __init__(self, rows: list[SettingRow], parent=None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self.rows = list(rows)
        self.separators: list[QFrame] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for index, row in enumerate(rows):
            if index:
                separator = QFrame(self)
                separator.setObjectName("cardSeparator")
                separator.setFixedHeight(1)
                self.separators.append(separator)
                layout.addWidget(separator)
            layout.addWidget(row)
        self.refresh_separators()

    def refresh_separators(self) -> None:
        """Keep dividers attached to visible rows during progressive disclosure."""
        visible_before = False
        for index, row in enumerate(self.rows):
            if index:
                self.separators[index - 1].setVisible(not row.isHidden() and visible_before)
            visible_before = visible_before or not row.isHidden()


class SettingsSection(QWidget):
    def __init__(self, title: str, rows: list[SettingRow], parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        label = QLabel(title, self)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        layout.addWidget(SettingsCard(rows, self))


class AppEditDialog(QDialog):
    """Dialog for editing an application entry (name and path)."""

    def __init__(self, name: str = "", path: str = "", is_browser: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置快捷应用")
        self.setFixedWidth(420)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Dialog)
        self.is_browser = is_browser
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(name, self)
        self.name_edit.setPlaceholderText("应用名称（例如: PCL2）")
        form.addRow("应用名称:", self.name_edit)

        if not self.is_browser:
            path_box = QHBoxLayout()
            path_box.setSpacing(6)
            self.path_edit = QLineEdit(path, self)
            self.path_edit.setPlaceholderText("应用程序路径")
            self.browse_btn = QPushButton("浏览…", self)
            self.browse_btn.clicked.connect(self._browse_path)
            path_box.addWidget(self.path_edit, 1)
            path_box.addWidget(self.browse_btn)
            form.addRow("程序路径:", path_box)
        else:
            self.path_edit = None

        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton("取消", self)
        self.ok_btn = QPushButton("确定", self)
        self.ok_btn.setDefault(True)
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self._on_accept)
        self.name_edit.returnPressed.connect(self._on_accept)
        if self.path_edit is not None:
            self.path_edit.returnPressed.connect(self._on_accept)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.ok_btn)
        layout.addLayout(buttons)

    def _browse_path(self) -> None:
        if sys.platform == "win32":
            filter_str = "应用程序 (*.exe *.lnk *.bat *.cmd);;所有文件 (*)"
            default_dir = ""
        elif sys.platform == "darwin":
            filter_str = "应用程序 (*.app);;所有文件 (*)"
            default_dir = "/Applications" if os.path.isdir("/Applications") else ""
        else:
            filter_str = "所有文件 (*)"
            default_dir = ""
        new_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择应用程序",
            self.path_edit.text().strip() if self.path_edit else default_dir,
            filter_str,
        )
        if new_path and self.path_edit is not None:
            self.path_edit.setText(new_path)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(new_path).stem)

    def _on_accept(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.name_edit.setFocus()
            return
        if self.path_edit is not None and not self.path_edit.text().strip():
            self.path_edit.setFocus()
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "path": self.path_edit.text().strip() if self.path_edit is not None else "",
        }


def _line_edit(text: str = "", *, password: bool = False, width: int = 240) -> QLineEdit:
    edit = QLineEdit(text)
    edit.setMinimumWidth(width)
    if password:
        edit.setEchoMode(QLineEdit.EchoMode.Password)
    return edit


class QuickLaunchEditor(QWidget):
    """Small application picker persisted into the modern menu."""

    changed = Signal()

    def __init__(self, apps: list[dict], parent=None):
        super().__init__(parent)
        self.list = QListWidget(self)
        self.list.setObjectName("quickLaunchList")
        self.list.setMinimumHeight(116)
        self.list.setIconSize(QSize(22, 22))
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setDragEnabled(True)
        self.list.setAcceptDrops(True)
        self.list.setDropIndicatorShown(True)
        self.list.itemDoubleClicked.connect(self._edit_selected_item)

        self.add_button = QPushButton("添加应用", self)
        self.add_button.setIcon(vector_widget_icon(self, "add", 15))
        self.edit_button = QPushButton("编辑应用", self)
        self.edit_button.setIcon(vector_widget_icon(self, "settings", 15))
        self.default_button = QPushButton("添加默认浏览器", self)
        self.default_button.setIcon(vector_widget_icon(self, "web", 15))
        self.remove_button = QPushButton("移除勾选", self)
        self.remove_button.setIcon(vector_widget_icon(self, "remove", 15))

        self.add_button.clicked.connect(self._choose_application)
        self.edit_button.clicked.connect(self._edit_selected_app)
        self.default_button.clicked.connect(self._add_default_browser)
        self.remove_button.clicked.connect(self._remove_checked)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(7)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.default_button)
        buttons.addStretch(1)
        buttons.addWidget(self.remove_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.list)
        layout.addLayout(buttons)
        for item in apps:
            self.add_app(item, notify=False)

        model = self.list.model()
        if model is not None:
            model.rowsMoved.connect(lambda *args: self.changed.emit())
            model.rowsRemoved.connect(lambda *args: self.changed.emit())
            model.rowsInserted.connect(lambda *args: self.changed.emit())

    def add_app(self, app: dict, *, notify: bool = True) -> None:
        app = dict(app)
        if app.get("kind") == "default_browser":
            icon = vector_widget_icon(self, "web", 22)
            app = {"name": str(app.get("name") or "默认浏览器"), "path": "", "kind": "default_browser"}
        else:
            path = str(app.get("path") or "")
            if not path:
                return
            provider_icon = QFileIconProvider().icon(QFileInfo(path))
            if provider_icon.isNull():
                icon = vector_widget_icon(self, "application", 17)
            else:
                icon = fitted_application_icon(provider_icon, 22, self)
            app = {"name": str(app.get("name") or Path(path).stem), "path": path, "kind": "application"}
        item = QListWidgetItem(icon, app["name"])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, app)
        item.setToolTip(app["path"] or "使用系统默认浏览器")
        self.list.addItem(item)
        if notify:
            self.changed.emit()

    def apps(self) -> list[dict]:
        result = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data:
                result.append(dict(data))
        return result

    def _choose_application(self) -> None:
        if sys.platform == "win32":
            filter_str = "应用程序 (*.exe *.lnk *.bat *.cmd);;所有文件 (*)"
            default_dir = ""
        elif sys.platform == "darwin":
            filter_str = "应用程序 (*.app);;所有文件 (*)"
            default_dir = "/Applications" if os.path.isdir("/Applications") else ""
        else:
            filter_str = "所有文件 (*)"
            default_dir = ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择需要启动的应用",
            default_dir,
            filter_str,
        )
        if path:
            self.add_app({"name": Path(path).stem, "path": path, "kind": "application"})

    def _edit_selected_app(self) -> None:
        curr = self.list.currentItem()
        if curr is not None:
            self._edit_item(curr)

    def _edit_selected_item(self, item: QListWidgetItem) -> None:
        self._edit_item(item)

    def _edit_item(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        kind = data.get("kind", "application")
        is_browser = (kind == "default_browser")
        dlg = AppEditDialog(name=data.get("name", ""), path=data.get("path", ""), is_browser=is_browser, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_data = dlg.get_data()
            name = new_data["name"]
            path = new_data["path"] if not is_browser else ""
            if is_browser:
                updated = {"name": name or "默认浏览器", "path": "", "kind": "default_browser"}
                icon = vector_widget_icon(self, "web", 22)
            else:
                updated = {"name": name or Path(path).stem, "path": path, "kind": "application"}
                provider_icon = QFileIconProvider().icon(QFileInfo(path))
                if provider_icon.isNull():
                    icon = vector_widget_icon(self, "application", 17)
                else:
                    icon = fitted_application_icon(provider_icon, 22, self)
            item.setIcon(icon)
            item.setText(updated["name"])
            item.setData(Qt.ItemDataRole.UserRole, updated)
            item.setToolTip(updated["path"] or "使用系统默认浏览器")
            self.changed.emit()

    def _remove_checked(self) -> None:
        for index in range(self.list.count() - 1, -1, -1):
            if self.list.item(index).checkState() == Qt.CheckState.Checked:
                self.list.takeItem(index)
        self.changed.emit()

    def _add_default_browser(self) -> None:
        if not any(item.get("kind") == "default_browser" for item in self.apps()):
            self.add_app(DEFAULT_QUICK_LAUNCH_APPS[0])


class WebsiteEditDialog(QDialog):
    """Dialog for adding or editing a website entry."""

    def __init__(self, name: str = "", url: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("配置快捷网址")
        self.setFixedWidth(380)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.Dialog)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.name_edit = QLineEdit(name, self)
        self.name_edit.setPlaceholderText("网页名称（可选，留空则默认显示网址）")
        self.url_edit = QLineEdit(url, self)
        self.url_edit.setPlaceholderText("例如: https://github.com")

        form.addRow("网页名称:", self.name_edit)
        form.addRow("网址 URL:", self.url_edit)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = QPushButton("取消", self)
        self.ok_btn = QPushButton("确定", self)
        self.ok_btn.setDefault(True)
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self._on_accept)
        self.name_edit.returnPressed.connect(self._on_accept)
        self.url_edit.returnPressed.connect(self._on_accept)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.ok_btn)
        layout.addLayout(buttons)

    def _on_accept(self) -> None:
        url = self.url_edit.text().strip()
        if not url:
            self.url_edit.setFocus()
            return
        self.accept()

    def get_data(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "url": self.url_edit.text().strip(),
        }


class WebsiteLinksEditor(QWidget):
    """Small website picker persisted into the modern menu."""

    changed = Signal()

    def __init__(self, websites: list[dict], parent=None):
        super().__init__(parent)
        self.list = QListWidget(self)
        self.list.setObjectName("quickWebsiteList")
        self.list.setMinimumHeight(116)
        self.list.setIconSize(QSize(20, 20))
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setDragEnabled(True)
        self.list.setAcceptDrops(True)
        self.list.setDropIndicatorShown(True)
        self.list.itemDoubleClicked.connect(self._edit_selected_item)

        self.add_button = QPushButton("添加网址", self)
        self.add_button.setIcon(vector_widget_icon(self, "add", 15))
        self.edit_button = QPushButton("编辑网址", self)
        self.edit_button.setIcon(vector_widget_icon(self, "settings", 15))
        self.default_button = QPushButton("恢复默认网址", self)
        self.default_button.setIcon(vector_widget_icon(self, "refresh", 15))
        self.remove_button = QPushButton("移除勾选", self)
        self.remove_button.setIcon(vector_widget_icon(self, "remove", 15))

        self.add_button.clicked.connect(self._add_website_dialog)
        self.edit_button.clicked.connect(self._edit_selected_website)
        self.default_button.clicked.connect(self._reset_defaults)
        self.remove_button.clicked.connect(self._remove_checked)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(7)
        buttons.addWidget(self.add_button)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.default_button)
        buttons.addStretch(1)
        buttons.addWidget(self.remove_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

        for item in websites:
            self.add_website(item, notify=False)

        model = self.list.model()
        if model is not None:
            model.rowsMoved.connect(lambda *args: self.changed.emit())
            model.rowsRemoved.connect(lambda *args: self.changed.emit())
            model.rowsInserted.connect(lambda *args: self.changed.emit())

    def add_website(self, site: dict, *, notify: bool = True) -> None:
        name = str(site.get("name") or "").strip()
        url = str(site.get("url") or "").strip()
        if not url:
            return
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file://")):
            url = "https://" + url
        display_text = f"{name} ({url})" if name else url
        icon = vector_widget_icon(self, "web", 20)
        item = QListWidgetItem(icon, display_text)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, {"name": name, "url": url})
        item.setToolTip(f"网址: {url}\n名称: {name or '（默认显示网址）'}")
        self.list.addItem(item)
        if notify:
            self.changed.emit()

    def websites(self) -> list[dict]:
        result = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(data, dict) and data:
                result.append(dict(data))
        return result

    def _add_website_dialog(self) -> None:
        dlg = WebsiteEditDialog(parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            data = dlg.get_data()
            if data.get("url"):
                self.add_website(data)

    def _edit_selected_website(self) -> None:
        curr = self.list.currentItem()
        if curr is not None:
            self._edit_item(curr)

    def _edit_selected_item(self, item: QListWidgetItem) -> None:
        self._edit_item(item)

    def _edit_item(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole) or {}
        dlg = WebsiteEditDialog(name=data.get("name", ""), url=data.get("url", ""), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_data = dlg.get_data()
            if new_data.get("url"):
                name = new_data["name"]
                url = new_data["url"]
                if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file://")):
                    url = "https://" + url
                item.setData(Qt.ItemDataRole.UserRole, {"name": name, "url": url})
                item.setText(f"{name} ({url})" if name else url)
                item.setToolTip(f"网址: {url}\n名称: {name or '（默认显示网址）'}")
                self.changed.emit()

    def _remove_checked(self) -> None:
        for index in range(self.list.count() - 1, -1, -1):
            if self.list.item(index).checkState() == Qt.CheckState.Checked:
                self.list.takeItem(index)
        self.changed.emit()

    def _reset_defaults(self) -> None:
        self.list.clear()
        for item in DEFAULT_QUICK_WEBSITES:
            self.add_website(item, notify=False)
        self.changed.emit()


class ModernSettingsDialog(QDialog):
    """Settings window matching Modern's sidebar and rounded-card hierarchy."""

    settings_saved = Signal()

    def __init__(self, config, parent=None, *, include_ai: bool = False):
        super().__init__(parent)
        self.config = config
        self.setProperty("modernStyle", True)
        self.setProperty("menuStyle", "modern")
        self.setWindowTitle("桌宠设置")
        self.resize(800, 560)
        self.setMinimumSize(720, 500)
        self._positioned_away = False
        self.setModal(False)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        font.setPixelSize(13)
        self.setFont(font)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        sidebar_pane = QFrame(self)
        sidebar_pane.setObjectName("sidebarPane")
        sidebar_pane.setFixedWidth(188)
        sidebar_layout = QVBoxLayout(sidebar_pane)
        sidebar_layout.setContentsMargins(12, 16, 12, 12)
        sidebar_layout.setSpacing(9)
        self.save_exit_button = QPushButton("保存并退出", sidebar_pane)
        self.save_exit_button.setObjectName("saveAndExit")
        self.save_exit_button.setIcon(vector_widget_icon(self.save_exit_button, "back", 16))
        self.save_exit_button.clicked.connect(self._save)
        self.save_exit_button.setAutoDefault(False)
        self.save_exit_button.setDefault(False)
        sidebar_layout.addWidget(self.save_exit_button)
        self.search_edit = QLineEdit(sidebar_pane)
        self.search_edit.setObjectName("settingsSearch")
        self.search_edit.setPlaceholderText("搜索设置…")
        self.search_edit.addAction(
            vector_widget_icon(self, "search", 16),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_edit.installEventFilter(self)
        sidebar_layout.addWidget(self.search_edit)
        self.search_status = QLabel("", sidebar_pane)
        self.search_status.setObjectName("searchStatus")
        self.search_status.setWordWrap(True)
        self.search_status.hide()
        sidebar_layout.addWidget(self.search_status)
        self.sidebar = QListWidget(sidebar_pane)
        self.sidebar.setObjectName("settingsSidebar")
        self.sidebar.setSpacing(2)
        sidebar_layout.addWidget(self.sidebar, 1)

        self.pages = QStackedWidget(self)
        body.addWidget(sidebar_pane)
        body.addWidget(self.pages, 1)
        root.addLayout(body, 1)

        self._build_pet_controls()

        general_content = QWidget()
        general_layout = QVBoxLayout(general_content)
        general_layout.setContentsMargins(0, 0, 0, 0)
        general_layout.setSpacing(18)
        autostart_desc = "登录系统后自动启动桌宠。"
        launch_rows = [
            SettingRow("autostart", "开机自启", autostart_desc, self.autostart_check),
            SettingRow("system_notifications", "系统通知", "允许重要事件显示桌面通知。", self.system_notifications_check),
        ]
        if sys.platform == "darwin":
            launch_rows.append(SettingRow(
                "dock_icon", "显示 Dock 图标", "在 macOS Dock 中显示桌宠应用；关闭后仍可通过桌宠和托盘操作。",
                self.dock_icon_check,
            ))
        general_layout.addWidget(SettingsSection("应用启动", launch_rows, general_content))
        window_rows = [
            SettingRow("on_top", "窗口置顶", "始终将桌宠保持在其他窗口上方。", self.on_top_check),
        ]
        if sys.platform == "win32":
            window_rows.extend([
                SettingRow("auto_hide_fullscreen", "全屏时自动隐藏", "全屏游戏或视频期间自动隐藏桌宠。", self.auto_hide_fullscreen_check),
            ])
        general_layout.addWidget(SettingsSection("窗口与系统", window_rows, general_content))

        general_layout.addStretch(1)
        self._add_page("常规", "settings", self._page_shell("常规", general_content))

        behavior_content = QWidget()
        behavior_layout = QVBoxLayout(behavior_content)
        behavior_layout.setContentsMargins(0, 0, 0, 0)
        behavior_layout.setSpacing(16)
        behavior_layout.addWidget(SettingsSection("动画", [
            SettingRow("playback_speed", "播放速率", "控制所有桌宠动画的播放速度。", self.speed_select),
            SettingRow("animation_gap", "动作等待间隔", "非待机动作之间的休息时间；0 秒表示连续播放。", self.gap_spin),
            SettingRow("no_move", "不移动", "暂停桌宠在桌面上的自动移动。", self.no_move_check),
            SettingRow("music_sing", "音乐自动唱歌", "检测到后台播放音乐时，自动播放唱歌动画。", self.music_sing_check),
        ], behavior_content))
        behavior_layout.addWidget(SettingsSection("拖拽", [
            SettingRow("lock_position", "锁定位置", "桌宠固定不动，无法拖动（点击互动仍有效）。", self.lock_position_check),
            SettingRow("shift_drag", "SHIFT+左键拖动", "开启后必须按住 SHIFT 再左键才能拖动桌宠。", self.shift_drag_check),
        ], behavior_content))
        behavior_layout.addWidget(SettingsSection("漫步与调头", [
            SettingRow("smart_edge_turn", "边缘智能调头", "漫步贴近屏幕边缘且前方空间不足时，自动转身面向开阔区域，避免在角落卡住。", self.smart_edge_turn_check),
            SettingRow("smooth_wander", "平滑加减速漫步", "漫步移动时启用 SmoothStep 平滑加减速缓动，起步与停步更轻柔自然。", self.smooth_wander_check),
        ], behavior_content))
        click_rows = [
            SettingRow("click_self_talk", "点击触发自言自语", "点击时随机显示一条自言自语内容。", self.click_self_talk_check),
        ]
        behavior_layout.addWidget(SettingsSection("点击反馈", click_rows, behavior_content))
        behavior_layout.addWidget(SettingsSection("自言自语", [
            SettingRow("self_talk", "气泡自言自语", "让桌宠偶尔显示一条随机思考气泡。", self.self_talk_check),
            SettingRow("self_talk_duration", "显示时间", "每条文字或图片气泡保持显示的时间。", self.self_talk_duration_spin),
            SettingRow("self_talk_min", "最短间隔", "上一条气泡消失后，到下一条出现前的最短空闲时间。", self.min_spin),
            SettingRow("self_talk_max", "最长间隔", "上一条气泡消失后，到下一条出现前的最长空闲时间。", self.max_spin),
            SettingRow("self_talk_texts", "候选内容", "每行一条；留空时恢复内置文本。", self.texts_edit, stacked=True),
            SettingRow("self_talk_images", "图片目录", "从目录中的常见图片格式随机选择；默认使用内置彩蛋图片池，留空时只显示文本。", self.self_talk_image_dir_picker, stacked=True),
            SettingRow("click_talk_bindings", "点击动画台词绑定", "为每个点击动画设置专属自言自语台词。", self.click_talk_bindings_btn),
        ], behavior_content))
        # Agent 联动：每个 Agent 一行自定义思考文案
        agent_thinking_rows = []
        for agent_key, edit in self.thinking_text_edits.items():
            agent_name = AgentLinkManager.AGENT_NAMES.get(agent_key, agent_key)
            default = AgentLinkManager._THINKING_DEFAULTS.get(agent_key, f"{agent_name} 正在深度烧烤……")
            agent_thinking_rows.append(
                SettingRow(f"agent_thinking_{agent_key}", f"{agent_name} 思考文案",
                           f"默认：{default}；支持 {{name}} 占位符；留空用默认。",
                           edit, stacked=True)
            )
        behavior_layout.addWidget(SettingsSection("Agent 联动 · 思考气泡文案", agent_thinking_rows, behavior_content))
        behavior_layout.addStretch(1)
        self._add_page("桌宠行为", "play", self._page_shell("桌宠行为", behavior_content))

        appearance_content = QWidget()
        appearance_layout = QVBoxLayout(appearance_content)
        appearance_layout.setContentsMargins(0, 0, 0, 0)
        appearance_layout.setSpacing(16)
        appearance_layout.addWidget(SettingsSection("桌宠显示", [
            SettingRow("scale", "桌宠大小", "调整桌宠在桌面上的显示尺寸。", self.scale_combo),
            SettingRow("pet_opacity", "不透明度", "调整桌宠窗口的整体透明度；100% 为完全不透明。", self.pet_opacity_spin),
            SettingRow(
                "self_talk_bubble_style", "气泡方案",
                "选择气泡视觉与相对桌宠的位置；贴近屏幕边缘时自动换位。",
                self.bubble_style_select,
            ),
        ], appearance_content))
        appearance_layout.addWidget(SettingsSection("菜单外观", [
            SettingRow("menu_theme", "颜色主题", "可跟随系统，或固定使用浅色/深色菜单。", self.menu_theme_select),
            SettingRow("menu_density", "菜单密度", "调整新版右键菜单的菜单项高度和分组留白。", self.menu_density_select),
            SettingRow("menu_radius", "圆角大小", "调整新版右键菜单和子菜单的外轮廓圆角。", self.menu_radius_select),
            SettingRow("menu_font", "UI 字体", "设置新版菜单使用的界面字体。", self.menu_font_select),
            SettingRow("menu_font_size", "UI 字号", "同步调整主菜单与多级菜单的字号。", self.menu_font_size_select),
            SettingRow("menu_translucent", "半透明菜单", "使用接近 Modern 的半透明浮层表面。", self.menu_translucent_check),
            SettingRow("menu_opacity", "表面不透明度", "调整菜单背景透出桌面内容的程度。", self.menu_opacity_spin),
        ], appearance_content))
        appearance_layout.addWidget(SettingsSection("浅色主题", [
            SettingRow("light_background", "背景色", "浅色菜单的浮层背景。", self.light_background_picker),
            SettingRow("light_foreground", "文字色", "浅色菜单的主要文字与图标颜色。", self.light_foreground_picker),
            SettingRow("light_hover", "悬停色", "鼠标悬停菜单项时的背景。", self.light_hover_picker),
        ], appearance_content))
        appearance_layout.addWidget(SettingsSection("深色主题", [
            SettingRow("dark_background", "背景色", "深色菜单的浮层背景。", self.dark_background_picker),
            SettingRow("dark_foreground", "文字色", "深色菜单的主要文字与图标颜色。", self.dark_foreground_picker),
            SettingRow("dark_hover", "悬停色", "鼠标悬停菜单项时的背景。", self.dark_hover_picker),
        ], appearance_content))
        appearance_layout.addWidget(SettingsSection("彩蛋入口", [
            SettingRow("egg_enabled", "显示彩蛋", "控制新版菜单首行彩蛋入口是否显示。", self.egg_enabled_check),
            SettingRow("egg_title", "入口标题", "显示在圆形头像右侧的文字。", self.egg_title_edit),
            SettingRow("egg_hint", "右侧提示", "显示在鼠标指针图标后的短提示。", self.egg_hint_edit),
            SettingRow("egg_avatar", "头像图片", "使用绝对路径；支持常见图片格式。", self.egg_avatar_picker),
            SettingRow("egg_image_dir", "弹窗图片目录", "使用绝对路径；每次点击会随机选择一张图片。", self.egg_image_dir_picker),
        ], appearance_content))
        appearance_layout.addStretch(1)
        self._add_page("外观", "appearance", self._page_shell("外观", appearance_content))

        launcher_content = QWidget()
        launcher_layout = QVBoxLayout(launcher_content)
        launcher_layout.setContentsMargins(0, 0, 0, 0)
        launcher_layout.setSpacing(18)
        self.quick_launch_editor = QuickLaunchEditor(
            self.config.get("quick_launch_apps", DEFAULT_QUICK_LAUNCH_APPS),
            launcher_content,
        )
        self.quick_launch_editor.changed.connect(self._sync_quick_launch_apps)
        launcher_layout.addWidget(SettingsSection("已配置应用", [
            SettingRow(
                "quick_launch_apps",
                "启动应用",
                "这些应用将按图标和名称显示在右键菜单的“启动应用”子菜单中。",
                self.quick_launch_editor,
                stacked=True,
            ),
        ], launcher_content))
        launcher_layout.addStretch(1)
        self._add_page("启动应用", "application", self._page_shell("启动应用", launcher_content))

        website_content = QWidget()
        website_layout = QVBoxLayout(website_content)
        website_layout.setContentsMargins(0, 0, 0, 0)
        website_layout.setSpacing(18)
        self.website_links_editor = WebsiteLinksEditor(
            self.config.get("quick_websites", DEFAULT_QUICK_WEBSITES),
            website_content,
        )
        self.website_links_editor.changed.connect(self._sync_quick_websites)
        website_layout.addWidget(SettingsSection("快捷网址", [
            SettingRow(
                "quick_websites",
                "快捷网址",
                "配置右键菜单“快捷网址”子菜单中的网页。可自定义命名；未命名时直接显示网址。",
                self.website_links_editor,
                stacked=True,
            ),
        ], website_content))
        website_layout.addStretch(1)
        self._add_page("快捷网址", "web", self._page_shell("快捷网址", website_content))

        self.sidebar.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        self._search_rows = self.findChildren(SettingRow)
        self._search_matches: list[SettingRow] = []
        self._search_index = -1
        self.search_edit.textChanged.connect(self._search_settings)

        self.self_talk_check.toggled.connect(self._update_self_talk_controls)
        self.menu_translucent_check.toggled.connect(self._update_translucency_controls)
        self._update_self_talk_controls(self.self_talk_check.isChecked())
        self._update_translucency_controls(self.menu_translucent_check.isChecked())
        self.setStyleSheet(self._stylesheet())

    def _build_pet_controls(self) -> None:
        self.scale_combo = ModernSelect(self, width=132)
        current_scale = float(self.config.get("scale", catalog.DEFAULT_SCALE))
        scales = list(catalog.SCALE_STEPS)
        if not any(abs(current_scale - value) < 0.001 for value in scales):
            scales.append(current_scale)
            scales.sort()
        for scale in scales:
            self.scale_combo.addItem(f"{int(round(catalog.CANVAS_W * scale))} px", scale)
        self.scale_combo.setCurrentIndex(self.scale_combo.findData(current_scale))

        self.on_top_check = ToggleSwitch(self)
        self.on_top_check.setChecked(bool(self.config.get("on_top", True)))
        self.no_move_check = ToggleSwitch(self)
        self.no_move_check.setChecked(bool(self.config.get("no_move", False)))

        # 漫步调头与缓动优化
        self.smart_edge_turn_check = ToggleSwitch(self)
        self.smart_edge_turn_check.setChecked(bool(self.config.get("smart_edge_turn", True)))
        self.smooth_wander_check = ToggleSwitch(self)
        self.smooth_wander_check.setChecked(bool(self.config.get("smooth_wander", True)))

        self.lock_position_check = ToggleSwitch(self)
        self.lock_position_check.setChecked(bool(self.config.get("lock_position", False)))
        self.shift_drag_check = ToggleSwitch(self)
        self.shift_drag_check.setChecked(bool(self.config.get("shift_drag", False)))
        self.pet_opacity_spin = BrowserSpinBox(self)
        self.pet_opacity_spin.setRange(10, 100)
        self.pet_opacity_spin.setSuffix(" %")
        self.pet_opacity_spin.setValue(int(_float_or_default(self.config.get("pet_opacity", 100), 100, 10, 100)))
        self.autostart_check = ToggleSwitch(self)
        self._autostart_initial = autostart_mod.is_enabled()
        self.autostart_check.setChecked(self._autostart_initial)
        self.system_notifications_check = ToggleSwitch(self)
        self.system_notifications_check.setChecked(bool(self.config.get("system_notifications_enabled", True)))
        self.dock_icon_check = None
        if sys.platform == "darwin":
            self.dock_icon_check = ToggleSwitch(self)
            self.dock_icon_check.setChecked(bool(self.config.get("show_dock_icon", True)))

        self.click_self_talk_check = ToggleSwitch(self)
        self.click_self_talk_check.setChecked(bool(self.config.get("click_show_self_talk", False)))
        self.music_sing_check = ToggleSwitch(self)
        self.music_sing_check.setChecked(bool(self.config.get("music_sing_enabled", False)))

        self.auto_hide_fullscreen_check = None
        if sys.platform == "win32":
            self.auto_hide_fullscreen_check = ToggleSwitch(self)
            self.auto_hide_fullscreen_check.setChecked(bool(self.config.get("auto_hide_fullscreen", True)))

        self.speed_select = ModernSelect(self, width=112)
        current_speed = float(self.config.get("playback_speed", 1.0))
        speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0]
        if not any(abs(current_speed - value) < 0.001 for value in speeds):
            speeds.append(current_speed)
            speeds.sort()
        for speed in speeds:
            self.speed_select.addItem(f"{speed:g}x", speed)
        self.speed_select.setCurrentData(current_speed)
        self.gap_spin = BrowserDoubleSpinBox(self)
        self.gap_spin.setRange(0.0, 3600.0)
        self.gap_spin.setSingleStep(0.5)
        self.gap_spin.setDecimals(1)
        self.gap_spin.setSuffix(" 秒")
        self.gap_spin.setValue(float(self.config.get("animation_gap_seconds", 0.0)))

        self.self_talk_check = ToggleSwitch(self)
        self.self_talk_check.setChecked(bool(self.config.get("self_talk_enabled", False)))
        self.self_talk_duration_spin = BrowserDoubleSpinBox(self)
        self.self_talk_duration_spin.setRange(1.0, 300.0)
        self.self_talk_duration_spin.setSingleStep(0.5)
        self.self_talk_duration_spin.setDecimals(1)
        self.self_talk_duration_spin.setSuffix(" 秒")
        self.self_talk_duration_spin.setValue(float(self.config.get(
            "self_talk_duration_seconds", DEFAULT_SELF_TALK_DURATION_SECONDS
        )))
        self.bubble_style_select = ModernSelect(self, width=172)
        for value, preset in BUBBLE_STYLE_PRESETS.items():
            self.bubble_style_select.addItem(str(preset["label"]), value)
        self.bubble_style_select.setCurrentData(
            str(self.config.get("self_talk_bubble_style", DEFAULT_SELF_TALK_BUBBLE_STYLE))
        )
        self.min_spin = BrowserDoubleSpinBox(self)
        self.max_spin = BrowserDoubleSpinBox(self)
        for spin, value in (
            (self.min_spin, self.config.get("self_talk_min_interval", DEFAULT_SELF_TALK_MIN_INTERVAL)),
            (self.max_spin, self.config.get("self_talk_max_interval", DEFAULT_SELF_TALK_MAX_INTERVAL)),
        ):
            spin.setRange(5.0, 3600.0)
            spin.setDecimals(0)
            spin.setSuffix(" 秒")
            spin.setValue(float(value))
        self.texts_edit = QPlainTextEdit(self)
        self.texts_edit.setMinimumSize(240, 82)
        self.texts_edit.setMaximumHeight(170)
        texts = self.config.get("self_talk_texts", DEFAULT_SELF_TALK_TEXTS)
        self.texts_edit.setPlainText("\n".join(str(item) for item in texts))
        self.self_talk_image_dir_picker = ResourcePathPicker(
            str(self.config.get("self_talk_image_dir", "") or ""),
            directory=True,
            parent=self,
        )
        self.click_talk_bindings_btn = QPushButton("编辑…", self)
        self.click_talk_bindings_btn.setObjectName("clickTalkBindingsButton")
        self.click_talk_bindings_btn.clicked.connect(self._open_click_talk_bindings)

        # Agent 联动：每个 Agent 的自定义 thinking 气泡文案
        agent_link_cfg = self.config.get("agent_link", {})
        thinking_texts = agent_link_cfg.get("thinking_texts") or {}
        # 兼容旧的全局 thinking_text 字段
        legacy_text = str(agent_link_cfg.get("thinking_text", "") or "")
        self.thinking_text_edits: dict[str, QLineEdit] = {}
        for agent_key, agent_name in AgentLinkManager.AGENT_NAMES.items():
            edit = QLineEdit(self)
            default = AgentLinkManager._THINKING_DEFAULTS.get(agent_key, f"{agent_name} 正在深度烧烤……")
            edit.setPlaceholderText(default)
            text = str(thinking_texts.get(agent_key, "") or "")
            if not text and legacy_text:
                text = legacy_text
            edit.setText(text)
            edit.setClearButtonEnabled(True)
            self.thinking_text_edits[agent_key] = edit

        appearance = self.config.get("context_menu_appearance", DEFAULT_CONTEXT_MENU_APPEARANCE)
        self.menu_theme_select = ModernSelect(self, width=132)
        for label, value in (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")):
            self.menu_theme_select.addItem(label, value)
        self.menu_theme_select.setCurrentData(appearance.get("theme", "system"))
        self.menu_density_select = ModernSelect(self, width=132)
        for label, value in (("紧凑", "compact"), ("标准", "standard"), ("宽松", "spacious")):
            self.menu_density_select.addItem(label, value)
        self.menu_density_select.setCurrentData(appearance.get("density", "standard"))
        self.menu_radius_select = ModernSelect(self, width=112)
        for radius in (8, 12, 16, 18):
            self.menu_radius_select.addItem(f"{radius} px", radius)
        self.menu_radius_select.setCurrentData(int(appearance.get("corner_radius", 12)))
        self.menu_font_select = ModernSelect(self, width=172)
        self.menu_font_select.addItem("系统默认", "system")
        self._menu_fonts_populated = False
        current_font = str(appearance.get("ui_font") or "system")
        if current_font != "system":
            # 保留当前配置值无需枚举字体库，确保用户未展开选择器直接保存时
            # 不会把自定义字体静默重置为 system。
            self.menu_font_select.addItem(current_font, current_font)
        self.menu_font_select.setCurrentData(current_font)
        # Windows 字体较多时首次枚举可阻塞数秒。零延迟定时器仍会在
        # 设置窗口首帧绘制前运行，因此改为仅在用户真正展开字体选择器时加载。
        self.menu_font_select.aboutToShowPopup.connect(self._populate_menu_fonts)
        self.menu_font_size_select = ModernSelect(self, width=112)
        for size in range(10, 19):
            self.menu_font_size_select.addItem(f"{size} px", size)
        self.menu_font_size_select.setCurrentData(int(appearance.get("ui_font_size", 13)))
        self.menu_translucent_check = ToggleSwitch(self)
        self.menu_translucent_check.setChecked(bool(appearance.get("translucent", True)))
        self.menu_opacity_spin = BrowserDoubleSpinBox(self)
        self.menu_opacity_spin.setRange(0.72, 1.0)
        self.menu_opacity_spin.setSingleStep(0.02)
        self.menu_opacity_spin.setDecimals(2)
        self.menu_opacity_spin.setValue(float(appearance.get("opacity", 0.94)))

        def color_picker(key: str) -> ColorPicker:
            return ColorPicker(str(appearance.get(key) or DEFAULT_CONTEXT_MENU_APPEARANCE[key]), self)

        self.light_background_picker = color_picker("light_background")
        self.light_foreground_picker = color_picker("light_foreground")
        self.light_hover_picker = color_picker("light_hover")
        self.dark_background_picker = color_picker("dark_background")
        self.dark_foreground_picker = color_picker("dark_foreground")
        self.dark_hover_picker = color_picker("dark_hover")

        egg = self.config.get("menu_easter_egg", DEFAULT_MENU_EASTER_EGG)
        self.egg_enabled_check = ToggleSwitch(self)
        self.egg_enabled_check.setChecked(bool(egg.get("enabled", True)))
        self.egg_title_edit = _line_edit(str(egg.get("title") or "厉害了我的鲸"), width=240)
        self.egg_hint_edit = _line_edit(str(egg.get("hint") or "请点击"), width=160)
        avatar = resolve_fun_asset(egg.get("avatar"), oijingjing_image_path())
        image_dir = resolve_fun_asset(egg.get("image_dir"), oijingjing_image_path().parent)
        self.egg_avatar_picker = ResourcePathPicker(str(avatar.resolve()), parent=self)
        self.egg_image_dir_picker = ResourcePathPicker(str(image_dir.resolve()), directory=True, parent=self)

    def _update_self_talk_controls(self, enabled: bool) -> None:
        keys = (
            "self_talk_duration", "self_talk_min", "self_talk_max",
            "self_talk_texts", "self_talk_images", "click_self_talk",
            "click_talk_bindings",
        )
        controls = (
            self.self_talk_duration_spin, self.min_spin, self.max_spin,
            self.texts_edit, self.self_talk_image_dir_picker,
            self.click_self_talk_check, self.click_talk_bindings_btn,
        )
        for key, control in zip(keys, controls):
            control.setEnabled(bool(enabled))
            row = self.findChild(SettingRow, f"settingRow_{key}")
            if row is not None:
                row.setEnabled(bool(enabled))

    def _populate_menu_fonts(self) -> None:
        if shiboken6.isValid(self) is False or self._menu_fonts_populated:
            return
        self._menu_fonts_populated = True
        appearance = self.config.get("context_menu_appearance", DEFAULT_CONTEXT_MENU_APPEARANCE)
        for family in _system_font_families():
            if self.menu_font_select.findData(family) < 0:
                self.menu_font_select.addItem(family, family)
        current_font = str(appearance.get("ui_font") or "system")
        if self.menu_font_select.findData(current_font) < 0:
            self.menu_font_select.addItem(current_font, current_font)
        self.menu_font_select.setCurrentData(current_font)

    def _open_click_talk_bindings(self) -> None:
        from pet.ui.click_talk_dialog import ClickTalkBindingsDialog

        click_names = None
        parent = self.parent()
        if parent is not None and hasattr(parent, "clicks") and parent.clicks:
            click_names = list(parent.clicks)
        dialog = ClickTalkBindingsDialog(self.config, click_names=click_names, parent=self)
        dialog.exec()

    def _update_translucency_controls(self, enabled: bool) -> None:
        self.menu_opacity_spin.setEnabled(bool(enabled))
        row = self.findChild(SettingRow, "settingRow_menu_opacity")
        if row is not None:
            row.setEnabled(bool(enabled))

    def move_away_from_pet(self) -> None:
        """把窗口定位到不与桌宠相交的位置。

        在 show() 之前调用（_present_dialog 的 before_present），窗口首帧
        即落在最终位置，避免 Windows 上"先显示默认位置再跳走"的两段式。
        """
        if self._positioned_away:
            return
        self._positioned_away = True
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            self._move_away_from(parent.geometry())

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        super().showEvent(event)
        # 兜底：未经 _present_dialog 直接 show 的路径仍要避让桌宠
        self.move_away_from_pet()

    def _move_away_from(self, pet_geo: QRect) -> None:
        """首次显示时把窗口移到不与桌宠相交的位置（右侧优先，再左侧/下方/上方）。"""
        size = self.size()
        screen = self.screen() or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen is not None else QRect()
        for rect in (
            QRect(pet_geo.right() + 12, pet_geo.top(), size.width(), size.height()),
            QRect(pet_geo.left() - 12 - size.width(), pet_geo.top(), size.width(), size.height()),
            QRect(pet_geo.left(), pet_geo.bottom() + 12, size.width(), size.height()),
            QRect(pet_geo.left(), pet_geo.top() - 12 - size.height(), size.width(), size.height()),
        ):
            if avail.contains(rect):
                self.move(rect.topLeft())
                return

    def _page_shell(self, title: str, content: QWidget) -> QWidget:
        page = QWidget(self.pages)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(28, 22, 26, 18)
        layout.setSpacing(12)
        heading = QLabel(title, page)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        scroll = QScrollArea(page)
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        content.setMaximumWidth(960)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        return page

    def _add_page(self, label: str, icon_name: str, page: QWidget) -> None:
        item = QListWidgetItem(vector_widget_icon(self, icon_name, 16), label)
        item.setSizeHint(QSize(0, 34))
        self.sidebar.addItem(item)
        self.pages.addWidget(page)

    def _clear_search_matches(self) -> None:
        for row in self._search_rows:
            if row.property("searchMatch"):
                row.setProperty("searchMatch", False)
                row.style().unpolish(row)
                row.style().polish(row)

    def _search_settings(self, query: str, *, advance: bool = False) -> None:
        query = query.strip().lower()
        self._clear_search_matches()
        if not query:
            self._search_matches = []
            self._search_index = -1
            self.search_status.hide()
            return
        matches = [
            row for row in self._search_rows
            if query in f"{row.label.text()} {row.hint_label.text()} {row.objectName()}".lower()
        ]
        if not matches:
            self._search_matches = []
            self._search_index = -1
            self.search_status.setText("未找到匹配的设置")
            self.search_status.show()
            return
        if matches != self._search_matches:
            self._search_matches = matches
            self._search_index = 0
        elif advance:
            self._search_index = (self._search_index + 1) % len(matches)
        row = matches[self._search_index]
        row.setProperty("searchMatch", True)
        row.style().unpolish(row)
        row.style().polish(row)
        page_index = 0
        for index in range(self.pages.count()):
            if self.pages.widget(index).isAncestorOf(row):
                page_index = index
                break
        self.sidebar.setCurrentRow(page_index)
        page = self.pages.widget(page_index)
        scroll = page.findChild(QScrollArea, "settingsScroll")
        if scroll is not None:
            scroll.ensureWidgetVisible(row, 0, 24)
        self.search_status.setText(
            f"{self._search_index + 1}/{len(matches)} · {row.label.text()}"
        )
        self.search_status.show()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if (
            watched is self.search_edit
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            self._search_settings(self.search_edit.text(), advance=True)
            return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _stylesheet() -> str:
        return _settings_stylesheet()



    def _apply_autostart(self) -> None:
        """应用「开机自启」开关：仅在实际改动时写入系统登录项。

        保存按钮与直接关闭（X / Esc）共用，保证三条路径行为一致。
        """
        if self.autostart_check.isChecked() != self._autostart_initial:
            # set_enabled 返回 bool（enable()/disable()）；仅在明确失败时提示。
            ok = autostart_mod.set_enabled(self.autostart_check.isChecked())
            if ok is False:
                QMessageBox.warning(
                    self,
                    "开机自启设置失败",
                    "写入开机自启失败：可能被安全软件拦截。\n"
                    "可稍后在托盘菜单重试，或检查安全软件/系统优化工具的拦截记录。",
                )

    def _save(self) -> None:
        """「保存并退出」：写入配置并关闭对话框。"""
        self._saved_via_button = True
        self._write_config()
        self._apply_autostart()
        self.settings_saved.emit()
        self.accept()

    def _write_config(self) -> bool:
        """把当前控件值写入 config 并落盘（按钮与直接关闭共用）。

        保存前从磁盘重读：吸收外部对本对话框未暴露字段的改动。
        已知限制：已暴露字段仍是 last-writer-wins（对话框获胜）。
        返回是否成功落盘；失败时提示用户。
        """
        self.config._load()
        minimum = min(self.min_spin.value(), self.max_spin.value())
        maximum = max(self.min_spin.value(), self.max_spin.value())
        texts = [line.strip()[:120] for line in self.texts_edit.toPlainText().splitlines() if line.strip()]
        self.config.set("scale", float(self.scale_combo.currentData()))
        self.config.set("on_top", self.on_top_check.isChecked())
        if self.dock_icon_check is not None:
            self.config.set("show_dock_icon", self.dock_icon_check.isChecked())
        self.config.set("no_move", self.no_move_check.isChecked())
        self.config.set("lock_position", self.lock_position_check.isChecked())
        self.config.set("shift_drag", self.shift_drag_check.isChecked())
        self.config.set("pet_opacity", int(self.pet_opacity_spin.value()))
        self.config.set("click_show_self_talk", self.click_self_talk_check.isChecked())
        self.config.set("music_sing_enabled", self.music_sing_check.isChecked())
        self.config.set("system_notifications_enabled", self.system_notifications_check.isChecked())
        if self.auto_hide_fullscreen_check is not None:
            self.config.set("auto_hide_fullscreen", self.auto_hide_fullscreen_check.isChecked())
        self.config.set("playback_speed", float(self.speed_select.currentData()))
        self.config.set("animation_gap_seconds", self.gap_spin.value())
        self.config.set("self_talk_enabled", self.self_talk_check.isChecked())
        self.config.set("self_talk_bubble_style", self.bubble_style_select.currentData())
        self.config.set("self_talk_min_interval", minimum)
        self.config.set("self_talk_max_interval", maximum)
        self.config.set("self_talk_duration_seconds", self.self_talk_duration_spin.value())
        self.config.set("self_talk_texts", texts or list(DEFAULT_SELF_TALK_TEXTS))
        self.config.set("self_talk_image_dir", self.self_talk_image_dir_picker.text())
        # Agent 联动：自定义 thinking 文案与音效（合并写回，不覆盖 agent_link 其他开关）
        agent_cfg = dict(self.config.get("agent_link", {}))
        agent_cfg["thinking_texts"] = {
            key: edit.text().strip()
            for key, edit in self.thinking_text_edits.items()
            if edit.text().strip()
        }
        agent_cfg.pop("thinking_text", None)  # 旧的全局字段已迁移到 thinking_texts

        self.config.set("agent_link", agent_cfg)
        self.config.set("context_menu_appearance", {
            "theme": self.menu_theme_select.currentData(),
            "density": self.menu_density_select.currentData(),
            "corner_radius": self.menu_radius_select.currentData(),
            "ui_font": self.menu_font_select.currentData(),
            "ui_font_size": self.menu_font_size_select.currentData(),
            "translucent": self.menu_translucent_check.isChecked(),
            "opacity": self.menu_opacity_spin.value(),
            "light_background": self.light_background_picker.text(),
            "light_foreground": self.light_foreground_picker.text(),
            "light_hover": self.light_hover_picker.text(),
            "dark_background": self.dark_background_picker.text(),
            "dark_foreground": self.dark_foreground_picker.text(),
            "dark_hover": self.dark_hover_picker.text(),
        })
        self.config.set("menu_easter_egg", {
            "enabled": self.egg_enabled_check.isChecked(),
            "title": self.egg_title_edit.text(),
            "hint": self.egg_hint_edit.text(),
            # 内置 assets 内的路径归一化回相对值，保持 portable（目录移动/自更新后仍可用）
            "avatar": store_fun_asset(self.egg_avatar_picker.text(), oijingjing_image_path()),
            "image_dir": store_fun_asset(self.egg_image_dir_picker.text(), oijingjing_image_path().parent),
        })
        self.config.set("quick_launch_apps", self.quick_launch_editor.apps())
        self.config.set("quick_websites", self.website_links_editor.websites())
        self.config.set("smart_edge_turn", self.smart_edge_turn_check.isChecked())
        self.config.set("smooth_wander", self.smooth_wander_check.isChecked())
        self.config.set("autostart_wanted", self.autostart_check.isChecked())
        ok = self.config.save()
        if not ok:
            QMessageBox.warning(
                self,
                "保存失败",
                "配置未能写入磁盘，改动可能在重启后丢失。\n\n配置路径："
                + str(self.config.path),
            )
        return ok

    def _sync_quick_launch_apps(self) -> None:
        self.config.set("quick_launch_apps", self.quick_launch_editor.apps())
        self.config.save()

    def _sync_quick_websites(self) -> None:
        self.config.set("quick_websites", self.website_links_editor.websites())
        self.config.save()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        """直接关闭（X / Esc）时同样落盘，避免修改丢失。

        设置项都是即时型偏好，与右键菜单/托盘修改的写入时机保持一致；
        已走「保存并退出」则跳过（防重复写入）。
        """
        if not getattr(self, "_saved_via_button", False):
            try:
                self._write_config()
                self._apply_autostart()
            except Exception:
                logging.exception("关闭设置时保存配置失败")
        super().closeEvent(event)
def _system_dark() -> bool:
    """按系统调色板判断深色模式（QSS 的 color 不自动级联到子控件，
    深色系统下未显式设 color 的控件会落到 palette 白字，白底上看不清）。"""
    from PySide6.QtGui import QGuiApplication
    return QGuiApplication.palette().window().color().lightness() < 128


# 深色系统的覆盖段：追加在浅色 QSS 之后（后写规则优先）
_DARK_OVERRIDE = """
QDialog { background: #202024; color: #e4e4e9; }
QFrame#sidebarPane { background: #26262b; border-right: 1px solid #34343a; }
QStackedWidget { background: #202024; }
QLineEdit#settingsSearch { background: #2e2e35; color: #e4e4e9; }
QPushButton#saveAndExit { color: #e4e4e9; }
QPushButton#saveAndExit:hover { background: #33333c; }
QListWidget#settingsSidebar::item { color: #b8b8c0; }
QListWidget#settingsSidebar::item:hover { background: #2e2e36; color: #f0f0f5; }
QListWidget#settingsSidebar::item:selected { background: #3a3a46; color: #ffffff; }
QLabel#pageTitle { color: #f0f0f5; }
QLabel#sectionTitle { color: #d8d8e0; }
QFrame#settingsCard { background: #2a2a30; border: 1px solid #3a3a42; }
QFrame#cardSeparator { background: #33333a; }
QLabel#settingLabel { color: #e0e0e6; }
QLabel#settingHint { color: #9a9aa3; }
QLabel#settingLabel:disabled, QLabel#settingHint:disabled { color: #66666e; }
SettingRow[searchMatch="true"] { background: #2c3a4e; }
QListWidget#quickLaunchList { background: #26262c; border: 1px solid #3c3c44; }
QListWidget#quickLaunchList::item:selected { background: #3a3a46; color: #ffffff; }
QPushButton { background: #3a3a42; border: 1px solid #4a4a54; color: #e4e4e9; }
QPushButton:hover { background: #44444e; }
QToolButton { color: #e4e4e9; }
QCheckBox, QRadioButton, QComboBox, QListWidget, QTreeWidget, QTableView { color: #e4e4e9; }
"""

_DARK_BROWSER_OVERRIDE = """
QLineEdit, QSpinBox, QDoubleSpinBox, QPlainTextEdit {
    background: #2e2e35; color: #e4e4e9; border: 1px solid #45454f;
}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QPlainTextEdit:hover { border-color: #56565f; }
QSpinBox::up-button, QDoubleSpinBox::up-button { border-left: 1px solid #45454f; border-bottom: 1px solid #45454f; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #55555e; }
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover { background: #6a6a74; }
"""

_DARK_POPUP_OVERRIDE = """
QMenu#ModernSelectPopup { background: #2a2a30; color: #e4e4e9; border: 1px solid #45454f; }
QMenu#ModernSelectPopup::item { color: #e4e4e9; }
QMenu#ModernSelectPopup::item:selected { background: #3a3a46; }
"""


def _settings_stylesheet() -> str:
    """浅色基础 QSS + 显式控件文字色补丁；深色系统时追加深色覆盖段。"""
    light_patch = """
        QPushButton { color: #202020; }
        QToolButton { color: #202020; }
        QCheckBox, QRadioButton, QComboBox, QListWidget, QTreeWidget, QTableView { color: #202020; }
    """
    base = _LIGHT_SETTINGS_STYLESHEET + light_patch
    if not _system_dark():
        return base + BROWSER_CONTROL_STYLESHEET
    return base + _DARK_OVERRIDE + BROWSER_CONTROL_STYLESHEET + _DARK_BROWSER_OVERRIDE


_LIGHT_SETTINGS_STYLESHEET = """
QDialog {
    background: #fcfcfd;
    color: #202020;
    font-family: "SF Pro Text", ".AppleSystemUIFont", "PingFang SC", "Segoe UI", sans-serif;
    font-size: 13px;
}
QFrame#sidebarPane {
    background: #f7f7f8;
    border: none;
    border-right: 1px solid #e3e5e8;
}
QStackedWidget { background: #fcfcfd; }
QLineEdit#settingsSearch {
    min-height: 30px;
    padding: 0 8px;
    background: #f0f1f3;
    border: 1px solid transparent;
    border-radius: 15px;
    color: #202020;
}
QLineEdit#settingsSearch:focus {
    border: 2px solid #0a84ff;
    padding: 0 7px;
}
QPushButton#saveAndExit {
    min-height: 28px;
    padding: 2px 8px;
    text-align: left;
    background: transparent;
    border: none;
    border-radius: 8px;
    font-weight: 500;
}
QPushButton#saveAndExit:hover { background: #e9eaec; }
QLabel#searchStatus {
    padding: 0 5px;
    color: #777b80;
    font-size: 11px;
}
QListWidget#settingsSidebar {
    background: transparent;
    border: none;
    outline: none;
    font-size: 13px;
    font-weight: 500;
}
QListWidget#settingsSidebar::item {
    min-height: 26px;
    padding: 4px 10px;
    border-radius: 9px;
    color: #4e4e4e;
}
QListWidget#settingsSidebar::item:hover {
    background: #eceef1;
    color: #202020;
}
QListWidget#settingsSidebar::item:selected {
    background: #e3e5e8;
    color: #171717;
}
QLabel#pageTitle {
    font-size: 26px;
    font-weight: 600;
    color: #171717;
}
QLabel#sectionTitle {
    font-size: 14px;
    font-weight: 600;
    color: #2b2b2b;
}
QFrame#settingsCard {
    background: #ffffff;
    border: 1px solid #e2e4e8;
    border-radius: 11px;
}
QFrame#cardSeparator {
    background: #eceef1;
    border: none;
    margin-left: 14px;
    margin-right: 14px;
}
QLabel#settingLabel {
    font-size: 14px;
    font-weight: 600;
    color: #252525;
}
QLabel#settingHint {
    font-size: 12px;
    font-weight: 400;
    color: #777777;
}
QLabel#settingLabel:disabled, QLabel#settingHint:disabled { color: #a6a8ac; }
SettingRow[searchMatch="true"] {
    background: #eaf3ff;
    border-radius: 8px;
}
QScrollArea#settingsScroll, QScrollArea#settingsScroll > QWidget > QWidget {
    background: transparent;
}
QListWidget#quickLaunchList {
    background: #fbfbfb;
    border: 1px solid #d9d9d9;
    border-radius: 8px;
    outline: none;
    padding: 3px;
}
QListWidget#quickLaunchList::item {
    min-height: 30px;
    padding: 3px 7px;
    border-radius: 6px;
}
QListWidget#quickLaunchList::item:selected { background: #e8e8e8; color: #202020; }
QPushButton {
    min-height: 26px;
    padding: 1px 12px;
    background: #ffffff;
    border: 1px solid #d0d0d0;
    border-radius: 7px;
    font-weight: 500;
}
QPushButton:hover { background: #f0f0f0; }
"""
