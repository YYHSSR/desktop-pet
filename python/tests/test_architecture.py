"""Regression checks for import boundaries, resource roots and media extension."""
import ast
import importlib
from pathlib import Path

import pytest


@pytest.mark.parametrize("old,new", [
    ("pet.config", "pet.infrastructure.config"),
    ("pet.catalog", "pet.infrastructure.catalog"),
    ("pet.window", "pet.ui.window"),
    ("pet.library", "pet.media.library"),
    ("pet.context_menus.shared", "pet.ui.context_menus.shared"),
])
def test_legacy_import_preserves_module_identity(old, new):
    assert importlib.import_module(old) is importlib.import_module(new)


def test_resource_root_does_not_depend_on_cwd(tmp_path, monkeypatch):
    from pet.infrastructure.paths import resource_root, source_root

    expected = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    assert source_root() == expected
    assert resource_root() == expected


def test_frozen_resource_root(tmp_path, monkeypatch):
    import sys
    from pet.infrastructure.paths import resource_root

    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert resource_root() == tmp_path


def test_custom_media_decoder_is_discovered_and_cached(tmp_path):
    from PySide6.QtWidgets import QApplication
    from pet.media.library import MovieLibrary

    app = QApplication.instance() or QApplication([])
    (tmp_path / "custom.petclip").write_bytes(b"example")
    calls = []

    def factory(path, *, parent):
        clip = object()
        calls.append((path, parent, clip))
        return clip

    library = MovieLibrary(asset_dir=tmp_path, clip_factories={".petclip": factory})
    assert library.names() == ["custom"]
    assert library.media_type == "petclip"
    first = library.movie("custom")
    assert library.movie("custom") is first
    assert calls == [(tmp_path / "custom.petclip", library, first)]
    assert not library._low_warm_timer.isActive()


def test_unsupported_manifest_format_fails_clearly(tmp_path):
    from PySide6.QtWidgets import QApplication
    from pet.media.library import MovieLibrary

    app = QApplication.instance() or QApplication([])
    (tmp_path / "custom.unknown").write_bytes(b"example")
    with pytest.raises(ValueError, match="unknown"):
        MovieLibrary(asset_dir=tmp_path, manifest={"custom": "custom.unknown"})


def test_layers_do_not_import_legacy_facades_or_upper_layers():
    root = Path(__file__).resolve().parents[1] / "pet"
    allowed = {
        "core": {"core"},
        "infrastructure": {"core", "infrastructure"},
        "media": {"core", "infrastructure", "media"},
        "services": {"core", "infrastructure", "media", "services"},
        "ui": {"core", "infrastructure", "media", "services", "ui", "menu_templates"},
        "application": {"core", "infrastructure", "media", "services", "ui", "application"},
    }
    violations = []
    for layer, dependencies in allowed.items():
        assert (root / layer).is_dir()
        for path in (root / layer).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom):
                    assert node.level == 0, f"Use explicit layer imports: {path}:{node.lineno}"
                    if node.module == "pet":
                        assert all(n.name == "__version__" for n in node.names), f"Legacy facade import: {path}:{node.lineno}"
                    names = [node.module or ""]
                elif isinstance(node, ast.Import):
                    names = [item.name for item in node.names]
                else:
                    continue
                for name in names:
                    if name.startswith("pet.") and name.split(".")[1] not in dependencies:
                        violations.append(f"{path.relative_to(root)}:{node.lineno}: {name}")
    assert not violations, "\n".join(violations)
