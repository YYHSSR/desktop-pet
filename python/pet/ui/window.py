# -*- coding: utf-8 -*-
"""
桌宠主窗口 —— 透明无边框置顶窗口 + 动画链状态机 + 移动驱动 + 交互。

状态机（动画链式模型）：
  - 每个动画一次性播放，播完按概率选下一个：30% 待机 / 10% 转向 / 40% 动作 / 20% 移动；
  - 转向（东张西望）播完翻转朝向；facing=right 时水平镜像；
  - 点击回应 / 拖拽动画播完先回待机缓冲，待机播完再进随机链；
  - 移动：动画只提供"走路姿态"（3 选 1），位置由 QTimer 驱动，
    开头/结尾各 2s 不动，中间按播放进度插值；
  - 透明区域鼠标穿透：每帧用当前帧 alpha 生成窗口 mask（等效原版命中层设计）。
"""

from __future__ import annotations

from pet.infrastructure.native_window import (
    _keep_macos_tool_window_visible,
    _mac_set_window_level,
    GWL_STYLE,
    GWL_EXSTYLE,
    _WS_CAPTION,
    _WS_EX_TOPMOST,
    _WS_EX_TRANSPARENT,
    _WinRect,
    _WinMonitorInfo,
    _set_windows_click_through,
)
from pet.infrastructure.fullscreen import _fullscreen_geometry_hit, _fs_user_busy_state, probe_foreground_fullscreen
from pet.ui.input_controller import WindowsPerPixelInputController

from pet.ui.context_menus.positioning import _clamp_menu_rect, animate_context_menu_to, pick_context_menu_position

from pet.core.geometry import _squash_geometry, wander_target_y

import ctypes
import json
import logging
import os
import math
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any

import shiboken6

from PySide6.QtCore import (
    QElapsedTimer,
    QPoint,
    QPointF,
    QRect,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QBitmap, QColor, QCursor, QImage, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QApplication, QInputDialog, QMenu, QToolTip, QWidget

from pet.infrastructure import autostart as autostart_mod
from pet.infrastructure import catalog
from pet.infrastructure.config import (
    DEFAULT_SELF_TALK_BUBBLE_STYLE,
    DEFAULT_SELF_TALK_DURATION_SECONDS,
    DEFAULT_SELF_TALK_MAX_INTERVAL,
    DEFAULT_SELF_TALK_MIN_INTERVAL,
    DEFAULT_SELF_TALK_TEXTS,
    Config,
    _float_or_default,
)
from pet.media.library import MovieLibrary
from pet.media.animation_thumbnail import decode_representative_frame, representative_frame_index
from pet.ui.speech_bubble import PetSpeechBubble, list_self_talk_images
from pet.ui.fun_image_popup import oijingjing_image_path, resolve_fun_asset
from pet.ui.context_menu import populate_context_menu as _populate_context_menu
from pet.ui.context_menus.shared import take_deferred_menu_callbacks
from pet.infrastructure.updater import QUARK_PAN_URL, REPO_URL

# 后台播放音乐时自动播放的唱歌/哼歌动画
SING_ANIM = '悠闲哼歌'


def _resolve_self_talk_image_dir(raw: str) -> str:
    """Resolve the self-talk image directory; empty keeps text-only behavior.

    用户显式配置的外部目录被删除后不再回退到内置彩蛋池（用户删目录的
    意图就是"不要再看图"），直接走纯文本；相对路径（内置 assets）保留
    回退以兼容便携包目录迁移。
    """
    raw = str(raw or '').strip()
    if not raw:
        return ''
    candidate = Path(raw).expanduser()
    if candidate.is_absolute() and not candidate.is_dir():
        return ''
    return str(resolve_fun_asset(raw, oijingjing_image_path().parent))


# 直播捕获兼容模式下窗口标题（普通顶层窗口需要可见标题，供直播姬/OBS 选择）
STREAM_CAPTURE_TITLE = 'desktop-pet 桌宠'

IDLE = "IDLE"
PRESS_CANDIDATE = "PRESS_CANDIDATE"
DRAGGING = "DRAGGING"


def build_window_flags(config):
    """构造桌宠窗口 flags。

    默认形态：FramelessWindowHint | Tool（Windows 上映射 WS_EX_TOOLWINDOW，
    不进任务栏/Alt+Tab）。
    """
    flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
    if config.get('on_top', True):
        flags |= Qt.WindowType.WindowStaysOnTopHint
    return flags


# ---- Win32：全屏判定用常量/结构 ----


def _set_speech_bubble_interactive(pet) -> None:
    """按当前是否可打开快速对话，切换气泡鼠标穿透/可点击。"""
    setter = getattr(pet._speech_bubble, "set_interactive", None)
    if callable(setter):
        setter(callable(getattr(pet, "on_open_quick_chat", None)))


class PetWindow(QWidget):
    """桌宠窗口本体。"""

    fullscreen_changed = Signal(bool)  # 全屏 watcher 线程 → 主线程（隐藏/恢复桌宠）

    def __init__(self, lib: MovieLibrary, config: Config) -> None:
        super().__init__()
        self.lib = lib
        self.cfg = config
        self.on_switch_character = None  # 由 app 注入，用于运行时切换角色
        self.on_open_quick_chat = None
        self.on_open_legacy_settings = None
        self.on_open_modern_settings = None
        self.on_restore_fun_windows = None
        self.on_hidden = None  # 由 app 注入：用户主动隐藏时弹托盘提示
        self._position_listeners = []
        self._animation_icon_image_cache: dict[str, QImage] = {}
        self._animation_icon_inflight: dict[str, threading.Event] = {}
        self._animation_icon_cache_lock = threading.Lock()

        # 根据当前形象实际拥有的动画动态计算分类，支持不同角色动作不一致
        self.cats = catalog.build_categories(lib.names(), getattr(lib, 'manifest', None), getattr(lib, 'folder_map', None), getattr(lib, 'folder_files', None))
        self.idle = self.cats['idle']
        self.turn = self.cats['turn']
        self.idles = self.cats['idles']
        self.turns = self.cats['turns']
        self.moves = self.cats['moves']
        self.clicks = self.cats['clicks']
        self.drag = self.cats['drag']
        self.acts = self.cats['acts']

        # 预载拖拽动画首帧，避免第一次进入拖拽状态时同步解码卡顿
        if self.drag:
            self.lib.movie(self.drag).jumpToFrame(0)

        self.playback_speed: float = float(config.get('playback_speed', 1.0))
        self.lock_position: bool = bool(config.get('lock_position', False))
        self.shift_drag: bool = bool(config.get('shift_drag', False))
        self.pet_opacity: int = int(_float_or_default(config.get('pet_opacity', 100), 100, 10, 100))
        self._applied_opacity: float | None = None  # 已应用到窗口的不透明度
        self.click_show_self_talk: bool = bool(config.get('click_show_self_talk', False))
        self.animation_gap_seconds: float = max(0.0, min(3600.0, float(config.get('animation_gap_seconds', 0.0))))
        self._animation_gap_active = False
        self._animation_gap_timer = QTimer(self)
        self._animation_gap_timer.setSingleShot(True)
        self._animation_gap_timer.timeout.connect(self._on_animation_gap_timeout)
        self._speech_bubble = PetSpeechBubble(
            style_id=str(config.get('self_talk_bubble_style', DEFAULT_SELF_TALK_BUBBLE_STYLE))
        )
        self._speech_bubble.clicked.connect(self._on_speech_bubble_clicked)
        self._self_talk_enabled = bool(config.get('self_talk_enabled', False))
        self._self_talk_texts = self._read_self_talk_texts(config.get('self_talk_texts'))
        self._self_talk_duration_seconds = max(
            1.0,
            min(300.0, float(config.get(
                'self_talk_duration_seconds', DEFAULT_SELF_TALK_DURATION_SECONDS
            ))),
        )
        self._self_talk_image_dir = str(config.get('self_talk_image_dir', '') or '')
        self._self_talk_images = list_self_talk_images(_resolve_self_talk_image_dir(self._self_talk_image_dir))
        self._self_talk_min_interval = max(5.0, float(config.get('self_talk_min_interval', DEFAULT_SELF_TALK_MIN_INTERVAL)))
        self._self_talk_max_interval = max(self._self_talk_min_interval, float(config.get('self_talk_max_interval', DEFAULT_SELF_TALK_MAX_INTERVAL)))
        self._self_talk_timer = QTimer(self)
        self._self_talk_timer.setSingleShot(True)
        self._self_talk_timer.timeout.connect(self._on_self_talk_timeout)
        # 后台音乐检测：默认关闭，开启后检测到系统正在输出音频就播放唱歌动画
        self._music_sing_enabled = bool(config.get('music_sing_enabled', False))
        self._music_sing_active = False
        self._music_sing_timer = QTimer(self)
        self._music_sing_timer.setInterval(4000)
        self._music_sing_timer.timeout.connect(self._check_music_sing)
        # 重要气泡（Agent 联动提醒等）占用期间，自言自语让路，
        # 避免重要提示刚出来就被自言自语顶掉。
        self._bubble_busy_until = 0.0
        # 设置窗口打开期间暂停气泡，避免置顶气泡盖住设置界面
        self._bubble_suppressed = False

        # Agent 联动动作衔接：正在播一次性动作时联动动作不打断，存为待播（最新覆盖旧的），
        # 等当前动作播完由 _on_anim_ended 自然接上；联动动作播完仍有 Agent 在忙则接下一个。
        self._pending_link_anim: str | None = None
        self._link_anim_current: str | None = None
        self._link_next_provider = None  # AgentLinkManager 注入：()->str|None

        # 多 Agent 状态感知管理器
        from pet.services.agent_link import AgentLinkManager
        self.agent_link_manager = AgentLinkManager(self, config)

        # ---- 全屏应用自动隐藏（Windows）----
        # 前台窗口覆盖整个屏幕几何（含任务栏区域）时自动隐藏桌宠，
        # 全屏退出后自动恢复。最大化窗口不覆盖任务栏，不会误触发。
        # 后台线程轮询 + 信号回主线程：QTimer 轮询在实测中多起「启动数秒后
        # 静默停发 timeout」的疑难，线程通道不受其影响；检测为纯 win32 调用。
        self.auto_hide_fullscreen: bool = bool(config.get('auto_hide_fullscreen', True))
        self._auto_hidden = False  # 只恢复"由本 watcher 隐藏"的状态，尊重手动隐藏
        self._fs_stop = threading.Event()
        self._fs_thread: threading.Thread | None = None
        self._fs_last = False
        self.fullscreen_changed.connect(self._on_fullscreen_changed)

        # ---- 窗口属性：无边框 + 透明 + 不进任务栏；置顶可配置 ----
        flags = build_window_flags(config)
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Cocoa hides Tool windows when an accessory application deactivates.
        # Visibility and z-order are separate: always keep the pet visible,
        # then use WindowStaysOnTopHint/NSWindow level for the on-top setting.
        _keep_macos_tool_window_visible(self)
        app = QApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._on_application_state_changed)

        # ---- 状态 ----
        self.anim: str = self.idle
        self.facing: str = config.get('facing', 'left')  # left | right
        self.scale: float = float(config.get('scale', catalog.DEFAULT_SCALE))
        self.no_move: bool = bool(config.get('no_move', False))  # 不移动：禁用自动移动
        self.movie = None
        self._frame_pixmap: QPixmap | None = None
        # 角色可见轮廓（窗口局部坐标）与逐像素命中缓存；贴边功能复用 _mask_bounds
        self._mask_bounds: QRect | None = None
        self._hit_alpha_image: QImage | None = None
        self._input_controller: WindowsPerPixelInputController | None = None
        if os.name == "nt":
            self._input_controller = WindowsPerPixelInputController(self)
        # 窗口隐藏时暂停动画解码/定时器；显示时由 showEvent 恢复
        self._hidden_paused = False
        self._ended_fired = False

        # ---- 交互状态 ----
        self._press_global: QPoint | None = None
        self._grab_offset: QPoint | None = None  # 按下时 鼠标全局坐标 - 窗口左上角
        self._dragging = False
        self._just_dragged = False               # 抑制拖拽结束后的幽灵点击
        self._interaction_state = "IDLE"
        self._context_menu_suppressed = False
        # ---- 移动驱动 ----
        self._move_plan: dict | None = None
        self._move_timer = QTimer(self)
        self._move_timer.setInterval(33)         # ~30fps 位置插值
        self._move_timer.timeout.connect(self._on_move_tick)

        # ---- 点击 Q 弹效果 ----
        self._squash_timer = QTimer(self)
        self._squash_timer.setInterval(16)
        self._squash_timer.timeout.connect(self._on_squash_tick)
        self._squash_clock = QElapsedTimer()
        self._squash_active = False
        self._squash_duration_ms = 220
        self._squash_progress = 1.0

        # ---- 尺寸与初始状态 ----
        self._apply_scale()
        # 懒加载：不再预先连接全部 91 个 clip 的信号；
        # 实际播放某个动画时由 _switch -> _connect_movie 按需连接。
        self._connected_movies: set[str] = set()

        # 副屏位置恢复：开机自启时副屏可能还没就绪（显示器唤醒慢于自启），
        # 记录的目标屏此刻枚举不到 → 先落主屏，然后等它上线再自动恢复。
        # 等待方式 = 5s 轮询（兜底，覆盖"信号已发但屏尚未进枚举"的竞态）
        #         + screenAdded 即时触发（常规路径秒回）。
        # 用户真正开始拖动/点「回到右下角」立即撤防（尊重手动选择），2 分钟超时撤防。
        self._awaiting_saved_screen: str | None = None
        self._screen_restore_armed = False
        self._screen_retry_deadline = 0.0
        self._screen_retry_timer = QTimer(self)
        self._screen_retry_timer.setInterval(5000)
        self._screen_retry_timer.timeout.connect(self._screen_retry_tick)

        self._restore_position()
        self._switch(self.idle)
        self._music_sing_timer.start()
        self._schedule_self_talk()
        if self._watch_required():
            self._start_fs_watch()

        if self._awaiting_saved_screen:
            self._arm_screen_restore_retry()

    def _arm_screen_restore_retry(self) -> None:
        """目标副屏暂未就绪：启动 5s 轮询 + screenAdded 监听，等它上线。"""
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is None:
            return
        self._screen_retry_deadline = time.monotonic() + 120.0
        if not self._screen_restore_armed:
            app.screenAdded.connect(self._screen_retry_tick)
            self._screen_restore_armed = True
            logging.debug('已监听屏幕变化，等待 %s 上线', self._awaiting_saved_screen)
        self._screen_retry_timer.start()  # start() 即重启，超时窗口随之刷新

    def _disarm_screen_restore_retry(self) -> None:
        self._awaiting_saved_screen = None
        if hasattr(self, '_screen_retry_timer'):
            self._screen_retry_timer.stop()
        if not self._screen_restore_armed:
            return
        self._screen_restore_armed = False
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is not None:
            try:
                app.screenAdded.disconnect(self._screen_retry_tick)
            except (RuntimeError, TypeError):
                pass

    def _screen_retry_tick(self, *_args) -> None:
        """轮询/screenAdded 共用入口：目标屏一旦进入枚举立即恢复位置。"""
        target = self._awaiting_saved_screen
        if not target:
            self._disarm_screen_restore_retry()
            return
        if time.monotonic() > self._screen_retry_deadline:
            logging.info('等待屏幕 %s 超时（120s），放弃自动恢复', target)
            self._disarm_screen_restore_retry()
            return
        # _screen_available 找不到目标屏时回退当前屏（名字不匹配），找到才算上线
        scr = self._screen_available(target)
        if scr is not None and scr.name() == target:
            self._disarm_screen_restore_retry()
            self._restore_position()
            logging.info('目标屏幕 %s 上线，已恢复到保存位置', target)

    def _on_screen_added_restore(self, screen) -> None:
        """兼容入口：新屏幕上线 → 立即触发一次检查。"""
        self._screen_retry_tick()

    # ================================================================ 尺寸
    def _apply_scale(self) -> None:
        """按缩放计算窗口尺寸：宽度 220×scale，高度 (124+落地偏移)×scale。"""
        self._w = max(1, int(round(catalog.CANVAS_W * self.scale)))
        self._h = max(1, int(round((catalog.CANVAS_H + catalog.PAD) * self.scale)))
        self.setFixedSize(self._w, self._h)

    def change_scale(self, scale: float) -> None:
        """切换缩放；保持窗口底边不动（脚踩的地面不变）。"""
        if abs(scale - self.scale) < 1e-6:
            return
        old_bottom = self.geometry().bottom()
        self.scale = scale
        self._apply_scale()
        self.move(self.x(), old_bottom - self._h + 1)
        self._rebuild_frame()
        if self._speech_bubble.isVisible():
            self._speech_bubble.reflow(
                self.visible_content_rect(), pet_scale=self.scale
            )
        self.update()
        self._save_position()

    # ================================================================ 位置
    def _screen_available(self, screen_name: str | None = None):
        """返回指定或窗口所在屏幕；macOS 上 self.screen() 失效时兜底主屏。"""
        from PySide6.QtGui import QGuiApplication
        if screen_name:
            for screen in QGuiApplication.screens():
                if screen.name() == screen_name:
                    return screen
        scr = self.screen()
        if scr is None:
            scr = QGuiApplication.primaryScreen()
        return scr

    def add_position_listener(self, listener) -> None:
        if callable(listener) and listener not in self._position_listeners:
            self._position_listeners.append(listener)

    def remove_position_listener(self, listener) -> None:
        try:
            self._position_listeners.remove(listener)
        except ValueError:
            pass

    def visible_content_rect(self) -> QRect:
        """Return the current visible character bounds in global coordinates.

        The pet window includes a transparent canvas and landing padding. The
        alpha mask is the source of truth for the actual visible character, so
        other windows can be placed beside the character instead of beside the
        transparent canvas.
        """
        frame_rect = self.frameGeometry()
        local_rect = self.character_local_region()
        if not local_rect.isEmpty():
            return QRect(frame_rect.topLeft() + local_rect.topLeft(), local_rect.size())
        mask = self.mask()
        if not mask.isEmpty():
            local_rect = mask.boundingRect()
            if not local_rect.isEmpty():
                return QRect(frame_rect.topLeft() + local_rect.topLeft(), local_rect.size())
        return frame_rect

    def _restore_position(self) -> None:
        """恢复上次位置（按屏幕比例），无记录则落右下角。
        保存位置时所在的屏幕此刻不在线（如开机自启时副屏未就绪）→
        落当前屏并记下目标屏，由 screenAdded 监听在它上线后重新恢复。"""
        saved_screen = self.cfg.get('screen_name')
        scr = self._screen_available(saved_screen)
        if saved_screen and scr.name() != saved_screen:
            self._awaiting_saved_screen = saved_screen
            logging.info('目标屏幕 %s 暂不在线，先落在 %s，等它上线后自动恢复',
                         saved_screen, scr.name())
        else:
            self._awaiting_saved_screen = None
        avail = scr.availableGeometry()
        rx, ry = self.cfg.get('rx'), self.cfg.get('ry')
        if rx is None or ry is None:
            x = avail.right() - self._w - catalog.CORNER_MARGIN
            y = avail.bottom() - self._h
        else:
            x = int(round(avail.left() + rx * avail.width())) - self._w // 2
            y = int(round(avail.top() + ry * avail.height())) - self._h // 2
            x = min(max(x, avail.left()), avail.right() - self._w)
            y = min(max(y, avail.top()), avail.bottom() - self._h)
        logging.info('恢复位置 screen=%s avail=(%d,%d,%d,%d) dpr=%s -> (%d,%d)',
                     scr.name(), avail.left(), avail.top(), avail.right(),
                     avail.bottom(), scr.devicePixelRatio(), x, y)
        self.move(x, y)

    def _save_position(self) -> None:
        """以"窗口中心相对屏幕可用区的比例"持久化位置（分辨率变化后仍正确）。
        等待目标副屏上线期间（_awaiting_saved_screen 非空）不写位置/屏名：
        当前只是临时落脚主屏，写回会把保存的副屏坐标永久覆盖。"""
        scr = self._screen_available()
        avail = scr.availableGeometry()
        if avail.width() <= 0 or avail.height() <= 0:
            return
        if not getattr(self, '_awaiting_saved_screen', None):
            cx = self.x() + self._w / 2
            cy = self.y() + self._h / 2
            self.cfg.set('rx', (cx - avail.left()) / avail.width())
            self.cfg.set('ry', (cy - avail.top()) / avail.height())
            self.cfg.set('screen_name', scr.name())
        self.cfg.set('facing', self.facing)
        self.cfg.set('scale', self.scale)
        self.cfg.save()

    def _go_default_corner(self) -> None:
        # 用户明确要求回右下角 = 手动位置决策，撤销"等副屏上线自动恢复"
        _disarm = getattr(self, '_disarm_screen_restore_retry', None)
        if callable(_disarm):
            _disarm()
        # Position can still be written by the animation interpolation timer
        # after a direct move. Stop it first, otherwise the pet briefly reaches
        # the corner and is immediately snapped back.
        self._cancel_move()
        scr = self._screen_available()
        avail = scr.availableGeometry()
        x = avail.right() - self._w - catalog.CORNER_MARGIN
        y = avail.bottom() - self._h
        logging.info('回到右下角 screen=%s avail=(%d,%d,%d,%d) dpr=%s -> (%d,%d)',
                     scr.name(), avail.left(), avail.top(), avail.right(),
                     avail.bottom(), scr.devicePixelRatio(), x, y)
        self.move(x, y)
        self._save_position()

    def _schedule_macos_window_level(self, on: bool) -> None:
        if sys.platform != 'darwin':
            return
        level = 3 if on else 0

        def apply_current_native_window() -> None:
            _mac_set_window_level(int(self.winId()), level)

        # Apply immediately, then again after Qt/Cocoa have processed the
        # native-window recreation and ordering events. winId is deliberately
        # resolved inside every callback so a stale NSView is never reused.
        apply_current_native_window()
        for delay in (0, 40, 160):
            QTimer.singleShot(delay, self, apply_current_native_window)

    def set_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, on)
        self.cfg.set('on_top', on)
        self.cfg.save()
        self.show()
        self._schedule_macos_window_level(on)
        if on:
            self.raise_()

    def _restore_on_top_after_context_menu(self) -> None:
        """Reassert the native floating level after menus/app activation changes."""
        if not bool(self.cfg.get('on_top', True)):
            return
        _keep_macos_tool_window_visible(self)
        self._schedule_macos_window_level(True)

    def _on_application_state_changed(self, _state) -> None:
        # Opening a native menu and then clicking another application can make
        # Cocoa reorder its owner Tool window. Reapply the level after the
        # activation transition without activating or stealing keyboard focus.
        QTimer.singleShot(0, self, self._restore_on_top_after_context_menu)

    def showEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        """窗口显示时校正层级（延迟执行，避免被 Qt 窗口重建覆盖）。"""
        super().showEvent(event)
        self._schedule_macos_window_level(bool(self.cfg.get('on_top', True)))
        self._apply_opacity()
        # 隐藏期暂停的活动在此恢复（与 hide() 中的 _pause_activity 配对）
        if self._hidden_paused:
            self._hidden_paused = False
            self._resume_activity()
        self._restore_dock_icon_preference()

    def hide(self, *, notify: bool = True) -> None:
        """隐藏桌宠。

        macOS 同步打开 Dock 图标；notify=False 供角色切换等内部替换使用
        （不弹托盘提示、不 arm Dock 点击恢复监听）。
        隐藏即暂停动画解码与全部活动定时器（低功耗：不可见就零消耗）。
        """
        self._ensure_dock_icon_on_hide()
        self._hidden_paused = True
        self._pause_activity()
        super().hide()
        if not notify:
            return
        if callable(getattr(self, "on_hidden", None)):
            self.on_hidden()
        self._arm_dock_reactivate_restore()

    def _pause_activity(self) -> None:
        """暂停动画解码与所有活动定时器（窗口不可见时没有任何可见效果）。"""
        if not hasattr(self, 'movie'):
            return  # 未完整初始化（测试桩/构造早期）无可暂停
        if self.movie is not None:
            self.movie.stop()
        self._move_timer.stop()
        # 全屏 watcher 不能在"全屏自动隐藏"期间停：它是退出全屏后
        # 重新 show() 的唯一检测路径，停了桌宠就再也回不来。
        # 只有手动隐藏（托盘/右键，_auto_hidden 为 False）才停它。
        if not self._auto_hidden:
            self._stop_fs_watch()
        self._self_talk_timer.stop()
        self._animation_gap_timer.stop()
        self._squash_timer.stop()
        self._squash_active = False
        if hasattr(self, 'agent_link_manager') and self.agent_link_manager is not None:
            self.agent_link_manager.pause()
        if hasattr(self, 'lib') and self.lib is not None and hasattr(self.lib, 'pause_warm'):
            self.lib.pause_warm()
        self._cancel_move()
        self._cancel_animation_gap()
        self._speech_bubble.hide()

    def _resume_activity(self) -> None:
        """显示时恢复动画与所需定时器（状态与隐藏前一致）。"""
        if not hasattr(self, 'movie'):
            return  # 未完整初始化（测试桩/构造早期）无可恢复
        if self.movie is not None:
            # 从当前动画第一帧重新开始：隐藏期间用户看不到，观感无差异；
            # 若隐藏前正在移动，_cancel_move 已清掉移动计划，不会出现"瞬移"。
            self._switch(self.anim)
        if self._watch_required():
            self._start_fs_watch()
        self._schedule_self_talk()
        if hasattr(self, 'agent_link_manager') and self.agent_link_manager is not None:
            self.agent_link_manager.resume()
        if hasattr(self, 'lib') and self.lib is not None and hasattr(self.lib, 'resume_warm'):
            self.lib.resume_warm()

    _FS_SKIP_CLASSES = {
        'Progman', 'WorkerW', 'Shell_TrayWnd', 'Shell_SecondaryTrayWnd',
        'Windows.UI.Core.CoreWindow',  # 开始菜单/通知中心全屏层
    }

    _fullscreen_geometry_hit = staticmethod(_fullscreen_geometry_hit)

    # ------------------------------------------------------------------
    # 全屏 watcher：后台线程轮询（纯 win32，线程安全）+ 信号回主线程
    # ------------------------------------------------------------------
    def _fg_fullscreen_win32(self) -> bool:
        """前台窗口是否真全屏。仅返回布尔值，诊断细节见 _fg_fullscreen_probe。"""
        try:
            return self._fg_fullscreen_probe()[0]
        except Exception:
            return False

    _fs_user_busy_state = staticmethod(_fs_user_busy_state)

    def _fg_fullscreen_probe(self) -> tuple[bool, str]:
        return probe_foreground_fullscreen(
            skip_classes=self._FS_SKIP_CLASSES,
            geometry_hit=self._fullscreen_geometry_hit,
            busy_state=self._fs_user_busy_state,
        )

    def _start_fs_watch(self) -> None:
        """启动全屏监视线程（幂等）。"""
        if self._fs_thread is not None and self._fs_thread.is_alive():
            return
        self._fs_stop.clear()
        self._fs_thread = threading.Thread(
            target=self._fs_watch_loop, daemon=True, name="pet-fs-watch")
        self._fs_thread.start()
        logging.info("全屏监视线程已启动")

    def _stop_fs_watch(self) -> None:
        """停止全屏监视线程（不 join，线程 1s 内自行退出，绝不卡 UI）。"""
        self._fs_stop.set()

    def _fs_watch_loop(self) -> None:
        """后台轮询前台窗口（1Hz 节拍），用于全屏自动隐藏。"""
        polls = 0
        next_fullscreen = time.monotonic() + 1.0
        while not self._fs_stop.wait(0.05):
            if shiboken6.isValid(self) is False:
                return
            now = time.monotonic()
            if not self.auto_hide_fullscreen or now < next_fullscreen:
                continue
            next_fullscreen = now + 1.0
            try:
                hit, detail = self._fg_fullscreen_probe()
            except Exception:
                logging.exception("全屏检测异常")
                continue
            polls += 1
            if hit != self._fs_last:
                self._fs_last = hit
                logging.info("全屏检测变化 hit=%s (%s)", hit, detail)
                if shiboken6.isValid(self) is False:
                    return
                self.fullscreen_changed.emit(hit)
            elif polls % 15 == 0:
                logging.info("全屏检测心跳 hit=%s %s", hit, detail)

    def _watch_required(self) -> bool:
        return os.name == 'nt' and self.auto_hide_fullscreen

    def _on_fullscreen_changed(self, hit: bool) -> None:
        """主线程：全屏出现 → 隐藏桌宠；全屏退出 → 恢复。"""
        logging.info("全屏状态变化 hit=%s auto_hidden=%s visible=%s", hit, self._auto_hidden, self.isVisible())
        if hit:
            if not self._auto_hidden and self.isVisible():
                self._auto_hidden = True
                self._speech_bubble.hide()
                self.hide(notify=False)  # 自动隐藏是内部语义，不弹"桌宠已隐藏"托盘通知
        elif self._auto_hidden:
            self._auto_hidden = False
            self.show()

    def set_auto_hide_fullscreen(self, on: bool) -> None:
        """全屏自动隐藏开关（供设置/菜单调用）。"""
        self.auto_hide_fullscreen = bool(on)
        self.cfg.set('auto_hide_fullscreen', self.auto_hide_fullscreen)
        self.cfg.save()
        if self._watch_required():
            self._start_fs_watch()
        else:
            self._stop_fs_watch()
        if not self.auto_hide_fullscreen and self._auto_hidden:
            self._auto_hidden = False
            self.show()


    def _arm_dock_reactivate_restore(self) -> None:
        """macOS：隐藏后点击 Dock 图标激活应用时自动恢复桌宠（一次性监听）。

        连接只建立一次，用 _dock_reactivate_armed 控制响应次数，
        避免对销毁中的窗口反复 connect/disconnect。
        """
        if sys.platform != 'darwin':
            return
        if getattr(self, "_dock_reactivate_armed", False):
            return
        app = QApplication.instance()
        if app is None:
            return
        self._dock_reactivate_armed = True
        app.applicationStateChanged.connect(self._restore_on_dock_reactivate)

    def _restore_on_dock_reactivate(self, state) -> None:
        if state != Qt.ApplicationState.ApplicationActive:
            return
        if not getattr(self, "_dock_reactivate_armed", False):
            return
        self._dock_reactivate_armed = False
        self.show()

    def _ensure_dock_icon_on_hide(self) -> None:
        """macOS：隐藏桌宠时临时开启 Dock 图标，供点击恢复。

        只改运行期策略、绝不写回配置：show_dock_icon 是用户偏好，
        一次隐藏不能把它覆盖掉，也不能经其他路径的 cfg.save() 落盘。
        恢复显示时由 _restore_dock_icon_preference 按偏好还原。
        """
        if sys.platform != 'darwin' or bool(self.cfg.get('show_dock_icon', True)):
            return
        if getattr(self, "_dock_icon_forced", False):
            return
        self._dock_icon_forced = True
        try:
            from pet.infrastructure.desktop_platform import _mac_set_dock_icon_visible
            _mac_set_dock_icon_visible(True)
        except Exception:
            self._dock_icon_forced = False

    def _restore_dock_icon_preference(self) -> None:
        """macOS：桌宠恢复显示后按用户偏好还原 Dock 图标策略。"""
        if sys.platform != 'darwin' or not getattr(self, "_dock_icon_forced", False):
            return
        self._dock_icon_forced = False
        try:
            from pet.infrastructure.desktop_platform import _mac_set_dock_icon_visible
            _mac_set_dock_icon_visible(bool(self.cfg.get('show_dock_icon', True)))
        except Exception:
            pass

    def set_no_move(self, on: bool) -> None:
        """切换「不移动」：禁用自动移动；勾选瞬间若正在移动则立即停下回待机。"""
        self.no_move = bool(on)
        self.cfg.set('no_move', self.no_move)
        self.cfg.save()
        if self.no_move and self._move_plan is not None:
            if self.idles:
                self._switch(self._pick(self.idles))  # 打断进行中的移动

    # ================================================================ 播放
    def _connect_movie(self, name: str, movie) -> None:
        """按需连接 clip 信号（懒加载）：同一动画只连接一次。

        兜底说明：主线程被阻塞导致队列溢出、最后一帧被丢弃时，
        frameChanged 永远到不了末尾帧；finished 信号保证动画链一定继续。
        """
        if name in self._connected_movies:
            return
        movie.frameChanged.connect(lambda n, name=name: self._on_frame(name, n))
        movie.finished.connect(lambda name=name: self._on_clip_finished(name))
        self._connected_movies.add(name)

    def _switch(self, name: str) -> None:
        """切换到指定动画（链式模型：全部一次性播放）。"""
        self._cancel_move()
        self.anim = name
        movie = self.lib.movie(name)
        self._connect_movie(name, movie)
        self.movie = movie
        movie.stop()
        movie.jumpToFrame(0)
        if hasattr(movie, 'set_playback_speed'):
            movie.set_playback_speed(self.playback_speed)
        self._ended_fired = False
        self._rebuild_frame()
        movie.start()

    # ---- Agent 联动动作平滑衔接 ----
    def _is_one_shot_playing(self) -> bool:
        """当前是否正在播一次性动作（动作池/点击回应/移动）。待机/转向可立即切换。"""
        return self.anim in self.acts or self.anim in self.clicks or self.anim in self.moves

    def request_link_anim(self, name: str) -> None:
        """Agent 联动动作请求：一次性动作播放中不打断，存为待播（最新覆盖旧的）。"""
        self._pending_link_anim = name
        if not self._is_one_shot_playing():
            self._play_pending_link_anim()

    def _play_pending_link_anim(self) -> None:
        name = self._pending_link_anim
        self._pending_link_anim = None
        if not name:
            return
        self._link_anim_current = name
        self._switch(name)

    def request_link_idle(self) -> None:
        """Agent 回到空闲：取消待播联动；一次性动作让它播完自然回待机，否则立即回待机。"""
        self._pending_link_anim = None
        self._link_anim_current = None
        if self._is_one_shot_playing():
            return
        if self.idles:
            self._switch(self._pick(self.idles))

    def _on_frame(self, name: str, n: int) -> None:
        """媒体帧推进回调：重建画面；最后一帧触发播完处理。"""
        if name != self.anim or self.movie is None:
            return
        self._rebuild_frame()
        self.update()
        if n >= self.lib.frames(name) - 1 and not self._ended_fired:
            self._ended_fired = True
            self.movie.stop()  # 停在最后一帧，等 _on_anim_ended 切走
            self._on_anim_ended(name)

    def _rebuild_frame(self) -> None:
        """重建当前帧：缩放 + 朝向镜像 + 生成窗口 mask。"""
        if self.movie is None:
            return
        img = None
        if hasattr(self.movie, 'currentImage'):
            try:
                img = self.movie.currentImage()
            except Exception:
                img = None
        if img is None or img.isNull():
            pm = self.movie.currentPixmap()
            if pm is None or pm.isNull():
                # ffmpeg 缺失/素材损坏时首帧解码可能失败返回 None，跳过本帧而不是崩溃
                return
            img = pm.toImage()
        # 含文字/方向性画面的动画登记在 lib.no_mirror，朝右时也不镜像（否则文字反显）
        if self.facing == 'right' and self.anim not in getattr(self.lib, 'no_mirror', frozenset()):
            img = img.mirrored(True, False)
        # 按屏幕 DPR 渲染到物理像素，避免高分屏下被 Qt 二次放大导致模糊。
        # 先转预乘 alpha 再缩放：直通 alpha 缩放会让透明像素的 RGB 渗入
        # 半透明边缘，产生暗边/彩边（毛边来源之一）。
        scr = self._screen_available()
        dpr = scr.devicePixelRatio() if scr is not None else 1.0
        w_c = max(1, int(round(catalog.CANVAS_W * self.scale * dpr)))
        h_c = max(1, int(round(catalog.CANVAS_H * self.scale * dpr)))
        img = img.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        img = img.scaled(w_c, h_c,
                         Qt.AspectRatioMode.IgnoreAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        self._frame_pixmap = pm
        self._hit_alpha_image = None  # 帧已变化，逐像素命中缓存失效
        self._sync_mask()

    def _frame_draw_rect(self) -> QRect:
        """当前帧在窗口内的绘制矩形（逻辑坐标）；paintEvent 与命中测试共用。"""
        if self._squash_active:
            x, y, w, h = _squash_geometry(
                self._w,
                self._h,
                int(round(catalog.CANVAS_W * self.scale)),
                int(round(catalog.CANVAS_H * self.scale)),
                self._squash_progress,
            )
            return QRect(x, y, w, h)
        return QRect(0, int(round(catalog.PAD * self.scale)),
                     int(round(catalog.CANVAS_W * self.scale)),
                     int(round(catalog.CANVAS_H * self.scale)))

    def _sync_mask(self) -> None:
        """更新角色可见轮廓与窗口 mask。

        - 非 Windows：继续用 QWidget.setMask 实现透明区域鼠标穿透。
        - Windows：不再 setMask（1-bit 裁剪会破坏半透明边缘），只更新
          _mask_bounds；鼠标穿透由 WindowsPerPixelInputController 负责。
        """
        canvas = QImage(self._w, self._h, QImage.Format.Format_ARGB32)
        canvas.fill(Qt.GlobalColor.transparent)
        p = QPainter(canvas)
        if self._frame_pixmap is not None:
            rect = self._frame_draw_rect()
            # 与 paintEvent 完全相同的绘制调用，保证 mask 与画面逐像素一致
            p.drawPixmap(rect, self._frame_pixmap)
        p.end()
        mask = QBitmap.fromImage(canvas.createAlphaMask())
        self._mask_bounds = QRegion(mask).boundingRect()
        if os.name != "nt":
            self.setMask(mask)
        elif not self.mask().isEmpty():
            self.clearMask()

    def character_local_region(self) -> QRect:
        """当前角色可见区域（窗口局部坐标）；供贴边/气泡定位等增量功能复用。"""
        if self._mask_bounds is not None and not self._mask_bounds.isEmpty():
            return QRect(self._mask_bounds)
        return QRect(0, 0, self._w, self._h)

    def _is_transparent_at(self, local: QPoint) -> bool:
        """判断窗口局部坐标处是否透明（供 Windows 命中测试使用）。"""
        if self._frame_pixmap is None or self._frame_pixmap.isNull():
            return False
        rect = self._frame_draw_rect()
        if not rect.contains(local):
            return True
        if self._hit_alpha_image is None:
            self._hit_alpha_image = self._frame_pixmap.toImage()
        img = self._hit_alpha_image
        if img.isNull():
            return False
        dpr = self._frame_pixmap.devicePixelRatio() or 1.0
        px = int(round((local.x() - rect.x()) * dpr))
        py = int(round((local.y() - rect.y()) * dpr))
        if px < 0 or py < 0 or px >= img.width() or py >= img.height():
            return True
        return img.pixelColor(px, py).alpha() < 16

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt 命名)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._frame_pixmap is not None:
            if self._squash_active:
                # Q 弹：使用逻辑帧尺寸；QPixmap.width() 可能是 DPR 物理像素尺寸。
                x, y, w, h = _squash_geometry(
                    self._w,
                    self._h,
                    int(round(catalog.CANVAS_W * self.scale)),
                    int(round(catalog.CANVAS_H * self.scale)),
                    self._squash_progress,
                )
                painter.drawPixmap(x, y, w, h, self._frame_pixmap)
            else:
                # 落地对齐：整帧下移 PAD×scale，让人物脚底踩在窗口底线
                painter.translate(0, int(round(catalog.PAD * self.scale)))
                painter.drawPixmap(0, 0, self._frame_pixmap)
        painter.end()

    def _start_squash(self) -> None:
        """点击时启动 Q 弹效果：画面先变矮再恢复。"""
        self._squash_active = True
        self._squash_progress = 0.0
        self._squash_clock.start()
        self._squash_timer.start()
        self.update()

    def _on_squash_tick(self) -> None:
        elapsed = self._squash_clock.elapsed()
        self._squash_progress = min(1.0, elapsed / self._squash_duration_ms)
        if self._squash_progress >= 1.0:
            self._squash_active = False
            self._squash_timer.stop()
        self._sync_mask()  # mask 跟随 squash 几何，避免变形边缘被旧轮廓裁切
        self.update()

    def icon_pixmap(self, size: int = 64) -> QPixmap:
        """托盘/菜单图标：裁掉帧透明留白后再缩放。"""
        pm = self._frame_pixmap
        if pm is None and self.idle:
            pm = self.lib.movie(self.idle).currentPixmap()
        if pm is None or pm.isNull():
            return QPixmap()
        return PetWindow._crop_icon_pixmap(pm, size)

    @staticmethod
    def _crop_icon_pixmap(pm: QPixmap, size: int) -> QPixmap:
        image = pm.toImage()
        bounds = QRegion(QBitmap.fromImage(image.createAlphaMask())).boundingRect()
        if bounds.isValid() and not bounds.isEmpty():
            pm = QPixmap.fromImage(image.copy(bounds))
        return pm.scaled(size, size,
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

    def animation_icon_pixmap(self, name: str, size: int = 64) -> QPixmap:
        """Synchronous compatibility path using a representative later frame."""
        image = PetWindow.animation_icon_image(self, name)
        if not image.isNull():
            return PetWindow._crop_icon_pixmap(QPixmap.fromImage(image), size)
        clip = self.lib.movie(name)
        target = representative_frame_index(clip.frameCount())
        if name != self.anim:
            clip.jumpToFrame(target)
        pm = clip.currentPixmap()
        if pm is None or pm.isNull():
            return self.icon_pixmap(size)
        return PetWindow._crop_icon_pixmap(pm, size)

    def animation_icon_image(self, name: str) -> QImage:
        """Decode a representative frame as QImage; safe to call in a worker."""
        lock = getattr(self, "_animation_icon_cache_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._animation_icon_cache_lock = lock
            self._animation_icon_image_cache = {}
            self._animation_icon_inflight = {}
        with lock:
            cached = self._animation_icon_image_cache.get(name)
            if cached is not None:
                return QImage(cached)
            pending = self._animation_icon_inflight.get(name)
            owner = pending is None
            if owner:
                pending = threading.Event()
                self._animation_icon_inflight[name] = pending
        if not owner:
            pending.wait()
            with lock:
                return QImage(self._animation_icon_image_cache.get(name, QImage()))
        path = self.lib.clip_path(name)  # 不在 worker 线程构造 WebMClip（Qt 线程亲和）
        try:
            image = decode_representative_frame(path) if path is not None else QImage()
            with lock:
                if not image.isNull():
                    cache = self._animation_icon_image_cache
                    # 简单上限：动画名数量有限，超限全清后按需重新解码
                    if len(cache) >= 128:
                        cache.clear()
                    cache[name] = QImage(image)
            return image
        finally:
            with lock:
                event = self._animation_icon_inflight.pop(name, None)
                if event is not None:
                    event.set()

    def animation_icon_cached_image(self, name: str) -> QImage:
        """Return a decoded thumbnail without starting any work."""
        lock = getattr(self, "_animation_icon_cache_lock", None)
        if lock is None:
            return QImage()
        with lock:
            return QImage(self._animation_icon_image_cache.get(name, QImage()))

    def _on_clip_finished(self, name: str) -> None:
        """WebMClip 播完兜底：正常路径在末尾帧处由 _on_frame 提前 stop，
        这里只处理“末尾帧被丢弃、结束标记被消费”的异常路径，推进动画链。"""
        if name != self.anim or self.movie is None:
            return
        if not self._ended_fired:
            self._ended_fired = True
            self._on_anim_ended(name)

    # ================================================================ 动画链
    def _on_anim_ended(self, name: str) -> None:
        if name == SING_ANIM:
            # 音乐自动唱歌开启且当前仍处于“唱歌中”时，直接无缝续播；
            # 不再每次播完都查一次音频 COM，降低长时间运行的崩溃风险。
            # 音乐停止由 _check_music_sing 定时检测后清掉 _music_sing_active。
            if self._music_sing_enabled and self._music_sing_active:
                self._switch(SING_ANIM)
                return
            self._music_sing_active = False
        if name == self.drag and self._dragging:
            self.movie.jumpToFrame(0)
            self._ended_fired = False
            self.movie.start()
            return
        # Agent 联动：待播动作优先接上（平滑衔接，不打断刚播完的动作）
        if self._pending_link_anim:
            self._play_pending_link_anim()
            return
        # 联动动作播完仍有 Agent 在忙 → 接下一个联动动作；否则走正常动画链
        if self._link_anim_current is not None and name == self._link_anim_current:
            self._link_anim_current = None
            provider = self._link_next_provider
            nxt = provider() if callable(provider) else None
            if nxt:
                self._link_anim_current = nxt
                self._switch(nxt)
                return
        if name in self.turns:
            self.facing = 'right' if self.facing == 'left' else 'left'
        if name == self.drag or name in self.clicks:
            self._cancel_animation_gap()
            if self.idles:
                self._switch(self._pick(self.idles))
            return
        if self._animation_gap_active:
            if name in self.idles or name in self.turns:
                self._play_animation_gap_step()
            else:
                # 异常状态（gap 期间播了非待机/转向动画）：兜底推进动画链，
                # 避免 return 后动画链停摆
                self._pick_next()
            return
        if self.animation_gap_seconds > 0 and (name in self.acts or name in self.moves):
            self._start_animation_gap()
            return
        self._pick_next()

    def _cancel_animation_gap(self) -> None:
        self._animation_gap_timer.stop()
        self._animation_gap_active = False

    def _start_animation_gap(self) -> None:
        if self.animation_gap_seconds <= 0 or not (self.idles or self.turns):
            self._pick_next()
            return
        self._animation_gap_active = True
        self._animation_gap_timer.start(max(1, int(round(self.animation_gap_seconds * 1000))))
        self._play_animation_gap_step()

    def _play_animation_gap_step(self) -> None:
        pool = self.idles + self.turns
        if pool:
            self._switch(self._pick(pool, exclude=self.anim))

    def _on_animation_gap_timeout(self) -> None:
        self._animation_gap_active = False

    def _pick_next(self) -> None:
        """动画链：30% 待机 / 10% 转向 / 40% 动作 / 20% 移动（空间不够回退动作）。

        「不移动」模式下跳过移动分支，其概率并入动作 → 30% 待机 / 10% 转向 / 60% 动作。
        """
        if not self.acts:
            # 角色包没有随机动作素材（仅核心动画）：需要 acts 的分支与回退
            # 统一改走待机；待机也没有则保持当前动画，绝不 random.choice([]) 崩溃。
            if self.idles:
                self._switch(self._pick(self.idles, exclude=self.anim))
            return
        roll = random.random()
        if roll < catalog.P_IDLE:
            if self.idles:
                self._switch(self._pick(self.idles, exclude=self.anim))
            else:
                self._switch(self._pick(self.acts, exclude=self.anim))
        elif roll < catalog.P_TURN:
            if self.turns:
                self._switch(self._pick(self.turns, exclude=self.anim))
            else:
                self._switch(self._pick(self.acts, exclude=self.anim))
        elif roll < catalog.P_ACTS:
            self._switch(self._pick(self.acts, exclude=self.anim))
        else:
            if self.no_move or not self._try_move():
                self._switch(self._pick(self.acts, exclude=self.anim))

    @staticmethod
    def _pick(pool: list[str], exclude: str | None = None) -> str:
        entries = [n for n in pool if n != exclude] or pool
        return random.choice(entries)

    # ================================================================ 移动
    def _try_move(self, name: str | None = None) -> bool:
        """计划一次朝 facing 移动；边缘空间不足时智能调头，动态自适应平滑漫步。"""
        if self._interaction_state == DRAGGING:
            return False
        if self._move_plan is not None:
            return True  # 移动中/已计划
        scr = self._screen_available()
        if scr is None:
            return False
        avail = scr.availableGeometry()
        dir_sign = 1 if self.facing == 'right' else -1
        cx = self.x() + self._w / 2
        half_w = self._w / 2
        left_bound = avail.left() + catalog.MOVE_MARGIN + half_w
        right_bound = avail.right() - catalog.MOVE_MARGIN - half_w

        if right_bound <= left_bound:
            return False

        # 智能调头判定：如果当前朝向空间不足，而反方向空间充裕，自动调头朝向开阔区域
        available_forward = (right_bound - cx) if dir_sign == 1 else (cx - left_bound)
        available_backward = (cx - left_bound) if dir_sign == 1 else (right_bound - cx)

        if bool(self.cfg.get('smart_edge_turn', True)):
            if available_forward < catalog.MOVE_MIN_PX and available_backward >= catalog.MOVE_MIN_PX:
                self.facing = 'left' if self.facing == 'right' else 'right'
                dir_sign = -dir_sign
                available_forward = available_backward
                if self.turns:
                    turn_anim = self._pick(self.turns)
                    self._switch(turn_anim)

        if available_forward < 20.0:
            return False

        if not self.moves:
            return False

        # 自适应步长计算，确保永远平滑不撞墙超界
        max_dist = min(float(catalog.MOVE_MAX_PX), max(float(catalog.MOVE_MIN_PX), available_forward))
        min_dist = min(float(catalog.MOVE_MIN_PX), max_dist)
        distance = random.uniform(min_dist, max_dist)
        target_cx = cx + dir_sign * distance
        target_cx = max(left_bound, min(right_bound, target_cx))

        move_name = name or self._pick(self.moves)
        duration = self.lib.duration(move_name)
        self._switch(move_name)
        self._move_plan = {
            'start_x': self.x(),
            'target_x': int(round(target_cx - half_w)),
            'start_y': self.y(),
            'target_y': wander_target_y(
                self.y(), avail.top(), avail.bottom(), self._h, catalog.MOVE_MARGIN
            ),
            'duration': duration,
        }
        self._move_timer.start()
        return True

    def _trigger_move(self, name: str) -> None:
        """手动触发移动（右键菜单）：先打断当前移动，再朝 facing 方向走动；
        屏幕空间不足则原地播放走路姿态（不位移）。"""
        self._cancel_move()
        self._cancel_animation_gap()
        if not self._try_move(name):
            self._switch(name)  # 贴边放不下：原地播放走路姿态，不位移

    def _on_move_tick(self) -> None:
        """位置驱动：跟随动画播放进度插值（前后各 2s 不动，中间走完全程）。"""
        plan = self._move_plan
        if not plan or self.movie is None:
            self._move_timer.stop()
            return
        t = self.movie.currentTimeSeconds()
        lead, tail = catalog.MOVE_LEAD_SEC, catalog.MOVE_TAIL_SEC
        dur = plan['duration']
        if t <= lead:
            x = plan['start_x']
            y = plan['start_y']
        elif t >= dur - tail:
            x = plan['target_x']
            y = plan['target_y']
        else:
            raw_progress = (t - lead) / max(0.1, dur - lead - tail)
            raw_progress = max(0.0, min(1.0, raw_progress))
            if bool(self.cfg.get('smooth_wander', True)):
                # 平滑加减速缓动（SmoothStep），起步和停步更自然
                progress = raw_progress * raw_progress * (3.0 - 2.0 * raw_progress)
            else:
                progress = raw_progress
            x = plan['start_x'] + (plan['target_x'] - plan['start_x']) * progress
            y = plan['start_y'] + (plan['target_y'] - plan['start_y']) * progress
        self.move(int(round(x)), int(round(y)))
        if t >= dur - tail:
            # 到位：提交终点，动画自然播完后续链。
            # 不把自动移动的终点写入记忆位置，否则重启后桌宠会停在
            # 上次随机游走的位置，而不是用户手动放置的位置。
            self._move_timer.stop()
            self._move_plan = None

    def _cancel_move(self) -> None:
        self._move_timer.stop()
        self._move_plan = None

    # ================================================================ 交互
    def _is_in_interactive_area(self, local_pos) -> bool:
        """由于动画左右有留白，只把窗口中间 1/3 宽度作为可交互区域。"""
        return self._w / 3.0 <= local_pos.x() <= self._w * 2.0 / 3.0

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self._context_menu_suppressed = False
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._is_in_interactive_area(event.position().toPoint()):
                return  # 左右留白区域不参与点击/拖拽
            if self.lock_position:
                # 锁定位置：不记录按下，拖拽不会开始；松手时仍按点击处理
                return
            self._press_global = event.globalPosition().toPoint()
            self._interaction_state = "PRESS_CANDIDATE"
            self._grab_offset = self._press_global - self.pos()
            self._dragging = False
            self._cancel_move()  # 按下即打断移动
            self.setFocus(Qt.FocusReason.OtherFocusReason)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_global is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        g = event.globalPosition().toPoint()
        delta = g - self._press_global
        if not self._dragging:
            if math.hypot(delta.x(), delta.y()) < catalog.DRAG_THRESHOLD * self.scale:
                return  # 未超阈值：仍是点击候选
            if self.shift_drag and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                # SHIFT+左键才能拖动：拖拽开始（越过阈值）时必须按住 SHIFT。
                # 判定放在阈值处而非按下时：Windows 上 press 事件的修饰键
                # 不一定可靠，且用户可能先按下再补按 SHIFT。未按 SHIFT 时
                # 取消按压状态，松手仍按点击处理。
                self._press_global = None
                self._grab_offset = None
                return
            self._dragging = True
            self._interaction_state = "DRAGGING"
            # 用户真正开始拖动 = 接管位置决策，撤销"等副屏上线自动恢复"
            # （必须在这里而不是按下时：普通点击/未过阈值/未按 SHIFT 不算接管）
            _disarm = getattr(self, '_disarm_screen_restore_retry', None)
            if callable(_disarm):
                _disarm()
            if self.drag:
                self._switch(self.drag)  # 进入拖拽：播放悬空反馈动画
            self.move(g - self._grab_offset)
            event.accept()
            return

        # 已经处于拖拽中
        self.move(g - self._grab_offset)  # 跟手（保持抓起时的偏移）
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        was_dragging = self._dragging
        g = event.globalPosition().toPoint()
        dist = 0.0
        if self._press_global is not None:
            d = g - self._press_global
            dist = math.hypot(d.x(), d.y())
        if was_dragging:
            self._just_dragged = True  # 抑制拖拽结束后的幽灵点击
            QTimer.singleShot(150, self, self._clear_just_dragged)
            # 拖动物理已移除：松手停在原地
            if self._grab_offset is not None:
                self.move(g - self._grab_offset)  # 停在松手处
            self._save_position()
            if self.idles:
                self._switch(self._pick(self.idles))  # 回待机缓冲
        elif dist < catalog.DRAG_THRESHOLD * self.scale:
            if not self._try_open_quick_chat_from_bubble(g):
                self._on_click()
        self._dragging = False
        self._interaction_state = "IDLE"
        self._press_global = None
        self._grab_offset = None
        event.accept()

    def _clear_just_dragged(self) -> None:
        self._just_dragged = False

    def _on_speech_bubble_clicked(self) -> None:
        if callable(getattr(self, "on_open_quick_chat", None)):
            self.on_open_quick_chat()

    def _try_open_quick_chat_from_bubble(self, global_pos) -> bool:
        """点击桌宠头顶的气泡时打开快速对话（而不是触发 Q 弹）。"""
        callback = getattr(self, "on_open_quick_chat", None)
        if not callable(callback):
            return False
        bubble = getattr(self, "_speech_bubble", None)
        if bubble is None or not bubble.isVisible():
            return False
        if not bubble.geometry().contains(global_pos):
            return False
        callback()
        return True

    def _on_click(self) -> None:
        """真点击 → 随机一个点击回应动画，并重置当前动画（可连续点击打断）。"""
        if self._just_dragged:
            return
        if callable(self.on_restore_fun_windows):
            self.on_restore_fun_windows()
        if not self.clicks:
            return
        # 点击可以打断当前动画（包括正在播放的点击回应），实现连续 Q 弹。
        click_name = self._pick(self.clicks)
        self._cancel_move()
        self._start_squash()
        self._switch(click_name)
        if self.click_show_self_talk and self._self_talk_enabled:
            if self._show_click_self_talk(click_name):
                self._schedule_self_talk(after_display=True)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        if self._context_menu_suppressed:
            self._context_menu_suppressed = False
            event.accept()
            return
        if self._interaction_state == "DRAGGING" and self._press_global is not None:
            event.accept()
            return
        if not self._is_in_interactive_area(event.pos()):
            return
        self._show_context_menu(event.globalPos())

    def _show_context_menu(self, global_pos: QPoint) -> None:
        self._context_menu_anchor = QPoint(global_pos)
        # 气泡是置顶 Tool 窗口（层级高于原生菜单 popup），右键时先隐藏，
        # 避免气泡盖住菜单
        self._speech_bubble.hide()
        menu = QMenu(self)
        self._active_context_menu = menu
        _populate_context_menu(menu, self)
        menu.aboutToHide.connect(
            lambda self=self: QTimer.singleShot(0, self, self._restore_on_top_after_context_menu)
        )
        # 根菜单避让角色且始终保持 LTR 视觉方向；右侧不够时贴近角色左侧。
        # 子菜单弹出侧由 Qt 按屏幕空间决定，再不行使用整树重叠最少的远角。
        pet_rect = self.visible_content_rect()
        scr = self._screen_available()
        avail = scr.availableGeometry() if scr is not None else QRect()
        submenu_width = max(
            (child.sizeHint().width() for child in menu.findChildren(QMenu)),
            default=0,
        )
        popup_pos, direction = pick_context_menu_position(
            pet_rect, menu.sizeHint(), submenu_width, avail
        )
        menu.setLayoutDirection(direction)
        for child in menu.findChildren(QMenu):
            child.setLayoutDirection(direction)
        menu_size = menu.sizeHint()
        slide_toward_pet = 18 if popup_pos.x() < pet_rect.center().x() else -18
        transition_start = _clamp_menu_rect(
            QRect(
                popup_pos.x() + slide_toward_pet,
                popup_pos.y(),
                menu_size.width(),
                menu_size.height(),
            ),
            avail,
        ).topLeft()
        menu.aboutToShow.connect(
            lambda menu=menu, target=QPoint(popup_pos): QTimer.singleShot(
                0,
                menu,
                lambda menu=menu, target=target: animate_context_menu_to(menu, target),
            )
        )
        menu.exec(transition_start)
        callbacks = take_deferred_menu_callbacks(menu)
        if getattr(self, "_active_context_menu", None) is menu:
            self._active_context_menu = None
        if callbacks:
            def dispatch_callbacks() -> None:
                for callback in callbacks:
                    callback()

            def schedule_after_menu_destroyed(*_args) -> None:
                # Windows may keep the translucent popup's native backing
                # surface alive briefly after exec() returns. Wait for the
                # QMenu QObject to be destroyed, then yield once more before
                # showing or activating another top-level window.
                try:
                    if not shiboken6.isValid(self):
                        return
                    QTimer.singleShot(0, self, dispatch_callbacks)
                except RuntimeError:
                    # The owning pet can be destroyed between isValid() and
                    # registering the context-bound timer during shutdown or
                    # character replacement. Its menu command is no longer
                    # meaningful, so discard it without touching Qt again.
                    return

            menu.destroyed.connect(schedule_after_menu_destroyed)
        # 菜单使用完毕即释放整棵菜单树：QMenu 以长命窗口为 parent，
        # 不删除会随每次右键累积（子菜单/动作/线程池/图标 pixmap）。
        # 先清掉尚未启动的解码任务，避免 QThreadPool 析构时在 GUI 线程
        # 等待运行中的 worker。
        pools = []
        for submenu in menu.findChildren(QMenu):
            pool = getattr(submenu, "_animation_icon_pool", None)
            if pool is not None:
                pool.clear()
                pools.append(pool)

        def delete_when_idle() -> None:
            """非阻塞等待图标解码 worker 结束后再释放菜单树。

            直接 pool.waitForDone(3000) 会阻塞 GUI 线程最多 3 秒，可能造成
            右键菜单关闭时卡顿/假死；这里每 50ms 轮询一次，不阻塞事件循环。
            """
            if any(not pool.waitForDone(0) for pool in pools):
                QTimer.singleShot(50, delete_when_idle)
                return
            menu.deleteLater()

        if pools:
            delete_when_idle()
        else:
            menu.deleteLater()

    def reopen_context_menu(self, menu: QMenu) -> None:
        """Close the old template and immediately show the newly selected one."""
        # QMenu may move the requested right-click point to remain on-screen.
        # Preserve the position the user actually saw, not the raw event point.
        global_pos = QPoint(menu.pos()) if menu is not None else QPoint(
            getattr(self, "_context_menu_anchor", QCursor.pos())
        )
        self._context_menu_anchor = QPoint(global_pos)
        menu.close()
        QTimer.singleShot(10, self, lambda: self._show_context_menu(global_pos))

    @staticmethod
    def _read_self_talk_texts(value) -> list[str]:
        if not isinstance(value, list):
            return list(DEFAULT_SELF_TALK_TEXTS)
        texts = []
        for item in value:
            text = str(item).strip()[:120]
            if text and text not in texts:
                texts.append(text)
        return texts or list(DEFAULT_SELF_TALK_TEXTS)

    def _schedule_self_talk(self, *, after_display: bool = False) -> None:
        self._self_talk_timer.stop()
        if not self._self_talk_enabled or not (
            self._self_talk_texts or self._self_talk_images
        ):
            return
        delay = random.uniform(self._self_talk_min_interval, self._self_talk_max_interval)
        if after_display:
            delay += self._self_talk_duration_seconds
        self._self_talk_timer.start(max(1000, int(round(delay * 1000))))

    def _show_self_talk_text(self, text: str) -> bool:
        if getattr(self, "_bubble_suppressed", False):
            return False
        duration_ms = int(round(self._self_talk_duration_seconds * 1000))
        anchor = self.visible_content_rect()
        _set_speech_bubble_interactive(self)
        if hasattr(self, 'hold_bubble') and callable(self.hold_bubble):
            self.hold_bubble(self._self_talk_duration_seconds + 1.5)
        self._speech_bubble.show_text(
            text, anchor, duration_ms, pet_scale=self.scale
        )
        return True

    def _show_random_self_talk(self) -> bool:
        if getattr(self, "_bubble_suppressed", False):
            return False
        # 惰性剔除运行期间被删除的图片（列表是启动/设置时的快照）
        live_images = [p for p in self._self_talk_images if p.is_file()]
        if len(live_images) != len(self._self_talk_images):
            self._self_talk_images = live_images
        choices = [
            ("text", text) for text in self._self_talk_texts
        ] + [
            ("image", path) for path in self._self_talk_images
        ]
        if not choices:
            return False
        kind, value = random.choice(choices)
        duration_ms = int(round(self._self_talk_duration_seconds * 1000))
        anchor = self.visible_content_rect()
        _set_speech_bubble_interactive(self)
        if kind == "image":
            if hasattr(self, 'hold_bubble') and callable(self.hold_bubble):
                self.hold_bubble(self._self_talk_duration_seconds + 1.5)
            return self._speech_bubble.show_image(
                value, anchor, duration_ms, pet_scale=self.scale
            )
        return self._show_self_talk_text(value)

    def _show_click_self_talk(self, click_name: str) -> bool:
        """优先播放当前点击动画绑定的台词；未绑定则回退全局随机自言自语。"""
        character_id = str(self.cfg.get('character', catalog.DEFAULT_CHARACTER))
        texts = self.cfg.click_talk_texts_for(character_id, click_name)
        if texts:
            return self._show_self_talk_text(random.choice(texts))
        return self._show_random_self_talk()

    def _on_self_talk_timeout(self) -> None:
        if time.time() < self._bubble_busy_until:
            # 重要气泡占用中：本次自言自语跳过，重新排队下一次
            self._schedule_self_talk()
            return
        displayed = False
        if self._self_talk_enabled and self.isVisible():
            displayed = self._show_random_self_talk()
        self._schedule_self_talk(after_display=displayed)

    def hold_bubble(self, seconds: float) -> None:
        """声明重要气泡占用时长（自言自语在此期间让路）。"""
        self._bubble_busy_until = max(self._bubble_busy_until, time.time() + max(0.0, seconds))

    def set_bubble_suppressed(self, suppressed: bool) -> None:
        """设置窗口打开期间暂停气泡显示；True 时立即隐藏当前气泡。"""
        self._bubble_suppressed = bool(suppressed)
        if self._bubble_suppressed:
            self._speech_bubble.hide()

    def _check_music_sing(self) -> None:
        """检测后台音乐并自动播放唱歌动画（可配置开关）。

        音乐播放期间唱歌动画会持续循环；音乐停止或开关关闭后恢复普通动画链。
        不打断正在播放的一次性动作/点击/拖拽。
        """
        if not self.isVisible():
            return
        if not self._music_sing_enabled:
            self._music_sing_active = False
            return
        from pet.infrastructure import music_detect
        playing = music_detect.is_music_playing()
        if self._music_sing_active:
            if not playing:
                self._music_sing_active = False
            return
        if self._dragging or self._is_one_shot_playing():
            return
        if playing:
            self._music_sing_active = True
            self._switch(SING_ANIM)

    def is_speech_busy(self) -> bool:
        """检查桌宠当前是否正在显示气泡或处于发言保护冷却期。"""
        if getattr(self, "_speech_bubble", None) is not None:
            is_vis = getattr(self._speech_bubble, "isVisible", None)
            if callable(is_vis) and is_vis():
                return True
        return time.time() < getattr(self, "_bubble_busy_until", 0.0)

    def show_bubble(self, text: str, duration_ms: int = 3200, subtitle: str | None = None) -> None:
        """向桌宠头顶冒泡提示（app 层反馈用，非侵入）。重要气泡会占用气泡位。"""
        if not self.isVisible() or self._bubble_suppressed:
            return
        _set_speech_bubble_interactive(self)
        self.hold_bubble(duration_ms / 1000.0 + 2.0)
        self._speech_bubble.show_text(
            str(text), self.visible_content_rect(), duration_ms,
            pet_scale=self.scale, subtitle=str(subtitle or ""),
        )

    def refresh_pet_settings(self) -> None:
        desired_scale = float(self.cfg.get('scale', self.scale))
        self.change_scale(desired_scale)
        desired_speed = float(self.cfg.get('playback_speed', self.playback_speed))
        if abs(desired_speed - self.playback_speed) >= 0.001:
            self.set_playback_speed(desired_speed)
        desired_on_top = bool(self.cfg.get('on_top', True))
        current_on_top = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if desired_on_top != current_on_top:
            self.set_on_top(desired_on_top)
        desired_no_move = bool(self.cfg.get('no_move', False))
        if desired_no_move != self.no_move:
            self.set_no_move(desired_no_move)
        # 窗口类开关也要立即生效（否则用户保存后得重启或再去菜单切一次）
        desired_auto_hide = bool(self.cfg.get('auto_hide_fullscreen', True))
        if desired_auto_hide != self.auto_hide_fullscreen:
            self.set_auto_hide_fullscreen(desired_auto_hide)
        desired_lock = bool(self.cfg.get('lock_position', False))
        if desired_lock != self.lock_position:
            self.set_lock_position(desired_lock)
        desired_shift = bool(self.cfg.get('shift_drag', False))
        if desired_shift != self.shift_drag:
            self.set_shift_drag(desired_shift)
        desired_opacity = int(_float_or_default(self.cfg.get('pet_opacity', 100), 100, 10, 100))
        if desired_opacity != self.pet_opacity:
            self.set_pet_opacity(desired_opacity)
        else:
            self._apply_opacity()  # 首次/未变时也确保窗口已应用
        self.animation_gap_seconds = max(0.0, min(3600.0, float(self.cfg.get('animation_gap_seconds', 0.0))))
        if self.animation_gap_seconds <= 0:
            self._cancel_animation_gap()
        self._music_sing_enabled = bool(self.cfg.get('music_sing_enabled', False))
        self._self_talk_enabled = bool(self.cfg.get('self_talk_enabled', False))
        self._speech_bubble.set_style(
            str(self.cfg.get('self_talk_bubble_style', DEFAULT_SELF_TALK_BUBBLE_STYLE))
        )
        self._self_talk_texts = self._read_self_talk_texts(self.cfg.get('self_talk_texts'))
        self._self_talk_duration_seconds = max(
            1.0,
            min(300.0, float(self.cfg.get(
                'self_talk_duration_seconds', DEFAULT_SELF_TALK_DURATION_SECONDS
            ))),
        )
        self._self_talk_image_dir = str(self.cfg.get('self_talk_image_dir', '') or '')
        self._self_talk_images = list_self_talk_images(_resolve_self_talk_image_dir(self._self_talk_image_dir))
        self._self_talk_min_interval = max(5.0, float(self.cfg.get('self_talk_min_interval', DEFAULT_SELF_TALK_MIN_INTERVAL)))
        self._self_talk_max_interval = max(self._self_talk_min_interval, float(self.cfg.get('self_talk_max_interval', DEFAULT_SELF_TALK_MAX_INTERVAL)))
        self.click_show_self_talk = bool(self.cfg.get('click_show_self_talk', False))
        self._schedule_self_talk()

    def set_context_menu_template(self, template_id: str) -> None:
        """Deprecated: only modern context menu is supported."""
        self.cfg.set('context_menu_template', 'modern')
        self.cfg.save()


    def set_animation_gap(self, seconds: float) -> None:
        self.animation_gap_seconds = max(0.0, min(3600.0, float(seconds)))
        self.cfg.set('animation_gap_seconds', self.animation_gap_seconds)
        self.cfg.save()
        if self.animation_gap_seconds <= 0:
            self._cancel_animation_gap()

    def set_self_talk_settings(
        self,
        enabled: bool,
        minimum: float,
        maximum: float,
        texts,
        *,
        duration: float | None = None,
        image_dir: str | None = None,
    ) -> None:
        self._self_talk_enabled = bool(enabled)
        self._self_talk_min_interval = max(5.0, float(minimum))
        self._self_talk_max_interval = max(self._self_talk_min_interval, float(maximum))
        self._self_talk_texts = self._read_self_talk_texts(texts)
        if duration is not None:
            self._self_talk_duration_seconds = max(1.0, min(300.0, float(duration)))
        if image_dir is not None:
            self._self_talk_image_dir = str(image_dir or '').strip()
            self._self_talk_images = list_self_talk_images(_resolve_self_talk_image_dir(self._self_talk_image_dir))
        self.cfg.set('self_talk_enabled', self._self_talk_enabled)
        self.cfg.set('self_talk_min_interval', self._self_talk_min_interval)
        self.cfg.set('self_talk_max_interval', self._self_talk_max_interval)
        self.cfg.set('self_talk_texts', list(self._self_talk_texts))
        self.cfg.set('self_talk_duration_seconds', self._self_talk_duration_seconds)
        self.cfg.set('self_talk_image_dir', self._self_talk_image_dir)
        self.cfg.save()
        self._schedule_self_talk()

    def set_chat_status(self, state: str, text: str = '') -> None:
        if not text:
            return
        if not self.isVisible():
            return
        _set_speech_bubble_interactive(self)
        self._speech_bubble.show_text(
            text, self.visible_content_rect(), duration_ms=2200,
            pet_scale=self.scale,
        )


    def _toggle_agent_link(self, agent_key: str, on: bool, action=None) -> None:
        """右键菜单切换 Agent 状态联动子项。

        set_enabled 返回 False（用户拒绝授权 / hooks 安装失败）时，
        必须把菜单勾选态回滚，否则 UI 显示已开启而实际未生效。"""
        if hasattr(self, 'agent_link_manager') and self.agent_link_manager is not None:
            ok = self.agent_link_manager.set_enabled(agent_key, on)
            if not ok:
                if action is not None:
                    action.blockSignals(True)
                    action.setChecked(not on)
                    action.blockSignals(False)
                return
        else:
            ag_data = dict(self.cfg.get('agent_link', {}))
            ag_data[agent_key] = bool(on)
            self.cfg.set('agent_link', ag_data)
            self.cfg.save()
        if on:
            self.show_bubble(f"已开启 {agent_key.upper()} 状态联动监听～", duration_ms=4000)

    def _set_agent_link_option(self, key: str, on: bool) -> None:
        """联动气泡提醒子项开关（开始干活 / 任务完成），立即写入配置。"""
        ag_data = dict(self.cfg.get('agent_link', {}))
        ag_data[key] = bool(on)
        self.cfg.set('agent_link', ag_data)
        self.cfg.save()

    def _rename_character(self) -> None:
        """自定义当前角色的显示名（空输入 = 恢复默认目录名）。"""
        cid = str(self.cfg.get('character', catalog.DEFAULT_CHARACTER))
        current = self.cfg.character_alias(cid) or catalog.character_display_name(cid)
        name, ok = QInputDialog.getText(
            self, '重命名角色', f'给 {cid} 起个名字（留空恢复默认）：', text=current,
        )
        if not ok:
            return
        self.cfg.set_character_alias(cid, name)
        shown = self.cfg.character_alias(cid) or catalog.character_display_name(cid)
        self.show_bubble(f'角色名：{shown}')

    def _request_switch_character(self, character_id: str) -> None:
        """请求切换角色；优先交给 app 做热切换，否则只保存配置。"""
        if self.on_switch_character is not None:
            self.on_switch_character(character_id)
        else:
            self.cfg.set('character', character_id)
            self.cfg.save()

    def set_playback_speed(self, speed: float) -> None:
        """设置动画播放速率并持久化。"""
        self.playback_speed = max(0.1, float(speed))
        self.cfg.set('playback_speed', self.playback_speed)
        self.cfg.save()
        if self.movie is not None and hasattr(self.movie, 'set_playback_speed'):
            self.movie.set_playback_speed(self.playback_speed)

    def set_lock_position(self, on: bool) -> None:
        """锁定位置：开启后桌宠不可拖动（点击互动仍有效）。"""
        self.lock_position = bool(on)
        self.cfg.set('lock_position', self.lock_position)
        self.cfg.save()
        if self.lock_position and self._dragging:
            self._dragging = False
            self._press_global = None
            self._grab_offset = None

    def set_shift_drag(self, on: bool) -> None:
        """按住 SHIFT+左键才能拖动。"""
        self.shift_drag = bool(on)
        self.cfg.set('shift_drag', self.shift_drag)
        self.cfg.save()

    def set_pet_opacity(self, value: int) -> None:
        """桌宠窗口不透明度（10-100）。"""
        self.pet_opacity = max(10, min(100, int(value)))
        self.cfg.set('pet_opacity', self.pet_opacity)
        self.cfg.save()
        self._apply_opacity()

    def _apply_opacity(self) -> None:
        """把 pet_opacity 应用到窗口（值未变时跳过，避免重复系统调用）。"""
        opacity = self.pet_opacity / 100.0
        if self._applied_opacity is None or abs(self._applied_opacity - opacity) >= 0.005:
            self.setWindowOpacity(opacity)
            self._applied_opacity = opacity

    def _request_quit(self) -> None:
        # 不在这里保存当前位置：退出时若正处于自动移动/物理抛掷后的位置，
        # 会把随机终点写进记忆，导致重启后位置变化。手动放置的位置已在
        # 拖动松手/回右下角/缩放时保存过。
        # The context menu is shown with QMenu.exec(), which owns a nested
        # event loop. Quitting the application from inside QAction.triggered
        # can leave that native menu loop alive (notably on macOS), making the
        # command appear to do nothing. End menu tracking first, then quit on
        # the next GUI event-cycle.
        menu = getattr(self, "_active_context_menu", None)
        app = QApplication.instance()
        if app is None:
            return
        if menu is not None:
            menu.close()
            QTimer.singleShot(0, app.quit)
            return
        # Normal context-menu actions are now dispatched only after
        # QMenu.exec() has returned, so there is no nested menu loop left to
        # unwind. Quitting synchronously avoids the first click being consumed
        # before the zero-delay callback can run.
        app.quit()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._speech_bubble.reposition(self.visible_content_rect())
        for listener in tuple(self._position_listeners):
            try:
                listener(self)
            except Exception:
                logging.exception("\u684c\u5ba0\u4f4d\u7f6e\u76d1\u542c\u5668\u6267\u884c\u5931\u8d25")

    def closeEvent(self, event) -> None:  # noqa: N802
        self._pause_activity()
        self._disarm_screen_restore_retry()  # 窗口销毁前摘掉 screenAdded 监听/超时回调
        self._stop_fs_watch()
        manager = getattr(self, "agent_link_manager", None)
        if manager is not None:
            manager.stop()
        if self._input_controller is not None:
            self._input_controller.stop()
            self._input_controller = None
        # 不在这里覆盖记忆位置：避免自动移动/抛掷后的随机终点被存下来。
        self._self_talk_timer.stop()
        self._cancel_animation_gap()
        self._speech_bubble.hide()
        super().closeEvent(event)
