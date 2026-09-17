from types import SimpleNamespace

from pet.config import Config


def test_movement_preferences_survive_restart(tmp_path):
    cfg = Config(tmp_path)
    cfg.set('smart_edge_turn', False)
    cfg.set('smooth_wander', False)
    assert cfg.save()
    loaded = Config(tmp_path)
    assert loaded.get('smart_edge_turn') is False
    assert loaded.get('smooth_wander') is False


def test_removed_chat_fields_are_not_created_in_pet_config(tmp_path):
    data = Config(tmp_path).data
    assert not any(key.startswith(('chat_', 'modern_chat_')) for key in data)


def test_modern_settings_persists_notifications_and_motion(_application_lifetime, tmp_path):
    from pet.modern_settings_dialog import ModernSettingsDialog
    cfg = Config(tmp_path)
    dialog = ModernSettingsDialog(cfg)
    dialog.system_notifications_check.setChecked(False)
    dialog.smart_edge_turn_check.setChecked(False)
    dialog.smooth_wander_check.setChecked(False)
    assert dialog._write_config()
    loaded = Config(tmp_path)
    assert loaded.get('system_notifications_enabled') is False
    assert loaded.get('smart_edge_turn') is False
    assert loaded.get('smooth_wander') is False
    dialog.close()


def test_disabled_system_notifications_do_not_construct_toast(_application_lifetime, tmp_path, monkeypatch):
    import pet.application.app as app_module
    cfg = Config(tmp_path)
    cfg.set('system_notifications_enabled', False)
    controller = app_module.PetApp(_application_lifetime, cfg)
    monkeypatch.setattr(app_module, 'DesktopNotification', lambda *a, **k: (_ for _ in ()).throw(AssertionError('toast')))
    assert controller.system_notify('title', 'body') is False


def test_agent_completion_does_not_use_system_notification(_application_lifetime, tmp_path):
    from pet.agent_link import AgentLinkManager
    sent = []
    win = SimpleNamespace(isVisible=lambda: True, on_system_notify=lambda title, body: sent.append((title, body)),
                          request_link_idle=lambda: None, show_bubble=lambda *a, **k: None,
                          cats={'acts': []}, idles=[])
    cfg = Config(tmp_path)
    manager = AgentLinkManager(win, cfg)
    manager._last_raw['chatgpt'] = 'idle'
    manager._fire_done('chatgpt')
    assert sent == []
    manager.stop()
