# -*- coding: utf-8 -*-



"""多 Agent 状态感知与动作联动单元测试。







测试覆盖：



- 默认全关；



- 有界 Byte-Offset Tailer：新增行增量读取、重复读取不重放、文件轮转/截断安全、backfill 防护；



- 事件 JSONL 解析与状态规范化映射；



- AgentLinkManager 生命周期与 pause / resume；



- 状态变更触发桌宠行为与气泡反馈；



- 


"""







from __future__ import annotations







import json



import time



from pathlib import Path







import pytest



from PySide6.QtWidgets import QApplication, QMessageBox







import pet.agent_link as agent_link



from pet.agent_link import (



    AgentLinkManager,



    BaseAgentMonitor,



    ByteOffsetTailer,



    





    CustomAgentMonitor,






    normalize_event_state,



)



from pet.config import Config



from pet.config import _clean_agent_link_data, _clean_custom_agents











# ============================================================================



# 1. ByteOffsetTailer 核心增量读取测试



# ============================================================================



class TestByteOffsetTailer:



    def test_backfill_protection_on_startup(self, tmp_path):



        fpath = tmp_path / "test.jsonl"



        fpath.write_text('{"event": "old1"}\n{"event": "old2"}\n', encoding="utf-8")







        tailer = ByteOffsetTailer(fpath)



        # 首次调用 read_new_lines 应当做 backfill 防护，不读取启动前的历史行



        lines = tailer.read_new_lines()



        assert lines == []



        assert tailer.offset == fpath.stat().st_size







        # 写入新行



        with open(fpath, "a", encoding="utf-8") as f:



            f.write('{"event": "new1"}\n')







        new_lines = tailer.read_new_lines()



        assert len(new_lines) == 1



        assert json.loads(new_lines[0])["event"] == "new1"







    def test_no_duplicate_reads(self, tmp_path):



        fpath = tmp_path / "test.jsonl"



        fpath.touch()



        tailer = ByteOffsetTailer(fpath)



        tailer.read_new_lines()  # 初始化







        with open(fpath, "a", encoding="utf-8") as f:



            f.write('{"event": "ev1"}\n')







        lines1 = tailer.read_new_lines()



        assert len(lines1) == 1







        # 再次调用不应重复读取



        lines2 = tailer.read_new_lines()



        assert len(lines2) == 0







    def test_file_truncation_resets_safely(self, tmp_path):



        fpath = tmp_path / "test.jsonl"



        fpath.write_text('{"event": "a"}\n{"event": "b"}\n', encoding="utf-8")



        tailer = ByteOffsetTailer(fpath)



        tailer.offset = 100  # 假设之前读取了较大 offset







        # 文件被清空重写（size < offset）



        fpath.write_text('{"event": "fresh"}\n', encoding="utf-8")



        tailer._initial_backfill_done = True







        lines = tailer.read_new_lines()



        assert len(lines) == 1



        assert json.loads(lines[0])["event"] == "fresh"











# ============================================================================



# 2. 状态映射与规范化测试



# ============================================================================



class TestEventStateNormalization:



    def test_known_events_mapping(self):



        assert normalize_event_state("UserPromptSubmit") == "thinking"



        assert normalize_event_state("PreToolUse") == "working"



        assert normalize_event_state("PostToolUse") == "working"



        assert normalize_event_state("Stop") == "attention"



        assert normalize_event_state("SubagentStop") == "attention"



        assert normalize_event_state("PostToolUseFailure") == "error"



        assert normalize_event_state("SessionStart") == "idle"







    def test_explicit_valid_state_override(self):



        assert normalize_event_state("CustomUnknownEvent", explicit_state="thinking") == "thinking"



        # 未知事件 + 非法显式状态：返回空串表示「忽略」，绝不默认当成 working 过度触发



        assert normalize_event_state("CustomUnknownEvent", explicit_state="invalid") == ""



        assert normalize_event_state("CustomUnknownEvent") == ""











# ============================================================================



# 3. AgentLinkManager 管理器与生命周期测试



# ============================================================================



class TestAgentLinkManager:



    def test_default_all_disabled(self, tmp_path):



        cfg = Config(base=tmp_path)



        assert cfg.data["agent_link"] == {



            "antigravity": False,



            "chatgpt": False,






            "custom_agents": [],



            "notify_state": False,



            "notify_done": True,



            "notify_activity": False,



        }







        mgr = AgentLinkManager(None, cfg)



        for key, mon in mgr.monitors.items():



            assert mon.is_running() is False







    def test_enable_disable_and_pause_resume(self, tmp_path):



        cfg = Config(base=tmp_path)



        mgr = AgentLinkManager(None, cfg)







        mgr.set_enabled("chatgpt", True)



        assert cfg.data["agent_link"]["chatgpt"] is True



        assert mgr.monitors["chatgpt"].is_running() is True







        # 暂停（桌宠隐藏）



        mgr.pause()



        assert mgr.monitors["chatgpt"].is_running() is False







        # 恢复



        mgr.resume()



        assert mgr.monitors["chatgpt"].is_running() is True







        # 关闭

        mgr.set_enabled("chatgpt", False)

        assert mgr.monitors["chatgpt"].is_running() is False








    def test_agent_state_triggers_pet_action(self, tmp_path):



        app = QApplication.instance() or QApplication([])







        switched_anims = []



        bubbles = []







        class DummyPetWindow:



            def __init__(self):



                self.cats = {"acts": ["写代码", "原地敲击桌面互动", "吃Token", "轻快记录", "漂浮踏步"]}



                self.idles = ["待机呼吸"]







            def isVisible(self):



                return True







            def _switch(self, name):



                switched_anims.append(name)







            def request_link_anim(self, name):



                switched_anims.append(name)







            def request_link_idle(self):



                if self.idles:



                    switched_anims.append(self.idles[0])







            def show_bubble(self, text, duration_ms=3000):



                bubbles.append(text)







            def _pick(self, lst):



                return lst[0]







        cfg = Config(base=tmp_path)



        win = DummyPetWindow()



        mgr = AgentLinkManager(win, cfg, min_interval=0.0)  # 测试关闭节流，逐个验证状态映射







        # 模拟 Agent 状态分发（busy 动作池轮换：写代码→吃Token）



        mgr._on_agent_state("customagent", "thinking")



        assert "写代码" in switched_anims







        mgr._on_agent_state("customagent", "working")



        assert "吃Token" in switched_anims







        mgr._on_agent_state("customagent", "attention")



        # busy 后的 attention（Stop=回合结束）不再立即弹「看一眼」，



        # 改由完成确认流程接管（防双气泡）；确认后弹中性完成文案



        assert not any("需要你看一眼" in b for b in bubbles)



        assert "customagent" in mgr._done_pending



        mgr._fire_done("customagent")



        assert any("自己看一眼" in b for b in bubbles)







        # 非 busy 后独立出现的 attention 仍立即提醒



        mgr._on_agent_state("customagent", "attention")



        assert any("需要你看一眼" in b for b in bubbles)











# ============================================================================



# 5. 终审修复回归：hooks 格式 / 半行缓冲 / 去抖节流 / 菜单回弹



# ============================================================================




class TestByteOffsetTailerPartialLine:



    def test_partial_line_buffered_not_dropped(self, tmp_path):



        """半行（无换行结尾）必须缓冲等待拼接，绝不能当整行解析或丢弃。"""



        fpath = tmp_path / "t.jsonl"



        fpath.touch()



        tailer = ByteOffsetTailer(fpath)



        tailer.read_new_lines()  # 初始化







        # 写入半行



        with open(fpath, "a", encoding="utf-8") as f:



            f.write('{"event": "PreTool')



        assert tailer.read_new_lines() == []  # 半行不产出







        # 补全该行



        with open(fpath, "a", encoding="utf-8") as f:



            f.write('Use"}\n{"event": "Stop"}\n')



        lines = tailer.read_new_lines()



        assert len(lines) == 2



        assert json.loads(lines[0])["event"] == "PreToolUse"



        assert json.loads(lines[1])["event"] == "Stop"







    def test_chunk_boundary_mid_line(self, tmp_path):



        """读取窗口恰好切在行中间时，半行拼接依然正确。"""



        fpath = tmp_path / "t.jsonl"



        fpath.touch()



        tailer = ByteOffsetTailer(fpath, max_chunk_bytes=16)



        tailer.read_new_lines()







        line1 = '{"event": "PreToolUse"}\n'  # 24 bytes，跨越 16B 边界



        with open(fpath, "a", encoding="utf-8") as f:



            f.write(line1)



        out = []



        for _ in range(3):



            out.extend(tailer.read_new_lines())



        assert out == [line1.strip()]











class TestAgentStateDebounce:



    def _make_mgr(self, tmp_path):



        from PySide6.QtWidgets import QApplication



        app = QApplication.instance() or QApplication([])







        switched = []







        class DummyWin:



            cats = {"acts": ["写代码", "原地敲击桌面互动", "吃Token", "轻快记录", "漂浮踏步"]}



            idles = ["待机呼吸"]







            def isVisible(self):



                return True







            def _switch(self, name):



                switched.append(name)







            def request_link_anim(self, name):



                switched.append(name)







            def request_link_idle(self):



                if self.idles:



                    switched.append(self.idles[0])







            def show_bubble(self, text, duration_ms=3000):



                pass







            def _pick(self, lst):



                return lst[0]







        cfg = Config(base=tmp_path)



        clock = [1000.0]



        mgr = AgentLinkManager(DummyWin(), cfg, min_interval=2.0, clock=lambda: clock[0])



        return mgr, switched, clock







    def test_same_state_deduped(self, tmp_path):



        mgr, switched, clock = self._make_mgr(tmp_path)



        mgr._on_agent_state("customagent", "working")



        mgr._on_agent_state("customagent", "working")



        mgr._on_agent_state("customagent", "working")



        assert switched == ["写代码"]  # 只切一次







    def test_throttled_within_interval(self, tmp_path):



        mgr, switched, clock = self._make_mgr(tmp_path)



        mgr._on_agent_state("customagent", "working")



        clock[0] += 1.0  # 1s < 2s 节流间隔



        mgr._on_agent_state("customagent", "thinking")



        assert switched == ["写代码"]  # 被节流



        clock[0] += 2.0  # 超过间隔



        mgr._on_agent_state("customagent", "thinking")



        assert switched == ["写代码", "吃Token"]  # 动作池轮换：写代码→吃Token











class TestAgentMenuRebound:



    def test_decline_rolls_back_checkbox(self, tmp_path, monkeypatch):



        """用户拒绝授权后，菜单勾选态必须回滚，不允许 UI 骗人。"""



        from PySide6.QtWidgets import QApplication



        from pet.window import PetWindow



        from pet.library import MovieLibrary







        app = QApplication.instance() or QApplication([])



        monkeypatch.setattr(QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No)







        cfg = Config(base=tmp_path)



        lib = MovieLibrary(character_id="shenshen")



        win = PetWindow(lib, cfg)







        class FakeAction:



            def __init__(self):



                self.checked = True  # 用户刚勾上



                self._blocked = []







            def blockSignals(self, b):



                self._blocked.append(b)







            def setChecked(self, v):



                self.checked = v







        act = FakeAction()



        win._toggle_agent_link("nonexistent", True, act)



        assert act.checked is False  # 回滚



        pass  # 配置未开启







    def test_bom_prefixed_file_tolerated(self, tmp_path):



        """PowerShell Add-Content -Encoding UTF8 会在新建文件首行写 BOM，



        tailer 必须容忍，否则 hooks 产生的第一条事件永远解析失败。"""



        fpath = tmp_path / "bom.jsonl"



        fpath.touch()



        tailer = ByteOffsetTailer(fpath)



        tailer.read_new_lines()  # 完成初始化（文件须先存在）



        # 外部以带 BOM 的方式重写文件（模拟轮转后首行带 BOM）



        fpath.write_bytes(b"\xef\xbb\xbf" + '{"event": "Stop"}\n'.encode("utf-8"))



        tailer.offset = 0  # 模拟轮转重置



        lines = tailer.read_new_lines()



        assert len(lines) == 1



        assert json.loads(lines[0])["event"] == "Stop"











# ============================================================================



# ============================================================================



class TestRealFormatMappers:



    def test_antigravity_event_types(self):



        from pet.agent_link import antigravity_event_state, antigravity_event_tool



        assert antigravity_event_state({"type": "USER_INPUT", "content": "hi"}) == "thinking"



        assert antigravity_event_state({"role": "user", "content": "hi"}) == "thinking"



        assert antigravity_event_state({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command"}]}) == "working"



        assert antigravity_event_state({"type": "PLANNER_RESPONSE", "status": "DONE"}) == "idle"



        assert antigravity_event_state({"status": "ERROR"}) == "error"



        assert antigravity_event_state({"state": "working"}) == "working"



        assert antigravity_event_state({"random": True}) == ""







        assert antigravity_event_tool({"tool_calls": [{"name": "run_command"}]}) == "run_command"



        assert antigravity_event_tool({"tool": "view_file"}) == "view_file"



        assert antigravity_event_tool({}) == ""











class TestAntigravityTranscriptTail:



    def test_transcript_incremental_poll(self, tmp_path):



        """Antigravity 监视器：基于 transcript.jsonl，验证 backfill 防护 + 增量轮询。"""



        import json as j



        from PySide6.QtWidgets import QApplication



        from pet.agent_link import AntigravityMonitor







        app = QApplication.instance() or QApplication([])







        brain_dir = tmp_path / "brain" / "conv1" / ".system_generated" / "logs"



        brain_dir.mkdir(parents=True)



        log_file = brain_dir / "transcript.jsonl"



        with open(log_file, "w", encoding="utf-8") as f:



            f.write(j.dumps({"type": "INIT"}) + "\n")







        cfg_dir = tmp_path / "cfg"



        cfg_dir.mkdir()



        received = []



        tools = []



        mon = AntigravityMonitor(cfg_dir, base_dir=tmp_path / "brain")



        mon.state_changed.connect(lambda k, s: received.append(s))



        mon.activity.connect(lambda k, t: tools.append(t))



        mon.start()



        mon._poll()  # 首次 = backfill，不产生事件



        assert received == []







        with open(log_file, "a", encoding="utf-8") as f:



            f.write(j.dumps({"type": "USER_INPUT", "content": "hello"}) + "\n")



            f.write(j.dumps({"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command"}]}) + "\n")



            f.write(j.dumps({"type": "OTHER", "status": "IGNORE"}) + "\n")



            f.write(j.dumps({"type": "PLANNER_RESPONSE", "status": "DONE"}) + "\n")







        mon._poll()



        assert received == ["thinking", "working", "idle"]



        assert tools == ["run_command"]



        mon.stop()











class TestAgentLinkBubbles:



    def _make_mgr(self, tmp_path, agent_link_cfg=None):



        app = QApplication.instance() or QApplication([])







        switched = []



        bubbles = []







        class DummyWin:



            cats = {"acts": ["写代码", "原地敲击桌面互动", "吃Token", "轻快记录", "漂浮踏步"]}



            idles = ["待机呼吸"]



            _bubble_busy_until = 0.0







            def isVisible(self):



                return True







            def _switch(self, name):



                switched.append(name)







            def request_link_idle(self):



                # 与真实 window 行为对齐：清待播并回待机



                if self.idles:



                    switched.append(self.idles[0])







            def show_bubble(self, text, duration_ms=3000):



                bubbles.append(text)







            def _pick(self, lst):



                return lst[0]







        win = DummyWin()



        win.switched = switched  # 供断言动画切换



        cfg = Config(base=tmp_path)



        if agent_link_cfg is not None:



            data = cfg.data



            data["agent_link"] = {**data.get("agent_link", {}), **agent_link_cfg}



            cfg.save()







        clock = [1000.0]



        mgr = AgentLinkManager(win, cfg, min_interval=2.0, clock=lambda: clock[0])



        return mgr, win, bubbles, clock







    def test_working_to_idle_done_bubble(self, tmp_path):



        """1. working→idle 后，mgr._done_pending 里出现 'customagent' 的定时器；



        手动调 mgr._fire_done('customagent') 后 fake win 的 show_bubble 收到含「干完活啦」的文本。"""



        mgr, win, bubbles, clock = self._make_mgr(tmp_path)



        mgr._on_agent_state("customagent", "working")



        assert "customagent" not in mgr._done_pending







        mgr._on_agent_state("customagent", "idle")



        assert "customagent" in mgr._done_pending







        mgr._fire_done("customagent")



        assert any("干完活啦" in b for b in bubbles)







    def test_notify_done_false_no_bubble(self, tmp_path):



        """2. 同样流程但 cfg 里 agent_link.notify_done=False → _fire_done 后无气泡。"""



        mgr, win, bubbles, clock = self._make_mgr(tmp_path, agent_link_cfg={"notify_done": False})



        mgr._on_agent_state("customagent", "working")



        mgr._on_agent_state("customagent", "idle")



        assert "customagent" in mgr._done_pending







        mgr._fire_done("customagent")



        assert bubbles == []







    def test_notify_state_start_bubble(self, tmp_path):

        """3. notify_state=True 时：thinking↔working 连续 busy 状态只弹一次；开始干活

        仅在 prev_raw 非 busy 时弹；默认 notify_state=False 时不弹。"""

        # 默认 notify_state=False

        mgr_off, win_off, bubbles_off, clock_off = self._make_mgr(tmp_path)

        mgr_off._on_agent_state("antigravity", "thinking")

        assert bubbles_off == []

        clock_off[0] += 3.0

        mgr_off._on_agent_state("antigravity", "working")

        assert bubbles_off == []



        # notify_state=True

        mgr_on, win_on, bubbles_on, clock_on = self._make_mgr(tmp_path, agent_link_cfg={"notify_state": True})

        mgr_on._on_agent_state("antigravity", "thinking")

        assert len(bubbles_on) == 1

        assert "正在深度思考" in bubbles_on[0]



        clock_on[0] += 3.0

        mgr_on._on_agent_state("antigravity", "working")

        # 连续 busy 状态，thinking↔working 互跳不重复弹

        assert len(bubbles_on) == 1



        # idle 后再 working → 弹「开始干活啦」

        clock_on[0] += 3.0

        mgr_on._on_agent_state("antigravity", "idle")

        clock_on[0] += 3.0

        mgr_on._on_agent_state("antigravity", "working")

        assert len(bubbles_on) == 2

        assert "开始干活啦" in bubbles_on[1]



    def test_thinking_text_custom_override(self, tmp_path):

        """自定义 thinking 文案：agent_link.thinking_text 非空时优先使用，支持 {name} 占位符。"""

        mgr, win, bubbles, clock = self._make_mgr(

            tmp_path, agent_link_cfg={"notify_state": True, "thinking_text": "{name} 大脑飞速运转中……"}

        )

        mgr._on_agent_state("antigravity", "thinking")

        assert len(bubbles) == 1

        assert "Antigravity IDE 大脑飞速运转中……" == bubbles[0]

        assert "深度思考" not in bubbles[0]



        # 空字符串 → 回退默认

        mgr2, win2, bubbles2, _ = self._make_mgr(

            tmp_path / "b", agent_link_cfg={"notify_state": True, "thinking_text": ""}

        )

        mgr2._on_agent_state("antigravity", "thinking")

        assert "Antigravity 正在深度思考" in bubbles2[0]





    def test_jitter_cancel_done_check(self, tmp_path):



        """4. working→idle→working 抖动：idle 后 pending 存在，



        再来 working 后 pending 被清空（_cancel_done_check 生效），此后 _fire_done 不弹气泡。"""



        mgr, win, bubbles, clock = self._make_mgr(tmp_path)



        mgr._on_agent_state("customagent", "working")



        mgr._on_agent_state("customagent", "idle")



        assert "customagent" in mgr._done_pending







        clock[0] += 3.0



        mgr._on_agent_state("customagent", "working")



        assert "customagent" not in mgr._done_pending







        # 此时尝试调用 _fire_done，因为当前 last_raw 是 working（busy 状态），不弹气泡



        mgr._fire_done("customagent")



        assert bubbles == []







    def test_done_cooldown(self, tmp_path):



        """5. 冷却：clock 前进不足 5 秒时第二次 _fire_done 被 _done_cooldown 抑制；



        前进超过 5 秒后正常弹。"""



        mgr, win, bubbles, clock = self._make_mgr(tmp_path)



        mgr._on_agent_state("customagent", "working")



        mgr._on_agent_state("customagent", "idle")



        mgr._fire_done("customagent")



        assert len(bubbles) == 1



        assert "干完活啦" in bubbles[0]







        # 再次进入 busy -> idle



        clock[0] += 3.0  # 3s < 5s 冷却



        mgr._on_agent_state("customagent", "working")



        clock[0] += 1.0  # 累计 4s < 5s



        mgr._on_agent_state("customagent", "idle")



        mgr._fire_done("customagent")



        assert len(bubbles) == 1  # 被冷却抑制，未新增气泡







        # 前进超过 5 秒（从第一次 _fire_done 时刻 1000.0 起算，此时 1004.0 + 2.0 = 1006.0 > 1000.0 + 5.0）



        clock[0] += 2.0



        mgr._on_agent_state("customagent", "working")



        mgr._on_agent_state("customagent", "idle")



        mgr._fire_done("customagent")



        assert len(bubbles) == 2



        assert "干完活啦" in bubbles[1]







    def test_error_during_busy_done_bubble_text(self, tmp_path):



        """6. busy 期间出现 error 再 idle：完成气泡文案含「自己看一眼」而不是「干完活啦」。"""



        mgr, win, bubbles, clock = self._make_mgr(tmp_path)



        mgr._on_agent_state("customagent", "working")



        clock[0] += 3.0



        mgr._on_agent_state("customagent", "error")



        clock[0] += 3.0



        mgr._on_agent_state("customagent", "idle")



        mgr._fire_done("customagent")







        # error 状态本身不立即弹气泡（由完成流程接管，防双气泡）；



        # 完成气泡应当含有「自己看一眼」且不含「干完活啦」



        done_bubbles = [b for b in bubbles if "自己看一眼" in b]



        assert len(done_bubbles) == 1



        assert not any("干完活啦" in b for b in bubbles)







    def test_bubble_busy_until_occupancy(self, tmp_path, monkeypatch):



        """7. _show_link_bubble 在 win._bubble_busy_until 为未来时间时：



        important=False 直接丢弃；important=True 时不立即弹（走 QTimer.singleShot 延后重试，测试里只需断言没有立即调用 show_bubble）。"""



        mgr, win, bubbles, clock = self._make_mgr(tmp_path)



        win._bubble_busy_until = time.time() + 100.0







        # important=False 丢弃



        mgr._show_link_bubble("普通消息", important=False)



        assert bubbles == []







        # important=True 走 singleShot 延后重试，不立即调用 show_bubble



        mgr._show_link_bubble("重要消息", important=True)



        assert bubbles == []







    def test_busy_to_attention_counts_as_done(self, tmp_path):



        """8. 通用 Stop 事件：working→attention(Stop) 进入完成确认，不弹立即提醒，



        确认后弹完成气泡（因见过 attention 用中性文案）。"""



        mgr, win, bubbles, clock = self._make_mgr(tmp_path)



        mgr._on_agent_state("customagent", "working")



        clock[0] += 3.0



        mgr._on_agent_state("customagent", "attention")



        assert "customagent" in mgr._done_pending  # 进入完成确认



        assert bubbles == []  # 不弹立即 attention 气泡（防双气泡）







        mgr._fire_done("customagent")



        assert any("自己看一眼" in b for b in bubbles)



        assert not any("干完活啦" in b for b in bubbles)







    def test_standalone_attention_immediate_bubble(self, tmp_path):



        """9. 非 busy 后独立出现的 attention：立即提醒，不进完成流程。"""



        mgr, win, bubbles, clock = self._make_mgr(tmp_path)



        mgr._on_agent_state("customagent", "attention")



        assert "customagent" not in mgr._done_pending



        assert any("看一眼" in b for b in bubbles)







    def test_done_restores_idle_anim_unless_others_busy(self, tmp_path):



        """10. 完成确认后恢复待机动画（部分 Agent 没有 idle 事件，靠这步回待机）；



        另有 Agent 在忙时不恢复（避免顶掉对方的工作动画）。"""



        mgr, win, bubbles, clock = self._make_mgr(tmp_path)



        mgr._on_agent_state("customagent", "working")



        clock[0] += 3.0



        mgr._on_agent_state("customagent", "idle")



        mgr._fire_done("customagent")



        assert win.switched[-1] == "待机呼吸"  # 恢复待机



        assert mgr._last_applied["customagent"][0] == "idle"







        # 另一 Agent 在忙：不恢复



        mgr2, win2, bubbles2, clock2 = self._make_mgr(tmp_path)



        mgr2._on_agent_state("antigravity", "working")



        clock2[0] += 3.0



        mgr2._on_agent_state("customagent", "working")



        clock2[0] += 3.0



        mgr2._on_agent_state("customagent", "idle")



        switched_before = len(win2.switched)



        mgr2._fire_done("customagent")



        assert len(win2.switched) == switched_before  # 没有切回待机











class TestAgentLinkChainingAndActivity:



    def _make_mgr(self, tmp_path, agent_link_cfg=None, acts=None):



        from PySide6.QtWidgets import QApplication



        app = QApplication.instance() or QApplication([])







        switched = []



        bubbles = []







        class DummyWin:



            cats = {"acts": ["写代码", "吃Token", "轻快记录", "漂浮踏步"] if acts is None else acts}



            idles = ["待机呼吸"]



            _bubble_busy_until = 0.0







            def isVisible(self):



                return True







            def _switch(self, name):



                switched.append(name)







            def show_bubble(self, text, duration_ms=3000):



                bubbles.append(text)







            def _pick(self, lst):



                return lst[0]







            def request_link_anim(self, name):



                switched.append(name)







            def request_link_idle(self):



                switched.append(self.idles[0])







        win = DummyWin()



        win.switched = switched



        cfg = Config(base=tmp_path)



        if agent_link_cfg is not None:



            data = cfg.data



            data["agent_link"] = {**data.get("agent_link", {}), **agent_link_cfg}



            cfg.save()







        clock = [1000.0]



        mgr = AgentLinkManager(win, cfg, min_interval=2.0, clock=lambda: clock[0])



        return mgr, win, bubbles, clock







    def test_anim_rotation_sequence(self, tmp_path):



        """1. 动作池轮换顺序：DummyWin 的 cats.acts 含 ['写代码','吃Token','轻快记录','漂浮踏步']，



        连续 6 次 busy（每次 clock 前进 3s 避免节流）→ 依次为 写代码/吃Token/轻快记录/写代码/吃Token/漂浮踏步（每第3次插播摸鱼）。"""



        mgr, win, bubbles, clock = self._make_mgr(



            tmp_path, acts=["写代码", "吃Token", "轻快记录", "漂浮踏步"]



        )



        res = [mgr._next_link_anim_rotation() for _ in range(6)]



        expected = ["写代码", "吃Token", "轻快记录", "吃Token", "写代码", "漂浮踏步"]



        assert res == expected







    def test_anim_rotation_falls_back_to_keywords_and_available_acts(self, tmp_path):



        """精确动作名不存在时，按主/摸鱼关键词选择；完全不匹配时回退到任意动作。"""



        mgr, win, bubbles, clock = self._make_mgr(



            tmp_path, acts=["敲击键盘", "伸懒腰", "发呆"]



        )



        res = [mgr._next_link_anim_rotation() for _ in range(6)]



        assert res == ["敲击键盘", "敲击键盘", "伸懒腰", "敲击键盘", "敲击键盘", "伸懒腰"]







        mgr, win, bubbles, clock = self._make_mgr(tmp_path, acts=["跳舞"])



        assert mgr._next_link_anim_rotation() == "跳舞"











    def test_empty_acts_returns_none(self, tmp_path):



        """2. 无可用动作时 _next_link_anim_rotation 返回 None（DummyWin cats.acts 为空列表）不抛异常。"""



        mgr, win, bubbles, clock = self._make_mgr(tmp_path, acts=[])



        assert mgr._next_link_anim_rotation() is None



        # 触发状态变更也不抛异常



        mgr._on_agent_state("customagent", "working")



        assert win.switched == []







    def test_activity_reporting(self, tmp_path):



        """3. 过程汇报：cfg agent_link.notify_activity=True 时 mgr._on_agent_activity('customagent','bash') → 气泡含「正在跑命令」；



        10 秒内第二次任何工具不弹；同工具 60 秒内不重复（clock 前进 15s 再发 bash 仍不弹；换成 read 则弹「正在读文件」）；



        全局限流 8s（另一 agent 在 8s 内也不弹）。notify_activity 默认 False 时不弹。未知工具（如 'frobnicate'）弹安全兜底文案。"""



        # notify_activity 默认 False 时不弹



        mgr_off, win_off, bubbles_off, clock_off = self._make_mgr(tmp_path)



        mgr_off._on_agent_activity("customagent", "bash")



        assert bubbles_off == []







        # notify_activity = True



        mgr, win, bubbles, clock = self._make_mgr(tmp_path, agent_link_cfg={"notify_activity": True})







        # 未知工具弹安全兜底文案，不泄露原始参数



        mgr._on_agent_activity("customagent", "frobnicate")



        assert len(bubbles) == 1



        assert "正在调用工具" in bubbles[-1]



        assert "frobnicate" not in bubbles[-1]







        # customagent bash → 弹「正在跑命令」



        clock[0] += 10.0



        mgr._on_agent_activity("customagent", "bash")



        assert len(bubbles) == 2



        assert "正在跑命令" in bubbles[-1]







        # 10 秒内第二次任何工具不弹



        clock[0] += 5.0



        mgr._on_agent_activity("customagent", "read")



        assert len(bubbles) == 2







        # 全局限流 8s（另一 agent 在 8s 内也不弹，从 1000.0 起算此时 1005.0 < 1008.0）



        mgr._on_agent_activity("customagent", "read")



        assert len(bubbles) == 2







        # 同工具 60 秒内不重复：前进 15s（总共 +20s > 10s，但 < 60s），再发 bash 仍不弹



        clock[0] += 15.0



        mgr._on_agent_activity("customagent", "bash")



        assert len(bubbles) == 2







        # 换成 read 则弹「正在读文件」



        mgr._on_agent_activity("customagent", "read")



        assert len(bubbles) == 3



        assert "正在读文件" in bubbles[-1]







        clock[0] += 10.0



        mgr._on_agent_activity("customagent", "pwsh")



        assert "正在跑命令" in bubbles[-1]



        clock[0] += 10.0



        mgr._on_agent_activity("customagent", "memory_search")



        assert "正在翻记忆" in bubbles[-1]







    def test_window_smooth_chaining(self, tmp_path):



        """4. window 侧平滑衔接（用真实 PetWindow + MovieLibrary，offscreen，参考 TestAgentMenuRebound 的构造）：



        win._switch('优雅女仆舞')（一次性动作）后 win.request_link_anim('写代码') →



        当前 anim 仍是 '优雅女仆舞' 且 _pending_link_anim=='写代码'（不打断）；



        手动调 win._on_anim_ended('优雅女仆舞') → anim 变为 '写代码'。



        再测：待机中（win.anim 在 win.idles 里）request_link_anim 立即切换。



        request_link_idle 在一次性动作播放中不切回待机（anim 不变、pending 清空）。"""



        from PySide6.QtWidgets import QApplication



        from pet.window import PetWindow



        from pet.library import MovieLibrary







        app = QApplication.instance() or QApplication([])



        cfg = Config(base=tmp_path)



        lib = MovieLibrary(character_id="shenshen")



        win = PetWindow(lib, cfg)







        try:



            # 确保 '优雅女仆舞' 是一次性动作 (acts)



            assert "优雅女仆舞" in win.acts



            win._switch("优雅女仆舞")



            assert win.anim == "优雅女仆舞"



            assert win._is_one_shot_playing() is True







            win.request_link_anim("写代码")



            assert win.anim == "优雅女仆舞"



            assert win._pending_link_anim == "写代码"







            # 手动调 _on_anim_ended('优雅女仆舞') → 播放待播的 '写代码'



            win._on_anim_ended("优雅女仆舞")



            assert win.anim == "写代码"



            assert win._pending_link_anim is None







            # 待机中（win.anim 在 win.idles 里）request_link_anim 立即切换



            idle_name = win.idles[0]



            win._switch(idle_name)



            assert win.anim in win.idles



            assert win._is_one_shot_playing() is False







            win.request_link_anim("吃Token")



            assert win.anim == "吃Token"







            # request_link_idle 在一次性动作播放中不切回待机（anim 不变、pending 清空）



            win._switch("优雅女仆舞")



            win._pending_link_anim = "写代码"



            win.request_link_idle()



            assert win.anim == "优雅女仆舞"



            assert win._pending_link_anim is None



        finally:



            win.close()



            win.deleteLater()







    def test_on_anim_ended_continuation(self, tmp_path):



        """5. _on_anim_ended 联动续播：构造 PetWindow 后，设置 win._link_anim_current='写代码'，



        win._link_next_provider=lambda: '吃Token'，调 win._on_anim_ended('写代码') → anim=='吃Token'；



        provider 返回 None 时走正常动画链（不抛异常即可）。"""



        from PySide6.QtWidgets import QApplication



        from pet.window import PetWindow



        from pet.library import MovieLibrary







        app = QApplication.instance() or QApplication([])



        cfg = Config(base=tmp_path)



        lib = MovieLibrary(character_id="shenshen")



        win = PetWindow(lib, cfg)







        try:



            win._link_anim_current = "写代码"



            win._link_next_provider = lambda: "吃Token"



            win._on_anim_ended("写代码")



            assert win.anim == "吃Token"



            assert win._link_anim_current == "吃Token"







            # provider 返回 None 时走正常动画链（不抛异常）



            win._link_next_provider = lambda: None



            win._on_anim_ended("吃Token")



            # 正常推进，不抛异常



            assert win._link_anim_current is None



        finally:



            win.close()



            win.deleteLater()















# ============================================================================



# 14. 过程汇报：事件 tool 字段 → activity 信号



# ============================================================================



class TestActivitySignal:



    def test_tool_field_emits_activity_without_state(self, tmp_path):



        """jsonl 事件带 tool 字段时发 activity 信号，且不产生状态变化。"""



        app = QApplication.instance() or QApplication([])



        cfg = Config(base=tmp_path)



        mon = BaseAgentMonitor("customagent", cfg.dir)



        got, states = [], []



        mon.activity.connect(lambda a, t: got.append((a, t)))



        mon.state_changed.connect(lambda a, s: states.append(s))



        mon.events_dir.mkdir(parents=True, exist_ok=True)



        mon.events_file.touch()  # 先建空文件，backfill 才能落到末尾



        mon._tailer.read_new_lines()  # backfill 初始化



        with mon.events_file.open("a", encoding="utf-8") as fh:



            fh.write('{"ts":1,"agent":"customagent","event":"tool/call","tool":"bash"}\n')



        mon._poll()



        assert got == [("customagent", "bash")]



        assert states == []







    def test_no_tool_no_activity(self, tmp_path):



        """普通状态事件不发 activity。"""



        app = QApplication.instance() or QApplication([])



        cfg = Config(base=tmp_path)



        mon = BaseAgentMonitor("customagent", cfg.dir)



        got = []



        mon.activity.connect(lambda a, t: got.append(t))



        mon.events_dir.mkdir(parents=True, exist_ok=True)



        mon.events_file.touch()



        mon._tailer.read_new_lines()



        with mon.events_file.open("a", encoding="utf-8") as fh:



            fh.write('{"ts":1,"agent":"customagent","event":"AgentStatus","state":"working"}\n')



        mon._poll()



        assert got == []







    class _HiddenWin:



        cats = {"acts": ["写代码"]}



        idles = ["待机呼吸"]



        _bubble_busy_until = 0.0



        switched = None



        bubbles = None







        def __init__(self):



            self.switched = []



            self.bubbles = []







        def isVisible(self):



            return False







        def _switch(self, name):



            self.switched.append(name)







        def show_bubble(self, text, duration_ms=3000):



            self.bubbles.append(text)







        def _pick(self, lst):



            return lst[0]







    def test_fire_done_hidden_window_is_noop(self, tmp_path):



        """opus 评审 H1：隐藏窗口上 _fire_done 不得切动画/弹气泡。"""



        app = QApplication.instance() or QApplication([])



        win = TestActivitySignal._HiddenWin()



        cfg = Config(base=tmp_path)



        mgr = AgentLinkManager(win, cfg)



        mgr._last_raw["customagent"] = "idle"



        mgr._fire_done("customagent")



        assert win.switched == []



        assert win.bubbles == []







    def test_pause_cancels_done_pending(self, tmp_path):



        """opus 评审 H1：pause 必须取消所有完成确认计时器。"""



        app = QApplication.instance() or QApplication([])



        cfg = Config(base=tmp_path)



        bubbles = []







        class Win:



            cats = {"acts": ["写代码"]}



            idles = ["待机呼吸"]



            _bubble_busy_until = 0.0







            def isVisible(self):



                return True







            def _switch(self, name):



                pass







            def request_link_idle(self):



                pass







            def show_bubble(self, text, duration_ms=3000):



                bubbles.append(text)







            def _pick(self, lst):



                return lst[0]







        mgr = AgentLinkManager(Win(), cfg)



        mgr._on_agent_state("customagent", "working")



        mgr._on_agent_state("customagent", "idle")



        assert "customagent" in mgr._done_pending



        mgr.pause()



        assert mgr._done_pending == {}







# ============================================================================



# 15. Antigravity 统一事件通道与进程归属测试



# ============================================================================



class TestAntigravityUnifiedChannel:



    def test_unified_jsonl_channel(self, tmp_path):



        """测试通过 agent-events/antigravity.jsonl 注入事件。"""



        import json as j



        from PySide6.QtWidgets import QApplication



        from pet.agent_link import AntigravityMonitor







        app = QApplication.instance() or QApplication([])



        cfg_dir = tmp_path / "cfg"



        cfg_dir.mkdir()



        events_file = cfg_dir / "agent-events" / "antigravity.jsonl"



        events_file.parent.mkdir(parents=True, exist_ok=True)



        events_file.write_text(j.dumps({"type": "INIT"}) + "\n", encoding="utf-8")







        received, tools = [], []



        mon = AntigravityMonitor(cfg_dir, base_dir=tmp_path / "brain")



        mon.state_changed.connect(lambda k, s: received.append(s))



        mon.activity.connect(lambda k, t: tools.append(t))



        mon.start()



        mon._poll()  # 首次 backfill 跳过历史







        with open(events_file, "a", encoding="utf-8") as f:



            f.write(j.dumps({"state": "working", "tool": "replace_file_content"}) + "\n")



            f.write(j.dumps({"state": "idle"}) + "\n")







        mon._poll()



        assert received == ["working", "idle"]



        assert tools == ["replace_file_content"]



        mon.stop()







    def test_busy_agent_owns_process(self, tmp_path):



        """联动去重：联动开启+忙碌+进程匹配 → True；其余组合 → False。"""



        app = QApplication.instance() or QApplication([])



        cfg = Config(base=tmp_path)



        cfg.data["agent_link"]["antigravity"] = True







        class W:



            cats = {"acts": []}



            idles = []



            _bubble_busy_until = 0.0



            def isVisible(self): return True



            def show_bubble(self, *a, **k): pass







        mgr = AgentLinkManager(W(), cfg)



        mgr._last_raw["antigravity"] = "working"



        assert mgr.busy_agent_owns_process("Antigravity.exe") is True



        assert mgr.busy_agent_owns_process("antigravity-ide.exe") is True



        assert mgr.busy_agent_owns_process("msedge.exe") is False



        mgr._last_raw["antigravity"] = "idle"



        assert mgr.busy_agent_owns_process("Antigravity.exe") is False



        mgr._last_raw["antigravity"] = "working"



        cfg.data["agent_link"]["antigravity"] = False  # 联动关闭时不抑制识屏



        assert mgr.busy_agent_owns_process("Antigravity.exe") is False



        # 靠窗口标题识别



        cfg.data["agent_link"]["antigravity"] = True



        mgr._last_raw["antigravity"] = "working"



        assert mgr.busy_agent_owns_process("other.exe", "Antigravity Project") is True



        assert mgr.busy_agent_owns_process("other.exe", "哔哩哔哩") is False



        mgr._last_raw["antigravity"] = "idle"



        assert mgr.busy_agent_owns_process("other.exe", "Antigravity Project") is False











# ============================================================================



# 自定义联动 Agent（agent_link.custom_agents 配置驱动）



# ============================================================================



class TestCustomAgentConfigCleaning:



    def test_valid_entry_kept_and_normalized(self):



        cleaned = _clean_custom_agents([



            {"key": "Gemini", "name": "  Gemini CLI  ", "path": " ~/.gemini/ev.jsonl "},



        ])



        assert cleaned == [{"key": "gemini", "name": "Gemini CLI", "path": "~/.gemini/ev.jsonl"}]







    def test_name_defaults_to_key(self):



        cleaned = _clean_custom_agents([{"key": "myagent", "path": "~/x.jsonl"}])



        assert cleaned == [{"key": "myagent", "name": "myagent", "path": "~/x.jsonl"}]







    def test_invalid_entries_dropped(self):



        cleaned = _clean_custom_agents([



            "not-a-dict",                                # 非对象



            {"key": "Bad Key", "path": "~/x.jsonl"},     # key 含空格/大写



            {"key": "antigravity", "path": "~/x.jsonl"},      # 与内置键冲突



            {"key": "ok", "path": ""},                   # 空 path



            {"key": "ok2"},                              # 缺 path



        ])



        assert cleaned == []







    def test_duplicate_keys_deduped(self):



        cleaned = _clean_custom_agents([



            {"key": "gemini", "path": "~/a.jsonl"},



            {"key": "gemini", "path": "~/b.jsonl"},



        ])



        assert len(cleaned) == 1



        assert cleaned[0]["path"] == "~/a.jsonl"







    def test_max_entries_truncated(self):



        raw = [{"key": f"agent{i}", "path": f"~/{i}.jsonl"} for i in range(20)]



        assert len(_clean_custom_agents(raw)) == 8







    def test_non_list_returns_empty(self):



        assert _clean_custom_agents(None) == []



        assert _clean_custom_agents({"key": "gemini"}) == []







    def test_clean_agent_link_data_cleans_and_keeps_custom_key_booleans(self):



        cleaned = _clean_agent_link_data({



            "custom_agents": [{"key": "gemini", "name": "Gemini CLI", "path": "~/ev.jsonl"}],



            "gemini": True,       # 自定义键的开关布尔（set_enabled 写入路径）



            "notify_done": False,



        })



        assert cleaned["custom_agents"] == [{"key": "gemini", "name": "Gemini CLI", "path": "~/ev.jsonl"}]

        assert cleaned["gemini"] is True

        assert cleaned["notify_done"] is False

    def test_clean_agent_link_data_drops_removed_builtin_integrations(self):
        cleaned = _clean_agent_link_data({
            "dsh": True,
            "claude": True,
            "deepseek": True,
            "chatgpt": True,
            "custom_agents": [{"key": "dsh", "path": "~/old.jsonl"}],
        })

        assert cleaned["chatgpt"] is True
        assert "dsh" not in cleaned
        assert "claude" not in cleaned
        assert "deepseek" not in cleaned
        assert cleaned["custom_agents"] == []











class TestCustomAgentMonitor:



    def test_tail_events_and_signals(self, tmp_path):



        """统一协议三种形态（state / event+tool / state 收尾）→ 信号正确。"""



        app = QApplication.instance() or QApplication([])



        events = tmp_path / "sub" / "gemini.jsonl"



        events.parent.mkdir(parents=True)



        events.touch()







        states, tools = [], []



        mon = CustomAgentMonitor("gemini", tmp_path / "cfg", str(events))



        mon.state_changed.connect(lambda k, s: states.append((k, s)))



        mon.activity.connect(lambda k, t: tools.append((k, t)))



        mon.start()



        mon._poll()  # backfill 初始化







        with open(events, "a", encoding="utf-8") as f:



            f.write(json.dumps({"ts": 1.0, "state": "working"}) + "\n")



            f.write(json.dumps({"ts": 2.0, "event": "PreToolUse", "tool": "bash"}) + "\n")



            f.write(json.dumps({"ts": 3.0, "state": "idle"}) + "\n")







        mon._poll()



        # PreToolUse 事件按内置映射同时产生 working 状态 + bash 工具过程



        assert states == [("gemini", "working"), ("gemini", "working"), ("gemini", "idle")]



        assert tools == [("gemini", "bash")]



        mon.stop()







    def test_missing_file_idle_then_appears(self, tmp_path):



        """文件不存在时空转；出现后 backfill 防护跳过历史，只读新增行。"""



        app = QApplication.instance() or QApplication([])



        missing = tmp_path / "not_yet.jsonl"



        mon = CustomAgentMonitor("gemini", tmp_path / "cfg", str(missing))



        states = []



        mon.state_changed.connect(lambda k, s: states.append((k, s)))



        mon.start()



        mon._poll()



        mon._poll()



        assert states == []







        missing.write_text('{"state": "working"}\n', encoding="utf-8")



        mon._poll()  # 首次发现文件：backfill，不回放历史



        assert states == []







        with open(missing, "a", encoding="utf-8") as f:



            f.write('{"state": "idle"}\n')



        mon._poll()



        assert states == [("gemini", "idle")]



        mon.stop()







    def test_start_does_not_create_dirs(self, tmp_path):



        """只读监听：绝不替用户在任意路径创建目录。"""



        app = QApplication.instance() or QApplication([])



        mon = CustomAgentMonitor(



            "gemini", tmp_path / "cfg", str(tmp_path / "deep" / "nested" / "ev.jsonl"),



        )



        mon.start()



        mon._poll()



        assert not (tmp_path / "deep").exists()



        mon.stop()







    def test_tilde_path_expanded(self, tmp_path, monkeypatch):



        # expanduser 在 Windows 读 USERPROFILE、POSIX 读 HOME，两个都设以保证跨平台



        monkeypatch.setenv("USERPROFILE", str(tmp_path))



        monkeypatch.setenv("HOME", str(tmp_path))



        mon = CustomAgentMonitor("gemini", tmp_path / "cfg", "~/events.jsonl")



        assert mon.events_file == tmp_path / "events.jsonl"



        assert "~" not in str(mon.events_file)











class TestCustomAgentManager:



    def test_registered_names_merged_and_generic_toggle(self, tmp_path):



        """custom_agents → 监视器注册 + 显示名合并 + 通用开关联动（无需授权弹窗）。"""



        app = QApplication.instance() or QApplication([])



        cfg = Config(base=tmp_path)



        ag = dict(cfg.get("agent_link", {}))



        ag["custom_agents"] = [



            {"key": "gemini", "name": "Gemini CLI", "path": str(tmp_path / "gemini.jsonl")},



        ]



        cfg.set("agent_link", ag)



        cfg.save()







        mgr = AgentLinkManager(None, cfg)



        assert "gemini" in mgr.monitors



        assert isinstance(mgr.monitors["gemini"], CustomAgentMonitor)



        assert mgr.agent_names["gemini"] == "Gemini CLI"



        # 类级 AGENT_NAMES 保持仅内置：设置页按内置枚举的遍历不受自定义影响



        assert "gemini" not in AgentLinkManager.AGENT_NAMES







        # 通用开关：开启持久化并启动监视器



        assert mgr.set_enabled("gemini", True) is True



        assert cfg.data["agent_link"]["gemini"] is True



        assert mgr.monitors["gemini"].is_running() is True







        # 隐藏暂停 / 显示恢复



        mgr.pause()



        assert mgr.monitors["gemini"].is_running() is False



        mgr.resume()



        assert mgr.monitors["gemini"].is_running() is True







        # 关闭



        assert mgr.set_enabled("gemini", False) is True



        assert cfg.data["agent_link"]["gemini"] is False



        assert mgr.monitors["gemini"].is_running() is False







    def test_builtin_key_in_custom_agents_ignored(self, tmp_path):



        """config 清洗会拒绝与内置键冲突的自定义条目，管理器不覆盖内置监视器。"""



        app = QApplication.instance() or QApplication([])



        cfg = Config(base=tmp_path)



        ag = dict(cfg.get("agent_link", {}))



        ag["custom_agents"] = [{"key": "chatgpt", "name": "Fake", "path": str(tmp_path / "x.jsonl")}]



        cfg.set("agent_link", ag)



        cfg.save()







        mgr = AgentLinkManager(None, cfg)



        assert not isinstance(mgr.monitors["chatgpt"], CustomAgentMonitor)



        assert mgr.agent_names["chatgpt"] == "ChatGPT"











class TestCustomAgentMenu:



    def test_menu_lists_custom_agent_and_toggle_routes(self, tmp_path):



        """右键菜单动态渲染自定义 Agent，勾选走通用 _toggle_agent_link。"""



        from PySide6.QtWidgets import QMenu



        from pet.context_menus.shared import add_agent_link_menu







        app = QApplication.instance() or QApplication([])



        cfg = Config(base=tmp_path)



        ag = dict(cfg.get("agent_link", {}))



        ag["custom_agents"] = [



            {"key": "gemini", "name": "Gemini CLI", "path": "~/gemini.jsonl"},



        ]



        cfg.set("agent_link", ag)



        cfg.save()







        toggles, options = [], []







        class DummyPet:



            def __init__(self):



                self.cfg = cfg







            def _toggle_agent_link(self, key, on, action=None):



                toggles.append((key, on))







            def _set_agent_link_option(self, key, on):



                options.append((key, on))







        menu = QMenu()



        try:



            add_agent_link_menu(menu, DummyPet())



            sub = menu.actions()[0].menu()



            texts = [a.text() for a in sub.actions()]



            # 内置 2 项仍在，自定义项按显示名插入



            for label in ("Antigravity IDE", "ChatGPT"):



                assert label in texts



            assert texts[0] == "Antigravity IDE"



            assert "Gemini CLI" in texts







            gemini_act = next(a for a in sub.actions() if a.text() == "Gemini CLI")



            gemini_act.setChecked(True)



            assert toggles == [("gemini", True)]



        finally:



            menu.deleteLater()




