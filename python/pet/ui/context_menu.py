# -*- coding: utf-8 -*-
"""Single maintained modern context-menu dispatcher."""
from __future__ import annotations

import json
from importlib import resources

from PySide6.QtWidgets import QMenu

from pet.ui.context_menus import build_modern_menu
from pet.ui.context_menus.icons import vector_menu_icon
from pet.ui.context_menus.menu_styles import (
    apply_modern_menu_style,
    install_modern_check_indicators,
)
from pet.ui.context_menus.menu_styles.common import install_responsive_menu_style, install_stay_open_interaction

TEMPLATE_IDS = ("modern",)

# 内置兜底模板：打包缺资源（如某个平台漏 --add-data）时菜单仍可用，
# 结构与 pet/menu_templates/*.json 保持一致。
_FALLBACK_TEMPLATES = {
    "modern": {
        "id": "modern",
        "name": "现代菜单",
        "groups": [
            {"id": "interaction", "items": ["ojingjing", "chat"]},
            {"id": "playback", "items": ["animations_hub", "character"]},
            {"id": "functions", "items": ["playback_speed", "size", "return_corner", "no_move", "on_top", "autostart"]},
            {"id": "tools", "items": ["agent_launch"]},
            {"id": "settings", "items": ["modern_settings"]},
            {"id": "exit", "items": ["quit"]},
        ],
    },
    "legacy": {
        "id": "legacy",
        "name": "旧版菜单（已合并至现代菜单）",
        "groups": [],
    },
}


def load_menu_template(template_id: str = "modern") -> dict:
    template_id = str(template_id or "modern").strip().lower()
    if template_id not in _FALLBACK_TEMPLATES:
        template_id = "modern"
    path = resources.files("pet.menu_templates").joinpath(f"{template_id}.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        # 打包资源缺失或已移除时回退内置模板，保证右键菜单始终可用
        return dict(_FALLBACK_TEMPLATES[template_id])
    if data.get("id") != template_id or not isinstance(data.get("groups"), list):
        return dict(_FALLBACK_TEMPLATES.get(template_id, _FALLBACK_TEMPLATES["modern"]))
    return data



def populate_context_menu(menu: QMenu, pet) -> None:
    cfg = getattr(pet, "cfg", None)
    template = load_menu_template("modern")
    apply_modern_menu_style(menu, cfg.get("context_menu_appearance", {}) if cfg is not None else {})
    build_modern_menu(menu, pet, template)
    install_modern_check_indicators(menu)
    install_responsive_menu_style(menu)
    install_stay_open_interaction(menu)



__all__ = [
    "TEMPLATE_IDS",
    "load_menu_template",
    "populate_context_menu",
    "vector_menu_icon",
]
