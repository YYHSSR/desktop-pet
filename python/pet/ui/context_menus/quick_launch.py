# -*- coding: utf-8 -*-
"""Configurable application shortcuts used only by the modern menu."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QFileInfo, QRectF, QProcess, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPainter, QPixmap, QRegion
from PySide6.QtWidgets import QFileIconProvider, QMenu

from pet.infrastructure.config import DEFAULT_QUICK_LAUNCH_APPS, DEFAULT_QUICK_WEBSITES
from pet.ui.context_menus.icons import vector_menu_icon
from pet.ui.context_menus.shared import add_submenu, connect_action


def configured_quick_apps(config) -> list[dict]:
    value = config.get("quick_launch_apps", DEFAULT_QUICK_LAUNCH_APPS)
    return [dict(item) for item in value if isinstance(item, dict)]


# 首次 QFileIconProvider 取应用图标可能较慢；按 (kind, path) 缓存结果
_QUICK_ICON_CACHE: dict[tuple[str, str], QIcon] = {}


def quick_app_icon(menu: QMenu, item: dict) -> QIcon:
    kind = str(item.get("kind") or "")
    path = str(item.get("path") or "")
    cache_key = (kind, path)
    cached = _QUICK_ICON_CACHE.get(cache_key)
    if cached is not None:
        return cached
    if kind == "default_browser":
        icon = vector_menu_icon(menu, "web")
    else:
        icon = QFileIconProvider().icon(QFileInfo(path)) if path else QIcon()
        icon = fitted_application_icon(icon, 18, menu) if not icon.isNull() else vector_menu_icon(menu, "application")
    _QUICK_ICON_CACHE[cache_key] = icon
    return icon


def fitted_application_icon(icon: QIcon, size: int, widget) -> QIcon:
    """Crop provider padding and fill the requested logical icon canvas."""
    if icon.isNull():
        return icon
    dpr = max(1.0, widget.devicePixelRatioF())
    source_size = max(32, round(size * dpr * 2))
    source = icon.pixmap(source_size, source_size)
    source.setDevicePixelRatio(1.0)
    bounds = QRegion(source.mask()).boundingRect()
    if bounds.isEmpty():
        return icon
    canvas = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
    canvas.setDevicePixelRatio(dpr)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.drawPixmap(
        QRectF(0.5, 0.5, size - 1.0, size - 1.0),
        source,
        QRectF(bounds),
    )
    painter.end()
    return QIcon(canvas)


def launch_quick_app(item: dict) -> bool:
    if item.get("kind") == "default_browser":
        return bool(QDesktopServices.openUrl(QUrl("https://www.google.com/")))
    path = os.path.abspath(os.path.expanduser(str(item.get("path") or "")))
    if not path:
        return False
    try:
        if sys.platform == "darwin":
            return bool(QProcess.startDetached("open", [path]))
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
            return True
        if os.path.isdir(path):
            return bool(QProcess.startDetached("xdg-open", [path]))
        return bool(QProcess.startDetached(path, []))
    except Exception as exc:
        import logging
        logging.warning("启动快捷应用失败: %s (%s)", path, exc)
        return False


def add_app_launch_menu(menu: QMenu, pet) -> QMenu:
    cfg = getattr(pet, "cfg", None)
    apps = configured_quick_apps(cfg) if cfg is not None else []
    submenu = add_submenu(menu, "启动应用", "application")
    if apps:
        for item in apps:
            action = submenu.addAction(quick_app_icon(submenu, item), str(item.get("name") or "应用"))
            action.setProperty("closeOnTrigger", True)
            connect_action(action, lambda item=dict(item): launch_quick_app(item))
    else:
        hint_action = submenu.addAction(vector_menu_icon(submenu, "settings"), "去设置中添加应用...")
        hint_action.setProperty("closeOnTrigger", True)
        settings_cb = getattr(pet, "on_open_modern_settings", None)
        if callable(settings_cb):
            connect_action(hint_action, settings_cb)
    return submenu


# 向后兼容别名
add_agent_launch_menu = add_app_launch_menu
add_quick_launch_menu = add_app_launch_menu


def configured_quick_websites(config) -> list[dict]:
    if config is None:
        return [dict(item) for item in DEFAULT_QUICK_WEBSITES]
    value = config.get("quick_websites", None)
    if value is None:
        value = DEFAULT_QUICK_WEBSITES
    return [dict(item) for item in value if isinstance(item, dict)]


def open_quick_website(item: dict) -> bool:
    url = str(item.get("url") or "").strip()
    if not url:
        return False
    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file://")):
        url = "https://" + url
    return bool(QDesktopServices.openUrl(QUrl(url)))


def add_quick_websites_menu(menu: QMenu, pet, *, icons: bool = True) -> QMenu:
    cfg = getattr(pet, "cfg", None)
    websites = configured_quick_websites(cfg)
    submenu = add_submenu(menu, "快捷网址", "web" if icons else None)
    if websites:
        for item in websites:
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "").strip()
            display_text = name if name else url
            action = submenu.addAction(vector_menu_icon(submenu, "web") if icons else QIcon(), display_text)
            action.setToolTip(url)
            action.setProperty("closeOnTrigger", True)
            connect_action(action, lambda item=dict(item): open_quick_website(item))
    else:
        hint_action = submenu.addAction(vector_menu_icon(submenu, "settings") if icons else QIcon(), "去设置中添加网址...")
        hint_action.setProperty("closeOnTrigger", True)
        settings_cb = getattr(pet, "on_open_modern_settings", None)
        if callable(settings_cb):
            connect_action(hint_action, settings_cb)
    return submenu


# 向后兼容别名
add_web_links_menu = add_quick_websites_menu

