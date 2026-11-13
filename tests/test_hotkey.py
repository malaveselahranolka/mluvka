"""Jednotkové testy stavového automatu HotkeyFSM."""

from __future__ import annotations

from mluvka.hotkey import Action, HotkeyFSM, VK_ESCAPE, VK_RCONTROL, VK_Z


def test_hold_to_talk_start_and_stop() -> None:
    fsm = HotkeyFSM(VK_RCONTROL)
    acts, swallow = fsm.on_key(VK_RCONTROL, True, 0.0, False, False, True)
    assert acts == [Action("START", "hold")]
    assert swallow is True

    acts, swallow = fsm.on_key(VK_RCONTROL, False, 0.35, False, False, False)
    assert acts == [Action("STOP")]
    assert swallow is True
    assert fsm.state == "IDLE"


def test_short_tap_cancels_after_400ms() -> None:
    fsm = HotkeyFSM(VK_RCONTROL)
    acts, _ = fsm.on_key(VK_RCONTROL, True, 0.0, False, False, True)
    assert acts == [Action("START", "hold")]

    acts, swallow = fsm.on_key(VK_RCONTROL, False, 0.15, False, False, False)
    assert acts == []
    assert swallow is True

    assert fsm.tick(0.50) == []
    assert fsm.tick(0.56) == [Action("CANCEL")]
    assert fsm.state == "IDLE"


def test_double_tap_enters_hands_free_and_swallows_up_then_stops() -> None:
    fsm = HotkeyFSM(VK_RCONTROL)
    acts, _ = fsm.on_key(VK_RCONTROL, True, 0.0, False, False, True)
    assert acts == [Action("START", "hold")]
    acts, _ = fsm.on_key(VK_RCONTROL, False, 0.10, False, False, False)
    assert acts == []

    # Druhý stisk do 400 ms -> HANDS_FREE
    acts, swallow = fsm.on_key(VK_RCONTROL, True, 0.25, False, False, True)
    assert acts == [Action("HANDS_FREE")]
    assert swallow is True
    assert fsm.state == "HANDS_FREE"

    # Uvolnění druhého stisku se spolkne bez akce
    acts, swallow = fsm.on_key(VK_RCONTROL, False, 0.35, False, False, False)
    assert acts == []
    assert swallow is True
    assert fsm.state == "HANDS_FREE"

    # Další stisk ukončí hands-free a uvolnění se spolkne
    acts, swallow = fsm.on_key(VK_RCONTROL, True, 2.0, False, False, True)
    assert acts == [Action("STOP")]
    assert swallow is True
    acts, swallow = fsm.on_key(VK_RCONTROL, False, 2.1, False, False, False)
    assert acts == []
    assert swallow is True


def test_esc_cancels_when_recording_and_passes_through_when_idle() -> None:
    fsm = HotkeyFSM(VK_RCONTROL)
    # Idle Esc passes through
    acts, swallow = fsm.on_key(VK_ESCAPE, True, 0.0, False, False, False)
    assert acts == []
    assert swallow is False
    acts, swallow = fsm.on_key(VK_ESCAPE, False, 0.05, False, False, False)
    assert acts == []
    assert swallow is False

    # Recording Esc cancels and swallows down + up
    fsm.on_key(VK_RCONTROL, True, 1.0, False, False, True)
    acts, swallow = fsm.on_key(VK_ESCAPE, True, 1.2, False, False, True)
    assert acts == [Action("CANCEL")]
    assert swallow is True
    acts, swallow = fsm.on_key(VK_ESCAPE, False, 1.25, False, False, True)
    assert acts == []
    assert swallow is True
    # Release of dictation key after Esc cancel is still swallowed
    acts, swallow = fsm.on_key(VK_RCONTROL, False, 1.3, False, False, False)
    assert acts == []
    assert swallow is True


def test_command_mode_start_stop_and_short_cancel() -> None:
    fsm = HotkeyFSM(VK_RCONTROL)
    acts, swallow = fsm.on_key(VK_RCONTROL, True, 0.0, True, False, True)
    assert acts == [Action("START", "command")]
    assert swallow is True

    acts, swallow = fsm.on_key(VK_RCONTROL, False, 0.40, False, False, False)
    assert acts == [Action("STOP_COMMAND")]
    assert swallow is True

    # Krátký stisk v režimu příkazu zruší nahrávání
    fsm.on_key(VK_RCONTROL, True, 1.0, True, False, True)
    acts, swallow = fsm.on_key(VK_RCONTROL, False, 1.15, True, False, False)
    assert acts == [Action("CANCEL")]
    assert swallow is True


def test_alt_shift_z_pastes_last_and_swallows_z() -> None:
    fsm = HotkeyFSM(VK_RCONTROL)
    acts, swallow = fsm.on_key(VK_Z, True, 0.0, True, True, False)
    assert acts == [Action("PASTE_LAST")]
    assert swallow is True
    # Auto-repeat Z ignored while swallowed
    acts, swallow = fsm.on_key(VK_Z, True, 0.05, True, True, False)
    assert acts == []
    assert swallow is True
    acts, swallow = fsm.on_key(VK_Z, False, 0.10, True, True, False)
    assert acts == []
    assert swallow is True


def test_auto_repeat_on_dictation_key_ignored_and_swallowed() -> None:
    fsm = HotkeyFSM(VK_RCONTROL)
    acts, swallow = fsm.on_key(VK_RCONTROL, True, 0.0, False, False, True)
    assert acts == [Action("START", "hold")]
    assert swallow is True

    acts, swallow = fsm.on_key(VK_RCONTROL, True, 0.5, False, False, True)
    assert acts == []
    assert swallow is True


def test_paused_passes_everything_through() -> None:
    fsm = HotkeyFSM(VK_RCONTROL, paused=True)
    acts, swallow = fsm.on_key(VK_RCONTROL, True, 0.0, False, False, True)
    assert acts == []
    assert swallow is False
    acts, swallow = fsm.on_key(VK_Z, True, 0.1, True, True, False)
    assert acts == []
    assert swallow is False
    acts, swallow = fsm.on_key(VK_ESCAPE, True, 0.2, False, False, False)
    assert acts == []
    assert swallow is False

    # Pozastavení během záznamu vyvolá CANCEL
    fsm.set_paused(False)
    fsm.on_key(VK_RCONTROL, True, 1.0, False, False, True)
    assert fsm.set_paused(True) == [Action("CANCEL")]


def test_cancel_active_preserves_held_key_against_autorepeat() -> None:
    fsm = HotkeyFSM(VK_RCONTROL)
    acts, _ = fsm.on_key(VK_RCONTROL, True, 0.0, False, False, True)
    assert acts == [Action("START", "hold")]

    fsm.cancel_active()
    assert fsm.state == "IDLE"

    # Další auto-repeat down před uvolněním nesmí znovu spustit START
    acts, swallow = fsm.on_key(VK_RCONTROL, True, 0.2, False, False, True)
    assert acts == []
    assert swallow is True

    # Teprve po uvolnění lze znovu diktovat
    acts, swallow = fsm.on_key(VK_RCONTROL, False, 0.3, False, False, False)
    assert acts == []
    assert swallow is True
    acts, swallow = fsm.on_key(VK_RCONTROL, True, 0.4, False, False, True)
    assert acts == [Action("START", "hold")]
