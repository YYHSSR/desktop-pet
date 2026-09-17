# -*- coding: utf-8 -*-
"""桌宠运行配置的读取、清洗与原子持久化。"""
from __future__ import annotations

from pet.infrastructure.paths import resource_root

import json
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from pet.core.defaults import DEFAULT_CHARACTER, DEFAULT_SCALE


DEFAULT_ANIMATION_GAP_SECONDS = 0.0
DEFAULT_SELF_TALK_MIN_INTERVAL = 20.0
DEFAULT_SELF_TALK_MAX_INTERVAL = 60.0
DEFAULT_SELF_TALK_DURATION_SECONDS = 3.2
DEFAULT_SELF_TALK_TEXTS = [
    # 趣味网络流行梗与打工人元气弹幕
    "疯狂星期四，v我50看看实力！",
    "质疑、理解、成为桌宠。",
    "电子布洛芬已就绪，今天又是元气满满的一天~",
    "人生苦短，先摸会儿鱼不过分吧？",
    "人类又在敲击神奇的小黑块了，看起来好厉害！",
    "报告主人！CPU温度正常，摸鱼雷达已开启！",
    "代码写得真棒，尊嘟假嘟？",
    "本喵/本鲸已启动自动治愈光环，疲惫通通退散~",
    "今天也是没有被bug打倒的一天呢！",
    "坐姿端正，多喝热水，颈椎操做起来~",
    "只要我不看报错，bug就追不上我！",
    "大脑正在加载今日份快乐，请稍候……",
    "CPU 负载 1%，我的可爱度 100%！",
    "咖啡续命成功，战斗力提升500%！",
    "生活不易，桌宠叹气~ 摸摸头就不叹啦！",
    "泰裤辣！今天的主人也在发光呢~",
    "累了就眨眨眼睛，看看窗外的小鸟吧~",
    # 经典陪伴台词
    "好女孩……",
    "好模型……",
    "欧鲸鲸……",
    "今天也要认真工作呀。",
    "再陪你一会儿。",
]
DEFAULT_SELF_TALK_BUBBLE_STYLE = "classic_top"
SELF_TALK_BUBBLE_STYLES = {
    "classic_top", "paper_left", "glass_right", "soft_blue_top", "breath_bubble",
}
DEFAULT_CONTEXT_MENU_APPEARANCE = {
    "theme": "system",
    "density": "standard",
    "corner_radius": 12,
    "ui_font": "system",
    "ui_font_size": 13,
    "translucent": True,
    "opacity": 0.94,
    "light_background": "#ffffff",
    "light_foreground": "#171717",
    "light_hover": "#eeeeee",
    "dark_background": "#252525",
    "dark_foreground": "#f3f3f3",
    "dark_hover": "#3a3a3a",
}
DEFAULT_MENU_EASTER_EGG = {
    "enabled": True,
    "title": "厉害了我的鲸",
    "hint": "请点击",
    "avatar": "assets/big_blue_fat_fish/ojingjing.jpg",
    "image_dir": "assets/big_blue_fat_fish",
}
DEFAULT_QUICK_LAUNCH_APPS = [
    {"name": "默认浏览器", "path": "", "kind": "default_browser"},
]
DEFAULT_QUICK_WEBSITES = [
    {"name": "GitHub 项目页", "url": "https://github.com/MerZlin/dsh-pet-indesktop"},
]


def _clean_color(value, default):
    value = str(value or "").strip()
    if len(value) == 7 and value.startswith("#"):
        try:
            int(value[1:], 16)
            return value.lower()
        except ValueError:
            pass
    return default


def _clean_menu_appearance(value):
    value = value if isinstance(value, dict) else {}
    defaults = DEFAULT_CONTEXT_MENU_APPEARANCE
    theme = str(value.get("theme", "system"))
    density = str(value.get("density", "standard"))
    try:
        radius = int(value.get("corner_radius", 12))
    except (TypeError, ValueError):
        radius = 12
    try:
        font_size = int(value.get("ui_font_size", 13))
    except (TypeError, ValueError):
        font_size = 13
    result = {
        "theme": theme if theme in {"system", "light", "dark"} else "system",
        "density": density if density in {"compact", "standard", "spacious"} else "standard",
        "corner_radius": max(6, min(18, radius)),
        "ui_font": str(value.get("ui_font") or "system")[:80],
        "ui_font_size": max(10, min(18, font_size)),
        "translucent": bool(value.get("translucent", True)),
        "opacity": _float_or_default(value.get("opacity"), 0.94, 0.72, 1.0),
    }
    for key in (
        "light_background", "light_foreground", "light_hover",
        "dark_background", "dark_foreground", "dark_hover",
    ):
        result[key] = _clean_color(value.get(key), defaults[key])
    return result


def _normalize_fun_asset_path(candidate: str, default: str) -> str:
    """绝对路径若指向应用内置 assets 目录，归一化为相对路径。

    旧版设置对话框会把默认相对路径固化成安装目录绝对路径；portable
    目录一移动/自更新即失效。此处在加载时统一还原为 assets/... 相对值。
    """
    candidate = str(candidate or "").strip()
    if not candidate:
        return default
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        return candidate
    assets_root = resource_root() / "assets"
    try:
        rel = path.resolve().relative_to(assets_root.resolve())
        # 统一正斜杠：配置值与 legacy 迁移比较、跨平台一致
        return str(Path("assets") / rel).replace("\\", "/")
    except ValueError:
        return candidate


def _clean_menu_easter_egg(value):
    value = value if isinstance(value, dict) else {}
    defaults = DEFAULT_MENU_EASTER_EGG
    avatar = _normalize_fun_asset_path(
        str(value.get("avatar") or defaults["avatar"]).strip()[:500], defaults["avatar"]
    )
    image_dir = _normalize_fun_asset_path(
        str(value.get("image_dir") or defaults["image_dir"]).strip()[:500], defaults["image_dir"]
    )
    return {
        "enabled": bool(value.get("enabled", defaults["enabled"])),
        "title": str(value.get("title") or defaults["title"]).strip()[:40],
        "hint": str(value.get("hint") or defaults["hint"]).strip()[:20],
        "avatar": avatar,
        "image_dir": image_dir,
    }


def _clean_quick_launch_apps(value):
    if not isinstance(value, list):
        return [dict(item) for item in DEFAULT_QUICK_LAUNCH_APPS]
    cleaned = []
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "application")
        path = str(item.get("path") or "").strip()
        name = str(item.get("name") or "").strip()[:60]
        if kind == "default_browser":
            cleaned.append({"name": name or "默认浏览器", "path": "", "kind": "default_browser"})
        elif path:
            if not name:
                name = Path(path).stem[:60]
            if name:
                cleaned.append({"name": name, "path": path, "kind": "application"})
    return cleaned


def _clean_quick_websites(value):
    if value is None:
        return [dict(item) for item in DEFAULT_QUICK_WEBSITES]
    if not isinstance(value, list):
        return [dict(item) for item in DEFAULT_QUICK_WEBSITES]
    cleaned = []
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("file://")):
            url = "https://" + url
        name = str(item.get("name") or "").strip()[:60]
        cleaned.append({"name": name, "url": url})
    return cleaned


def _default_agent_link_data() -> dict:
    return {
        "antigravity": False,
        "chatgpt": False,
        # 自定义联动 Agent（协议见 docs/AGENT_LINK_PROTOCOL.md §4）：只读监听
        # 用户指定的事件文件，不写外部配置、无需授权弹窗，默认空
        "custom_agents": [],
        # 联动气泡：开始干活提醒（可选，默认关）、任务完成通知（默认开）
        "notify_state": False,
        "notify_done": True,
        # 过程汇报（可选，默认关）：Agent 干活中报「正在读文件/跑命令/改代码…」
        "notify_activity": False,
    }


# 内置联动 Agent 键：custom_agents 的 key 不得与之重复
_AGENT_LINK_BUILTIN_KEYS = ("antigravity", "chatgpt")
# 历史上出现过但已移除的联动键：加载旧配置时一并清理，避免无效开关继续落盘
_REMOVED_AGENT_LINK_KEYS = ("dsh", "claude", "deepseek", "cursor")
# 自定义联动 Agent 条目上限（防配置文件被塞爆）
_CUSTOM_AGENT_MAX = 8


def _clean_custom_agents(raw: Any) -> list[dict]:
    """清洗自定义联动 Agent 列表（agent_link.custom_agents）。

    条目 {key, name, path}：key 为小写标识（不得与内置键/其他条目重复），
    name 为显示名（缺省用 key），path 为事件文件路径（支持 ~，允许暂不存在）。
    非法条目直接丢弃，超出上限截断。"""
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if len(result) >= _CUSTOM_AGENT_MAX:
            break
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", key):
            continue
        if key in _AGENT_LINK_BUILTIN_KEYS or key in _REMOVED_AGENT_LINK_KEYS or key in seen:
            continue
        path = str(item.get("path") or "").strip()[:500]
        if not path:
            continue
        name = str(item.get("name") or "").strip()[:50] or key
        seen.add(key)
        result.append({"key": key, "name": name, "path": path})
    return result


def _clean_agent_link_data(raw: Any) -> dict:
    defaults = _default_agent_link_data()
    if not isinstance(raw, dict):
        return dict(defaults)
    result = dict(defaults)
    # 保留传入的额外合法键（例如 thinking_text, thinking_texts 等）
    result.update(raw)
    # 旧版内置联动已移除；加载旧配置时一并清掉，避免无效开关继续落盘。
    for key in _REMOVED_AGENT_LINK_KEYS:
        result.pop(key, None)
    # 旧版可固定单个 Codex 对话；现在始终自动监听所有本机主任务。
    result.pop("chatgpt_thread_id", None)
    # 音效功能已整体移除：加载旧配置时一并清理，避免无效键继续落盘。
    for key in (
        "sound_enabled", "sound_start_path", "sound_done_path", "sound_error_path",
        "sound_volume", "sound_cooldown_seconds",
        "sound_start_enabled", "sound_done_enabled", "sound_error_enabled",
    ):
        result.pop(key, None)
    result["custom_agents"] = _clean_custom_agents(raw.get("custom_agents"))
    for key in (
        "antigravity", "chatgpt", "notify_state", "notify_done", "notify_activity",
    ):
        if key in raw:
            result[key] = bool(raw[key])
    return result


def _merge_agent_link_data(raw: Any) -> dict:
    return _clean_agent_link_data(raw)


def _default_base():
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA') or Path.home())
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support'
    return Path.home() / '.config'


def _app_dir_name() -> str:
    try:
        from build_variant import VARIANT
        name = str(VARIANT).strip()
        if name and name != 'desktop-pet':
            return f'desktop-pet-{name}'
    except Exception:
        pass
    return 'desktop-pet'


APP_NAME = 'desktop-pet'
APP_DIR_NAME = _app_dir_name()


def _float_or_default(value, default, minimum, maximum):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _bool_or_default(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'true', '1', 'yes', 'on'}:
            return True
        if normalized in {'false', '0', 'no', 'off'}:
            return False
    return bool(default)


def _clean_self_talk_texts(value):
    if not isinstance(value, list):
        return list(DEFAULT_SELF_TALK_TEXTS)
    texts = []
    for item in value:
        text = str(item).strip()
        if text and text not in texts:
            texts.append(text[:120])
    return texts or list(DEFAULT_SELF_TALK_TEXTS)


def _clean_character_profiles(value) -> dict:
    """角色档案：当前先承载 click_talk_bindings，后续可扩展头像/人设字段。"""
    if not isinstance(value, dict):
        return {}
    cleaned = {}
    for character_id, profile in value.items():
        if not isinstance(profile, dict):
            continue
        bindings_raw = profile.get("click_talk_bindings")
        bindings = {}
        if isinstance(bindings_raw, dict):
            for action_id, texts in bindings_raw.items():
                if not isinstance(texts, list):
                    continue
                items = []
                for item in texts:
                    text = str(item).strip()
                    if text and text not in items:
                        items.append(text[:120])
                if items:
                    bindings[str(action_id)] = items
        entry = dict(profile)
        entry["click_talk_bindings"] = bindings
        cleaned[str(character_id)] = entry
    return cleaned


class Config:
    def __init__(self, base=None):
        base = Path(base) if isinstance(base, str) else (base or _default_base())
        self.dir = base / APP_DIR_NAME
        self.path = self.dir / "config.json"
        self._migrate_legacy_config(base)
        self.data = {
            "version": 4,
            "rx": None,
            "ry": None,
            "screen_name": None,
            "facing": "left",
            "scale": DEFAULT_SCALE,
            "on_top": True,
            "show_dock_icon": True,
            "no_move": False,
            "smart_edge_turn": True,
            "smooth_wander": True,
            "character": DEFAULT_CHARACTER,
            "playback_speed": 1.0,
            "animation_gap_seconds": DEFAULT_ANIMATION_GAP_SECONDS,
            "self_talk_enabled": False,
            "self_talk_min_interval": DEFAULT_SELF_TALK_MIN_INTERVAL,
            "self_talk_max_interval": DEFAULT_SELF_TALK_MAX_INTERVAL,
            "self_talk_duration_seconds": DEFAULT_SELF_TALK_DURATION_SECONDS,
            "self_talk_texts": list(DEFAULT_SELF_TALK_TEXTS),
            "self_talk_image_dir": "assets/big_blue_fat_fish",
            "self_talk_bubble_style": DEFAULT_SELF_TALK_BUBBLE_STYLE,
            "lock_position": False,  # 锁定位置：桌宠不可拖动（点击仍有效）
            "shift_drag": False,     # 按住 SHIFT+左键才能拖动
            "pet_opacity": 100,      # 桌宠窗口不透明度 10-100
            "context_menu_template": "modern",
            "context_menu_appearance": dict(DEFAULT_CONTEXT_MENU_APPEARANCE),
            "menu_easter_egg": dict(DEFAULT_MENU_EASTER_EGG),
            "quick_launch_apps": [dict(item) for item in DEFAULT_QUICK_LAUNCH_APPS],
            "quick_websites": [dict(item) for item in DEFAULT_QUICK_WEBSITES],
            "auto_hide_fullscreen": True,  # 全屏应用自动隐藏（Windows）
            "click_show_self_talk": False, # 点击随机显示自定义自言自语
            "music_sing_enabled": False,   # 检测到后台播放音乐时自动播放唱歌动画
            "autostart_wanted": False,     # 用户曾开启过开机自启（用于启动自检：被安全软件清理时提醒）
            "character_aliases": {},  # 角色显示名别名 {角色id: 自定义名}，空名=恢复默认
            "character_profiles": {},  # 角色档案：{角色id: {click_talk_bindings: {动画id: [台词]}}}
            "agent_link": _default_agent_link_data(),
            "system_notifications_enabled": True,
        }
        self._load()
        self._normalize_pet_settings()

    def _migrate_legacy_config(self, base) -> None:
        """旧版配置迁移：升级后首次运行时把旧目录的 config.json 复制到新目录，
        避免用户设置"消失"。仅在新目录尚不存在时执行。"""
        if self.path.exists():
            return
        for legacy_name in ("desktop-pet",):
            legacy = base / legacy_name
            if legacy == self.dir:
                continue
            if (legacy / "config.json").is_file():
                try:
                    self.dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(legacy / "config.json", self.path)
                    return
                except OSError:
                    pass

    def _backup_corrupt_config(self) -> None:
        """把无法解析的配置文件改名备份，避免每次启动重复解析失败。"""
        try:
            stamp = int(time.time() * 1000)
            target = self.path.with_name(f"{self.path.name}.corrupt-{stamp}-{os.getpid()}")
            self.path.replace(target)
            logging.warning("配置损坏，已备份为 %s", target.name)
        except OSError:
            pass

    def _load(self):
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._backup_corrupt_config()
            return
        if not isinstance(raw, dict):
            self._backup_corrupt_config()
            return
        try:
            old_version = int(raw.get("version", 1) or 1)
        except (TypeError, ValueError):
            old_version = 1  # 脏数据（手改/损坏）不得导致启动崩溃
        if old_version < 2:
            raw.pop("scale", None)
        for key in (
            "rx", "ry", "screen_name", "facing", "scale", "on_top", "show_dock_icon", "no_move", "character",
            "playback_speed", "animation_gap_seconds", "self_talk_enabled",
            "smart_edge_turn", "smooth_wander",
            "self_talk_min_interval", "self_talk_max_interval", "self_talk_texts",
            "self_talk_duration_seconds", "self_talk_image_dir",
            "self_talk_bubble_style",
            "context_menu_template",
            "lock_position", "shift_drag", "pet_opacity",
            "context_menu_appearance", "quick_launch_apps", "quick_websites",
            "menu_easter_egg", "auto_hide_fullscreen",
            "click_show_self_talk",
            "autostart_wanted",
            "music_sing_enabled",
            "system_notifications_enabled",
            "character_aliases",
            "character_profiles",
        ):
            if key in raw and raw[key] is not None:
                self.data[key] = raw[key]
        if "agent_link" in raw:
            self.data["agent_link"] = _merge_agent_link_data(raw["agent_link"])
        self.data["version"] = 4

    def _normalize_pet_settings(self):
        self.data["playback_speed"] = _float_or_default(self.data.get("playback_speed"), 1.0, 0.1, 8.0)
        self.data["animation_gap_seconds"] = _float_or_default(
            self.data.get("animation_gap_seconds"), DEFAULT_ANIMATION_GAP_SECONDS, 0.0, 3600.0
        )
        minimum = _float_or_default(
            self.data.get("self_talk_min_interval"), DEFAULT_SELF_TALK_MIN_INTERVAL, 5.0, 3600.0
        )
        maximum = _float_or_default(
            self.data.get("self_talk_max_interval"), DEFAULT_SELF_TALK_MAX_INTERVAL, 5.0, 3600.0
        )
        self.data["self_talk_min_interval"] = min(minimum, maximum)
        self.data["self_talk_max_interval"] = max(minimum, maximum)
        self.data["self_talk_duration_seconds"] = _float_or_default(
            self.data.get("self_talk_duration_seconds"),
            DEFAULT_SELF_TALK_DURATION_SECONDS,
            1.0,
            300.0,
        )
        self.data["self_talk_image_dir"] = str(
            self.data.get("self_talk_image_dir") or ""
        ).strip()[:500]
        self.data["self_talk_enabled"] = bool(self.data.get("self_talk_enabled", False))
        self.data["show_dock_icon"] = bool(self.data.get("show_dock_icon", True))
        self.data["self_talk_texts"] = _clean_self_talk_texts(self.data.get("self_talk_texts"))
        bubble_style = str(self.data.get("self_talk_bubble_style") or "")
        self.data["self_talk_bubble_style"] = (
            bubble_style if bubble_style in SELF_TALK_BUBBLE_STYLES
            else DEFAULT_SELF_TALK_BUBBLE_STYLE
        )
        self.data["context_menu_template"] = "modern"

        self.data["context_menu_appearance"] = _clean_menu_appearance(
            self.data.get("context_menu_appearance")
        )
        self.data["menu_easter_egg"] = _clean_menu_easter_egg(
            self.data.get("menu_easter_egg")
        )
        self.data["quick_launch_apps"] = _clean_quick_launch_apps(
            self.data.get("quick_launch_apps")
        )
        self.data["quick_websites"] = _clean_quick_websites(
            self.data.get("quick_websites")
        )
        self.data["character_profiles"] = _clean_character_profiles(
            self.data.get("character_profiles")
        )
        self.data["smart_edge_turn"] = bool(self.data.get("smart_edge_turn", True))
        self.data["smooth_wander"] = bool(self.data.get("smooth_wander", True))
        self.data["system_notifications_enabled"] = bool(
            self.data.get("system_notifications_enabled", True)
        )

        self.data["agent_link"] = _clean_agent_link_data(self.data.get("agent_link"))

    def get(self, key, default=None):
        return self.data.get(key, default)

    def character_alias(self, character_id: str) -> str:
        """用户自定义的角色显示名；未设置返回空串。"""
        aliases = self.data.get("character_aliases")
        if isinstance(aliases, dict):
            return str(aliases.get(character_id, "") or "").strip()
        return ""

    def set_character_alias(self, character_id: str, name: str) -> None:
        """设置角色显示名别名（最长 24 字符）；空名表示恢复默认。"""
        aliases = self.data.setdefault("character_aliases", {})
        if not isinstance(aliases, dict):
            aliases = {}
            self.data["character_aliases"] = aliases
        name = (name or "").strip()[:24]
        if name:
            aliases[character_id] = name
        else:
            aliases.pop(character_id, None)
        self.save()

    def character_profile(self, character_id: str) -> dict:
        """返回角色档案；不存在时返回空档案。"""
        profiles = self.data.get("character_profiles")
        if isinstance(profiles, dict):
            profile = profiles.get(str(character_id))
            if isinstance(profile, dict):
                return profile
        return {}

    def click_talk_bindings(self, character_id: str) -> dict:
        """返回某角色的点击动画台词绑定：{动画id: [台词, ...]}。"""
        profile = self.character_profile(character_id)
        bindings = profile.get("click_talk_bindings")
        return bindings if isinstance(bindings, dict) else {}

    def click_talk_texts_for(self, character_id: str, action_id: str) -> list[str]:
        """返回某点击动画绑定的台词；未绑定返回空列表。"""
        bindings = self.click_talk_bindings(character_id)
        texts = bindings.get(str(action_id))
        return texts if isinstance(texts, list) else []

    def set_click_talk_bindings(self, character_id: str, bindings: dict) -> None:
        """保存某角色的点击动画台词绑定并立即落盘。"""
        profiles = self.data.setdefault("character_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
            self.data["character_profiles"] = profiles
        profile = profiles.setdefault(str(character_id), {})
        if not isinstance(profile, dict):
            profile = {}
            profiles[str(character_id)] = profile
        profile["click_talk_bindings"] = bindings
        self.data["character_profiles"] = _clean_character_profiles(profiles)
        self.save()

    def set(self, key, value):
        self.data[key] = value
        if key in {
            "playback_speed", "animation_gap_seconds", "self_talk_enabled",
            "self_talk_min_interval", "self_talk_max_interval", "self_talk_texts",
            "self_talk_duration_seconds", "self_talk_image_dir",
            "self_talk_bubble_style",
            "context_menu_appearance", "quick_launch_apps", "quick_websites",
            "menu_easter_egg",
            "agent_link",
            "character_profiles",
        }:
            self._normalize_pet_settings()

    def save(self) -> bool:
        """把配置写入磁盘；成功返回 True，失败返回 False（并记录 warning）。"""
        try:
            self._normalize_pet_settings()
            self.dir.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            temp.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, self.path)
        except OSError as exc:
            logging.warning("保存配置失败: %s (%s)", self.path, exc)
            return False
        return True


class ProviderConfig:
    def __init__(self, provider_id: str = "openai-main", **kwargs):
        self.provider_id = provider_id
        for k, v in kwargs.items():
            setattr(self, k, v)

    @classmethod
    def from_dict(cls, pid: str, raw: dict | None = None) -> "ProviderConfig":
        return cls(pid, **(raw or {}))
