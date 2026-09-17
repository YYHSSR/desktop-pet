# -*- coding: utf-8 -*-
"""
应用入口 —— QApplication + 桌宠窗口 + 系统托盘。

支持运行时切换角色：
- 右键桌宠 →「切换角色」
- 托盘菜单 →「切换角色」
切换后会热加载对应形象的 webm，并保留位置/朝向等配置。
"""

from __future__ import annotations

from pet.infrastructure.desktop_platform import _mac_set_dock_icon_visible, _default_xcb_platform_on_wayland

from pet.infrastructure.paths import resource_root

import json
import logging
import os
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from pet.infrastructure import autostart as autostart_mod
from pet.infrastructure import catalog
from pet.infrastructure.config import APP_DIR_NAME, Config
from pet.ui.desktop_notify import DesktopNotification, position_stack
from pet.media.library import MovieLibrary
from pet.ui.window import PetWindow
from pet.ui.fun_image_popup import restore_ojingjing_windows
from pet.infrastructure.runtime_cleanup import cleanup_stale_runtime_dirs


def _setup_logging(config: Config) -> None:
    config.dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        handlers=[RotatingFileHandler(
            str(config.dir / f'pet-{os.getpid()}.log'),  # 多开实例日志按 PID 隔离，避免互相覆盖
            maxBytes=1_000_000, backupCount=2, encoding='utf-8',
        )],  # 滚动日志：1MB×2，不再无限增长
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        encoding='utf-8',
    )


def _show_startup_error(title: str, message: str) -> None:
    QMessageBox.critical(None, title, message)


def _cleanup_stale_runtime_dirs() -> None:
    """清理 PyInstaller onefile 遗留的 ``_MEI*`` 临时目录。

    只扫描系统临时目录中超过 24 小时的目录，并始终跳过当前进程的
    ``sys._MEIPASS``。删除失败只记录日志，不接管 ACL，也不影响启动。
    """
    if not getattr(sys, "frozen", False):
        return
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return

    current = Path(meipass).resolve(strict=False)
    result = cleanup_stale_runtime_dirs(current_dir=current)
    for directory in result.removed:
        logging.info("已清理遗留 PyInstaller 缓存目录: %s", directory)
    for directory, error in result.failed.items():
        logging.warning("清理 PyInstaller 缓存目录失败: %s (%s)", directory, error)

class PetApp:
    """管理桌宠窗口、托盘与角色热切换。"""

    def __init__(self, app: QApplication, config: Config, enable_chat: bool = False) -> None:
        self.app = app
        self.config = config
        self.win: PetWindow | None = None
        self.tray: QSystemTrayIcon | None = None
        self._notification_click_callback = None
        self._toast_windows: list[DesktopNotification] = []
        self.modern_settings_dialog = None
        self._pending_dialog_opens: set[str] = set()
        self._on_about_to_quit_connected = False

    # ------------------------------------------------------------ 启动
    def start(self) -> None:
        # aboutToQuit 只在控制器层绑定一次：角色热切换会重建窗口，逐个
        # connect win._save_position 会在旧窗口延迟销毁后残留失效引用。
        # 统一走 _on_about_to_quit，在信号触发时读取当前有效窗口。
        if not self._on_about_to_quit_connected:
            self.app.aboutToQuit.connect(self._on_about_to_quit)
            self._on_about_to_quit_connected = True
        character_id = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        logging.info('当前形象: %s', character_id)
        self._create_ui(character_id)
        QTimer.singleShot(3500, self._check_autostart_wanted)

    def _on_about_to_quit(self) -> None:
        """退出前保存当前有效窗口的位置并释放资源。

        aboutToQuit 只绑定一次自本控制器；切换角色会重建桌宠窗口，信号
        触发时读取当前窗口（self.win），避免调用已延迟销毁的旧窗口。
        """
        if self.win is not None:
            self.win._save_position()

    def _set_autostart(self, enabled: bool, win=None) -> bool:
        ok = autostart_mod.set_enabled(bool(enabled))
        self.config.set("autostart_wanted", bool(enabled))
        self.config.save()
        target = win or self.win
        if target is not None and not ok:
            target.show_bubble("开机自启写入失败，请检查系统登录项或安全软件设置。", duration_ms=6000)
        return ok

    def _check_autostart_wanted(self) -> None:
        if self.config.get("autostart_wanted", False) and not autostart_mod.is_enabled() and self.win is not None:
            self.win.show_bubble("检测到开机自启已被系统或安全软件关闭，可在设置中重新启用。", duration_ms=7000)



    def _create_library(self, character_id: str) -> MovieLibrary:
        lib = MovieLibrary(character_id=character_id)
        # UI 就绪后统一调度预热：高优先级立即后台跑（带 0~0.5s 错峰），
        # 随机动作池延迟 2s 补全，避免多开启动时 ffmpeg 进程洪峰。
        lib.schedule_high_priority_warm()
        lib.schedule_low_priority_warm()
        logging.info('素材加载完成：%s %d 段动画', character_id, len(lib.names()))
        return lib

    def _bind_window_actions(self, win: PetWindow) -> None:
        win.on_switch_character = self.switch_character
        win.on_open_quick_chat = None
        win.on_system_notify = self.system_notify
        win.on_open_legacy_settings = None
        win.on_open_modern_settings = self.open_modern_settings
        win.on_restore_fun_windows = restore_ojingjing_windows
        win.on_hidden = self._notify_pet_hidden

    def _create_ui(self, character_id: str) -> None:
        lib = self._create_library(character_id)
        win = PetWindow(lib, self.config)
        self._bind_window_actions(win)
        win.show()

        tray = self._build_tray(win)

        # 清理旧对象（热切换时使用）
        old_win = self.win
        old_tray = self.tray
        self.win = win
        self.tray = tray

        if old_win is not None:
            old_win.hide(notify=False)
            old_tray.hide() if old_tray is not None else None
            QTimer.singleShot(0, old_win.deleteLater)
            if old_tray is not None:
                QTimer.singleShot(0, old_tray.deleteLater)

    # ------------------------------------------------------------ 角色切换
    def switch_character(self, character_id: str) -> None:
        if self.win is None:
            return
        current = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        if character_id == current:
            return

        # 先保存配置，即使后续加载失败也记住用户选择
        self.config.set('character', character_id)
        self.config.save()

        try:
            # 预创建新库，失败则保留当前角色
            lib = self._create_library(character_id)
        except Exception as exc:
            logging.exception('切换角色失败: %s', character_id)
            _show_startup_error('切换角色失败', str(exc))
            return

        logging.info('切换角色: %s -> %s', current, character_id)

        # 用新库创建新窗口/托盘，旧对象延迟销毁
        old_win = self.win
        win = PetWindow(lib, self.config)
        self._bind_window_actions(win)
        win.show()

        tray = self._build_tray(win)

        old_tray = self.tray
        self.win = win
        self.tray = tray

        old_win.hide(notify=False)
        if old_tray is not None:
            old_tray.hide()
        QTimer.singleShot(0, old_win.deleteLater)





    def _defer_while_popup_active(self, key: str, callback) -> bool:
        """Avoid constructing a heavy dialog inside QMenu.exec()."""
        if QApplication.activePopupWidget() is None:
            self._pending_dialog_opens.discard(key)
            return False
        if key in self._pending_dialog_opens:
            return True
        self._pending_dialog_opens.add(key)

        def retry() -> None:
            if QApplication.activePopupWidget() is not None:
                QTimer.singleShot(50, retry)
                return
            self._pending_dialog_opens.discard(key)
            callback()

        QTimer.singleShot(50, retry)
        return True

    def _present_dialog(self, dialog, before_present=None, attempt: int = 0) -> None:
        """延迟呈现非模态窗口，直到任何弹出菜单关闭。

        macOS 的右键/托盘菜单是原生 NSMenu 跟踪会话（menu.exec 阻塞期间），
        菜单项动作触发时会话尚未结束，此时新建窗口的 show/raise/activate
        会被 AppKit 抑制——表现为首次点击「AI 设置 / 桌宠设置」无反应，
        需要再点一次（此时窗口实例已存在，直接 show 成功）。
        延迟到菜单关闭后再呈现即可稳定弹出；Qt 自绘菜单（Windows）同样
        覆盖：弹窗仍显示时重试等待。重试 60 次（约 3.6 秒）后放弃，
        防止弹窗长期不消失时无限空转。
        """
        if attempt > 60:
            return
        if QApplication.activePopupWidget() is not None:
            QTimer.singleShot(60, lambda: self._present_dialog(dialog, before_present, attempt + 1))
            return
        if before_present is not None:
            before_present()
        if dialog.isMinimized():
            dialog.showNormal()
        else:
            dialog.show()
        dialog.raise_()
        dialog.activateWindow()


    def _update_bubble_suppression_for_settings(self) -> None:
        """任一设置窗口打开时暂停桌宠气泡，避免气泡盖住设置界面。"""
        if getattr(self, "win", None) is None:
            return
        any_open = getattr(self, "modern_settings_dialog", None) is not None
        self.win.set_bubble_suppressed(any_open)

    # ------------------------------------------------------------ 托盘
    def open_modern_settings(self) -> None:
        from pet.ui.modern_settings_dialog import ModernSettingsDialog
        if self.modern_settings_dialog is None:
            dialog = ModernSettingsDialog(
                self.config,
                self.win,
            )
            dialog.finished.connect(self._modern_settings_finished)
            self.modern_settings_dialog = dialog
        self._update_bubble_suppression_for_settings()
        # 在 show 之前定位，避免 Windows 上窗口先显示默认位置再跳走（闪现小窗）
        self._present_dialog(
            self.modern_settings_dialog,
            before_present=self.modern_settings_dialog.move_away_from_pet,
        )

    def _modern_settings_finished(self, result: int) -> None:
        self.modern_settings_dialog = None
        self._update_bubble_suppression_for_settings()
        # 新版设置在关闭时一律落盘（closeEvent 自动保存，「保存并退出」同样走
        # _write_config），因此无论 Accepted/Rejected 都把改动应用到桌宠。
        # 此前只有 Accepted 才刷新：直接 X 关闭时保存生效但桌宠不更新。
        if self.win is not None:
            self.win.refresh_pet_settings()
        _mac_set_dock_icon_visible(bool(self.config.get("show_dock_icon", True)))

    def _notify_pet_hidden(self) -> None:
        """用户主动隐藏桌宠后弹托盘提示，指明恢复入口。"""
        if self.tray is None:
            return
        self.tray.showMessage(
            "桌宠已隐藏",
            "点击托盘图标或 Dock 图标即可恢复。",
            QSystemTrayIcon.MessageIcon.Information,
            4000,
        )

    def system_notify(self, title: str, message: str, *, on_click=None, duration_ms: int = 5000) -> bool:
        """Show a bottom-right desktop notification (self-drawn, tray-independent)."""
        if not self.config.get("system_notifications_enabled", True):
            return False
        self._prune_toasts()
        toast = DesktopNotification(
            str(title),
            str(message),
            on_click=on_click,
            duration_ms=int(duration_ms),
        )
        self._toast_windows.append(toast)
        toast.destroyed.connect(lambda _obj=None: self._prune_toasts())
        toast.show()
        position_stack(self._toast_windows)
        return True

    def _prune_toasts(self) -> None:
        self._toast_windows = [
            w for w in self._toast_windows
            if not (hasattr(w, "is_closed") and w.is_closed())
        ]
        position_stack(self._toast_windows)

    def _on_tray_message_clicked(self) -> None:
        callback = self._notification_click_callback
        self._notification_click_callback = None
        if callable(callback):
            try:
                callback()
            except Exception:
                logging.exception("系统通知点击回调执行失败")

    def _build_tray(self, win: PetWindow) -> QSystemTrayIcon:
        icon_pm = win.icon_pixmap()
        if icon_pm is None or icon_pm.isNull():
            ico_path = resource_root() / "assets" / "icon.ico"
            icon = QIcon(str(ico_path)) if ico_path.is_file() else QIcon()
        else:
            icon = QIcon(icon_pm)
        tray = QSystemTrayIcon(icon)

        def toggle_visible() -> None:
            if win.isVisible():
                win.hide()
            else:
                win.show()

        menu = QMenu()
        # 气泡是置顶 Tool 窗口（层级高于原生菜单 popup），托盘菜单弹出前
        # 先隐藏气泡，避免气泡盖住菜单
        menu.aboutToShow.connect(lambda: win._speech_bubble.hide())
        menu.addAction('显示 / 隐藏', toggle_visible)

        menu.addAction('桌宠设置', self.open_modern_settings)

        m_char = menu.addMenu('切换角色')
        current = str(self.config.get('character', catalog.DEFAULT_CHARACTER))
        for cid in catalog.list_available_characters():
            act = m_char.addAction(cid)
            act.setCheckable(True)
            act.setChecked(cid == current)
            act.triggered.connect(lambda checked=False, cid=cid: self.switch_character(cid))

        menu.addSeparator()

        auto = menu.addAction('开机自启')
        auto.setCheckable(True)
        auto.setChecked(autostart_mod.is_enabled())
        auto.toggled.connect(lambda enabled: self._set_autostart(enabled, win))

        def sync_tray_checks() -> None:
            # 设置对话框/右键菜单里改过的开关，弹出托盘菜单前同步复选状态
            #（托盘菜单在 _build_tray 时一次性构建，不复用则不刷新会过期）
            auto.setChecked(autostart_mod.is_enabled())

        menu.aboutToShow.connect(sync_tray_checks)

        menu.addSeparator()
        menu.addAction('退出', self.app.quit)

        tray.setContextMenu(menu)
        tray.setToolTip('desktop-pet 桌宠')
        tray.messageClicked.connect(self._on_tray_message_clicked)
        tray.activated.connect(
            lambda reason: toggle_visible()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        tray.show()
        return tray


def main(argv: list[str] | None = None, enable_chat: bool = False, **kwargs) -> int:
    _default_xcb_platform_on_wayland()
    argv = list(argv if argv is not None else sys.argv)

    app = QApplication(argv)
    app.setApplicationName(APP_DIR_NAME)
    app.setQuitOnLastWindowClosed(False)

    config = Config()
    _mac_set_dock_icon_visible(bool(config.get("show_dock_icon", True)))
    _setup_logging(config)
    logging.info("desktop-pet 启动")
    _cleanup_stale_runtime_dirs()
    stale_removed = autostart_mod.cleanup_stale_entries()
    if stale_removed:
        logging.info("已清理 %d 个指向不存在路径的开机自启项", stale_removed)

    controller = PetApp(app, config, enable_chat=enable_chat)
    try:
        controller.start()
    except Exception as exc:
        logging.exception("启动失败")
        _show_startup_error("desktop-pet", str(exc))
        return 1

    logging.info("进入事件循环")
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
