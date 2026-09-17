# -*- coding: utf-8 -*-
"""
Media library —— 多形象，自动识别 webm / gif。

支持按角色 ID 加载不同形象：
- 默认从内置 assets/characters/<character_id>/videos/ 加载
- 也支持外部扩展目录（exe 同目录/用户数据目录下的 characters/<id>/videos）
- 如果目录里是 *.webm 则用 WebMClip；如果是 *.gif 则用 GifClip

对外保持与窗口层一致的形状：
- movie(name) -> clip object
- movies() -> name -> clip mapping
- frames(name) / duration(name)（秒）

WebMClip 基于 imageio-ffmpeg 解码 640×360 透明 webm（RGBA）。
GifClip 基于 QMovie 播放透明 GIF（兼容旧 GIF 路线）。
"""

from __future__ import annotations

from pet.media.gif_clip import GifClip, PLAYBACK_SPEED

from concurrent.futures import ThreadPoolExecutor
import random
import threading
import time
from pathlib import Path
from typing import Mapping
from pet.media.contracts import ClipFactory

from PySide6.QtCore import QObject, QTimer

from pet.infrastructure import catalog
from pet.media.webm_clip import WebMClip


class MovieLibrary(QObject):
    """素材库：加载指定形象的 webm 或 gif 动画。"""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        character_id: str | None = None,
        asset_dir: Path | str | None = None,
        manifest: Mapping[str, str] | None = None,
        clip_factories: Mapping[str, ClipFactory] | None = None,
    ) -> None:
        super().__init__(parent)
        self._clip_factories: dict[str, ClipFactory] = {".webm": WebMClip, ".gif": GifClip}
        for suffix, factory in (clip_factories or {}).items():
            suffix = str(suffix).lower()
            if not suffix.startswith(".") or len(suffix) < 2 or not callable(factory):
                raise ValueError(f"Invalid clip factory: {suffix!r}")
            self._clip_factories[suffix] = factory
        self.character_id = character_id or catalog.DEFAULT_CHARACTER
        if asset_dir is not None:
            self._asset_dir = Path(asset_dir)
        else:
            self._asset_dir = catalog.resolve_character_video_dir(self.character_id)
        self._manifest = None if manifest is None else dict(manifest)
        self.manifest = catalog.load_character_manifest(self.character_id, self._asset_dir)
        self.folder_map: dict[str, str] = {}
        self.folder_files: dict[str, list[str]] = {}
        self._movies: dict[str, object] = {}
        self._paths: dict[str, Path] = {}
        # 随机动作池延迟预热：启动后 2s 再以 1 个 worker 慢慢补，避免多开时
        # ffmpeg 进程洪峰；只在高优先级（idle/turn/click/drag/move）就绪后触发。
        self._low_warm_timer = QTimer(self)
        self._low_warm_timer.setSingleShot(True)
        self._low_warm_timer.setInterval(2000)
        self._low_warm_timer.timeout.connect(self._warm_low_priority_background)
        # 隐藏即暂停：桌宠不可见时预热没有任何可见收益，停掉定时器并
        # 让在飞的预热线程尽快退出（低功耗铁律）。
        self._warm_paused = False
        self._low_first_frames_done = False  # 低优先级池首帧预热是否完整跑完
        self.media_type: str = 'webm'
        self.no_mirror: set[str] = self._load_no_mirror()

        self._load_all()

    def _load_no_mirror(self) -> set[str]:
        '''加载 text_clips.json：内含文字的动画在朝向翻转时不镜像（防文字反显）。'''
        import json
        path = self._asset_dir / 'text_clips.json'
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return set()
        names = data.get('no_mirror', [])
        return {str(n) for n in names} if isinstance(names, list) else set()

    def _load_all(self) -> None:
        if self._manifest is None:
            # 自动扫描该形象目录下的 webm 或 gif，支持不同角色有不同动作集
            if not self._asset_dir.is_dir():
                raise FileNotFoundError(
                    f"角色素材目录不存在: {self._asset_dir}（character_id={self.character_id}）"
                )
            # Registry order preserves WebM/GIF precedence for duplicate names.
            candidates = sorted(self._asset_dir.rglob("*"))
            files = [path for suffix in self._clip_factories
                     for path in candidates
                     if path.is_file() and path.suffix.lower() == suffix]
            if not files:
                supported = "/".join(s.lstrip(".") for s in self._clip_factories)
                raise FileNotFoundError(f"角色素材目录中没有 {supported} 文件: {self._asset_dir}")
            media_types = {path.suffix.lower().lstrip(".") for path in files}
            self.media_type = next(iter(media_types)) if len(media_types) == 1 else "mixed"
            self._manifest = {}
            self.folder_map = {}
            self.folder_files = {}
            for f in files:
                rel = f.relative_to(self._asset_dir)
                name = f.stem
                self._manifest[name] = rel.as_posix()
                folder = rel.parts[0].lower() if len(rel.parts) > 1 else ''
                self.folder_map[name] = folder
                self.folder_files.setdefault(folder, []).append(name)

        missing: list[str] = []
        resolved: dict[str, Path] = {}
        for name, fname in self._manifest.items():
            path = self._asset_dir / fname
            if not path.exists():
                missing.append(f"{name}: {path}")
                continue
            if path.suffix.lower() not in self._clip_factories:
                raise ValueError(f"Unsupported animation format: {path.suffix} ({path})")
            resolved[name] = path

        if missing:
            raise FileNotFoundError("缺少素材文件: " + ", ".join(missing))

        self._paths = resolved

        # 高优先级 clip 必须在主线程创建（QObject 线程亲和），再交给后台线程预热；
        # 低优先级由 QTimer 在主线程触发 _warm_low_priority_background 创建。
        high, _ = self._priority_names()
        for name in high:
            self.movie(name)

        # 预热线程由应用层在 UI 就绪后统一调度（schedule_high_priority_warm /
        # schedule_low_priority_warm），避免库构造时在测试/非事件循环环境里
        # 凭空拉起 ffmpeg 预热线程。

    def _priority_names(self) -> tuple[list[str], list[str]]:
        """默认优先级：高频交互动画立刻预热，随机动作池延迟预热。

        高优先级来自状态机必然/高频路径：
          idle（启动即播）、turn（10% + 间隔期）、click（点击）、
          drag（拖拽）、move（20% + 手动触发）。
        低优先级 = 随机动作池（42 个，单个命中率低）。
        """
        names = list(self._manifest)
        cats = catalog.build_categories(
            names,
            None,
            self.folder_map,
            self.folder_files,
        )
        # 点击回应优先级最高：首次点击最怕同步 ffmpeg 解码（实测可达 600ms+），
        # 先预热点击动画，避免用户刚启动就点击时卡顿。
        high = list(dict.fromkeys(
            [*(cats['clicks'] or []), *(cats['idles'] or []),
             *(cats['turns'] or []), *(cats['moves'] or [])]
            + ([cats['drag']] if cats.get('drag') else [])
        ))
        low = [n for n in cats.get('acts', []) if n not in high]
        return high, low

    def _warm_objects(self, clips: list, workers: int) -> None:
        """预热已创建的 clip 对象：元数据 + 首帧 QImage（线程安全）。"""
        if not clips:
            return
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(lambda clip: clip.warm_meta(), clips))
            if self._warm_paused:
                return  # 窗口已隐藏：首帧预热（每段拉起 ffmpeg）留到恢复后按需进行
            # 预解码各动画首帧（QImage 线程安全），首次播放时零阻塞切换，
            # 避免点击 Q 弹瞬间同步 ffmpeg 解码造成卡顿与旧动画帧残留。
            list(ex.map(
                lambda clip: getattr(clip, 'warm_first_frame', lambda: None)(),
                clips,
            ))

    def pause_warm(self) -> None:
        """窗口隐藏时暂停预热：停掉延迟定时器，在飞线程尽快收尾。"""
        self._warm_paused = True
        self._low_warm_timer.stop()

    def resume_warm(self) -> None:
        """窗口恢复显示时补齐预热：低优先级池未建完或首帧未预热完则重新排期。"""
        self._warm_paused = False
        try:
            _, low = self._priority_names()
            incomplete = any(name not in self._movies for name in low) or not self._low_first_frames_done
            if incomplete and not self._low_warm_timer.isActive():
                self._low_warm_timer.start()
        except Exception:
            pass

    def _warm_clips(self, names: list[str], workers: int) -> None:
        """预热指定动画（调用方需保证 clip 已在主线程创建）。"""
        if not names:
            return
        self._warm_objects([self.movie(name) for name in names], workers)

    def _warm_all_meta_background(self) -> None:
        try:
            # 高优先级（尤其点击回应）尽快开始；保留极短随机错峰降低多开
            # 同时拉起 ffmpeg 的峰值，但不再让首次点击等 0.5s 预热。
            time.sleep(random.uniform(0, 0.05))
            # 并发控制在 3：每个 webm 首帧预热都会拉起一个 ffmpeg 子进程，
            # 并发过高会形成进程洪峰，提高杀毒软件拦截/误报概率。
            high, _ = self._priority_names()
            self._warm_clips(high, workers=min(3, len(high)))
        except Exception:
            # 预热失败不致命，后续按需读取时会再尝试
            pass

    def _warm_low_priority_background(self) -> None:
        """启动后延迟补全随机动作池：1 个 worker，避免多开启动 CPU 峰值。

        注意：QTimer 回调运行在主线程，clip 对象必须在主线程创建；
        真正耗时的 ffmpeg 预热放到独立 daemon 线程，避免阻塞事件循环。
        """
        try:
            _, low = self._priority_names()
            if not low:
                return
            clips = [self.movie(name) for name in low]  # 主线程创建 QObject

            def run() -> None:
                try:
                    self._warm_objects(clips, 1)
                finally:
                    # 记录首帧预热是否完整跑完：中途 pause 会跳过首帧阶段，
                    # resume_warm 据此决定是否重新排期（避免"clip 已建但首帧永缺"）。
                    self._low_first_frames_done = not self._warm_paused

            threading.Thread(target=run, daemon=True).start()
        except Exception:
            # 预热失败不致命，后续按需读取时会再尝试
            pass

    def schedule_high_priority_warm(self) -> None:
        """应用层调用：UI 就绪后后台预热高优先级动画。

        加入 0~0.5s 随机错峰，多开同时启动时避免 ffmpeg 进程洪峰。
        """
        if not self._paths:
            return
        threading.Thread(target=self._warm_all_meta_background, daemon=True).start()

    def schedule_low_priority_warm(self) -> None:
        """应用层调用：UI 就绪后延迟补全随机动作池预热（2s 后 1 worker）。"""
        self._low_warm_timer.start()

    def movie(self, name: str):
        """按需创建并缓存 clip（懒加载）：启动时只创建实际用到/预热的动画。

        这样多开实例不会在启动瞬间一次性 new 出 91 个播放器对象；
        随机动作池由 _warm_low_priority_background 在启动后 2s 补全。
        """
        if name not in self._movies:
            path = self._paths[name]
            factory = self._clip_factories[path.suffix.lower()]
            self._movies[name] = factory(path, parent=self)
        return self._movies[name]

    def clip_path(self, name: str) -> Path | None:
        """只取素材路径、不创建 clip——供工作线程解码缩略图用。

        movie() 会构造带 QTimer 的 WebMClip（GUI 线程亲和对象），
        在 QThreadPool worker 里调用会违反 Qt 线程规则；缩略图只需要文件路径。
        """
        return self._paths.get(name)

    def frames(self, name: str) -> int:
        return self.movie(name).frameCount()

    def duration(self, name: str) -> float:
        return self.movie(name).duration()

    def names(self) -> list[str]:
        return list(self._paths)

    def movies(self) -> dict[str, object]:
        """当前已创建（已加载）的 clip 映射，供窗口层连接信号。"""
        return dict(self._movies)
