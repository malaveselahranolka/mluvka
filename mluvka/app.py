"""Hlavní koordinátor aplikace Mluvka (vlákna, stav, záznam, přepis a vložení)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import os
import queue
import threading
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from mluvka import (
    asr,
    audio,
    channel,
    config,
    history,
    hotkey,
    llm,
    netguard,
    okno,
    output,
    overlay,
    pamet,
    text,
    tray,
    trenink,
    winproc,
)

log = logging.getLogger("mluvka.app")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

kernel32.CreateMutexW.argtypes = [wt.LPVOID, wt.BOOL, wt.LPCWSTR]
kernel32.CreateMutexW.restype = wt.HANDLE
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL
user32.MessageBoxW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.UINT]
user32.MessageBoxW.restype = ctypes.c_int

ERROR_ALREADY_EXISTS = 183
MB_ICONINFORMATION = 0x00000040


def setup_logging(data: Path | None = None) -> None:
    log_path = config.log_file_path(data)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger("mluvka")
    for h in list(root_logger.handlers):
        if isinstance(h, RotatingFileHandler):
            if Path(h.baseFilename) == log_path:
                return
            root_logger.removeHandler(h)
            h.close()
    root_logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root_logger.addHandler(handler)


class MluvkaApp:
    """Propojuje globální zkratky, zvuk, ASR, volitelné LLM, schránku, tray a overlay."""

    def __init__(self, cfg_store: config.ConfigStore, data: Path | None = None) -> None:
        self._data = data
        self._cfg_store = cfg_store
        self._state_lock = threading.Lock()

        self._paused = False
        self._recording = False
        self._recording_mode = ""
        self._recording_seq = 0
        self._transcribing = 0
        self._stopping = False
        self._fg_app = ""
        self._selection = ""
        self._saved_clipboard: str | None = None
        self._last_transcript = history.last_transcript(data)

        self._whisper_ready = threading.Event()
        self._model_loaded = False
        self._whisper_error: Exception | None = None
        self._active_model_path = self._resolve_model_path(self._cfg_store.snapshot())
        self._pending_model_path: Path | None = None
        self._reloading_model = False

        self._overlay = overlay.Overlay()
        self._whisper = asr.WhisperServer(data, model_path=self._active_model_path)
        self._ai = llm.ClaudeAI(data)
        self._recorder = audio.Recorder()
        self._streamer: asr.StreamingTranscriber | None = None

        self._work_queue: queue.Queue[tuple[str, Any] | None] = queue.Queue()
        self._hook = hotkey.GlobalHotkeyHook(
            on_action=self._on_hotkey_action,
            klavesa_vk=int(self._cfg_store.get("klavesa_vk", hotkey.VK_RCONTROL)),
            paused=False,
        )
        self._tray = tray.TrayController(
            get_cfg=self._cfg_store.snapshot,
            is_ready=lambda: self._model_loaded,
            is_paused=lambda: self._paused,
            on_paste_last=lambda: self._work_queue.put(("paste_last", True)),
            on_select_mic=self._select_mic,
            on_toggle_ai=self._toggle_ai,
            on_toggle_channel=self._toggle_channel,
            on_toggle_sounds=self._toggle_sounds,
            on_toggle_paused=self._toggle_paused,
            on_toggle_autostart=self._toggle_autostart,
            on_quit=self._request_quit,
            on_open_window=self._open_window,
            data=data,
        )
        self._config_poll_after_id: str | None = None

    def _idle_tray_state(self) -> str:
        if self._paused:
            return "pozastaveno"
        if self._model_loaded:
            return "pripraveno"
        return "nacita"

    def _resolve_model_path(self, cfg: dict[str, Any]) -> Path:
        env_model = os.environ.get("MLUVKA_WHISPER_MODEL", "").strip()
        if env_model:
            return Path(env_model)
        raw_name = cfg.get("model_soubor")
        if raw_name is None or raw_name == "":
            return config.default_whisper_model_path(self._data)
        try:
            return config.whisper_model_path(self._data, model_soubor=raw_name)
        except ValueError as exc:
            log.warning("Neplatný model_soubor v config.json (%s); použiji výchozí.", exc)
            return config.default_whisper_model_path(self._data)

    def _maybe_schedule_model_reload(self) -> None:
        if not self._whisper_ready.is_set():
            return
        target: Path | None = None
        with self._state_lock:
            if (
                self._stopping
                or self._recording
                or self._transcribing > 0
                or self._reloading_model
                or self._pending_model_path is None
            ):
                return
            if self._pending_model_path == self._active_model_path:
                self._pending_model_path = None
                return
            target = self._pending_model_path
            self._pending_model_path = None
            self._reloading_model = True
        if target is not None:
            self._work_queue.put(("reload_model", target))

    def _refresh_config(self, *, refresh_menu: bool = True) -> dict[str, Any]:
        if self._cfg_store.reload_if_changed():
            cfg = self._cfg_store.snapshot()
            netguard.set_allowed_hosts(config.allowed_hosts(cfg))
            try:
                vk = int(cfg.get("klavesa_vk", hotkey.VK_RCONTROL))
            except (TypeError, ValueError):
                vk = hotkey.VK_RCONTROL
            self._hook.set_klavesa_vk(vk)
            target_model = self._resolve_model_path(cfg)
            with self._state_lock:
                if target_model != self._active_model_path or self._reloading_model:
                    self._pending_model_path = target_model
                elif self._pending_model_path == self._active_model_path:
                    self._pending_model_path = None
            log.info(
                "Konfigurace aktualizována: klavesa_vk=%d zvuky=%s",
                vk,
                bool(cfg.get("zvuky", True)),
            )
            if refresh_menu:
                self._tray.refresh_menu()
            return cfg
        return self._cfg_store.snapshot()

    def _on_hotkey_action(self, act: hotkey.Action) -> None:
        if act.name == "START":
            self._start_recording(act.mode or "hold")
        elif act.name == "HANDS_FREE":
            with self._state_lock:
                if self._recording:
                    self._recording_mode = "hands_free"
                    self._overlay.show("volne_ruce")
        elif act.name == "CANCEL":
            self._cancel_recording()
        elif act.name in ("STOP", "STOP_COMMAND"):
            self._stop_recording(is_command=(act.name == "STOP_COMMAND"))
        elif act.name == "PASTE_LAST":
            # Z bylo spolknuto: bez maskovací klávesy by Windows po puštění Alt+Shift přepnul jazyk klávesnice.
            output.send_mask_key()
            self._work_queue.put(("paste_last", False))

    def _start_recording(self, mode: str) -> None:
        cfg = self._refresh_config()
        with self._state_lock:
            if self._recording or self._stopping:
                return
            self._recording = True
            self._recording_seq += 1
            rec_seq = self._recording_seq
            self._recording_mode = mode
            self._selection = ""
            self._saved_clipboard = None
            self._fg_app = winproc.foreground_process_name()

        audio.play_sound("start", bool(cfg.get("zvuky", True)), self._data)
        ov_state = "prikaz" if mode == "command" else "nahravam"
        self._overlay.show(ov_state)
        self._tray.set_state("nahravam")

        try:
            self._recorder.start(
                mikrofon=str(cfg.get("mikrofon", "") or ""),
                on_level=self._overlay.set_level,
                on_max_duration=lambda seq=rec_seq: self._on_max_duration(seq),
            )
        except Exception as exc:
            log.exception("Nepodařilo se otevřít mikrofon: %s", type(exc).__name__)
            with self._state_lock:
                self._recording = False
                self._recording_mode = ""
            self._hook.cancel_active()
            self._tray.set_state(self._idle_tray_state())
            self._overlay.toast("Mikrofon není dostupný", kind="error")
            return

        if mode == "command":
            sel, prev = output.grab_selection()
            with self._state_lock:
                self._selection = sel
                self._saved_clipboard = prev

        should_stream = not (mode == "command" and not llm.is_available(self._data))
        streamer: asr.StreamingTranscriber | None = None
        if should_stream:
            raw_sid = getattr(self._recorder, "session_id", None)
            sid = raw_sid if isinstance(raw_sid, int) and not isinstance(raw_sid, bool) else None

            def _get_audio(start_idx: int) -> Any:
                if sid is not None:
                    return self._recorder.snapshot(start_idx, session_id=sid)
                return self._recorder.snapshot(start_idx)

            with self._state_lock:
                if self._recording and not self._stopping:
                    streamer = asr.StreamingTranscriber(
                        self._whisper,
                        _get_audio,
                        get_server=lambda: self._whisper,
                        cfg=cfg,
                        ready_event=self._whisper_ready,
                        is_ready_fn=lambda: (
                            self._model_loaded
                            and self._whisper_error is None
                            and not self._reloading_model
                        ),
                    )
                    self._streamer = streamer
                    streamer.start()

    def _on_max_duration(self, recording_seq: int | None = None) -> None:
        with self._state_lock:
            if not self._recording:
                return
            if recording_seq is not None and recording_seq != self._recording_seq:
                return
            is_cmd = self._recording_mode == "command"
            expected_seq = self._recording_seq
        self._hook.cancel_active()
        self._stop_recording(is_command=is_cmd, expected_seq=expected_seq)

    def _cancel_recording(self) -> None:
        with self._state_lock:
            if not self._recording:
                return
            self._recording = False
            self._recording_mode = ""
            streamer = self._streamer
            self._streamer = None
            sel = self._selection
            saved_clip = self._saved_clipboard
            self._selection = ""
            self._saved_clipboard = None
        if streamer is not None:
            streamer.cancel(wait=False)
        self._recorder.cancel()
        if streamer is not None:
            streamer.cancel(wait=True)
        if saved_clip is not None and sel:
            output.set_clipboard_text(saved_clip)
        self._overlay.hide()
        self._tray.set_state(self._idle_tray_state())
        self._maybe_schedule_model_reload()

    def _stop_recording(
        self, *, is_command: bool, expected_seq: int | None = None
    ) -> None:
        release_t = time.monotonic()
        with self._state_lock:
            if not self._recording:
                return
            if expected_seq is not None and expected_seq != self._recording_seq:
                return
            self._recording = False
            self._recording_mode = ""
            streamer = self._streamer
            self._streamer = None
            fg_app = self._fg_app
            selection = self._selection
            saved_clip = self._saved_clipboard
            self._selection = ""
            self._saved_clipboard = None
            queued_job = not (is_command and not llm.is_available(self._data))
            if queued_job:
                self._transcribing += 1

        if streamer is not None:
            streamer.stop_polling()
        samples = self._recorder.stop()
        cfg = self._cfg_store.snapshot()
        audio.play_sound("stop", bool(cfg.get("zvuky", True)), self._data)
        self._tray.set_state(self._idle_tray_state())

        if not queued_job:
            if streamer is not None:
                streamer.cancel()
            if saved_clip is not None and selection:
                output.set_clipboard_text(saved_clip)
            self._overlay.toast(
                "Příkazy potřebují API klíč pro Claude (viz README)",
                kind="error",
            )
            self._maybe_schedule_model_reload()
            return

        if not is_command and bool(cfg.get("ai_uprava", False)) and llm.is_available(self._data):
            self._ai.warm_up_async()

        self._overlay.show("prepisuji")
        job_type = "command" if is_command else "dictation"
        self._work_queue.put(
            (job_type, (samples, cfg, fg_app, selection, saved_clip, release_t, streamer))
        )

    def _load_whisper_bg(self) -> None:
        try:
            self._whisper.start(timeout_s=60.0)
            with self._state_lock:
                self._model_loaded = True
                stopping = self._stopping
                recording = self._recording
                transcribing = self._transcribing
            if not stopping:
                if not recording:
                    self._tray.set_state(self._idle_tray_state())
                else:
                    self._tray.refresh_menu()
                if not recording and transcribing == 0:
                    self._overlay.toast(
                        "Mluvka je připravená. Drž pravý Ctrl a mluv.",
                        kind="info",
                    )
            log.info("whisper-server připraven na portu %d", self._whisper.port)
        except Exception as exc:
            self._whisper_error = exc
            with self._state_lock:
                stopping = self._stopping
            if not stopping:
                log.exception("Start whisper-serveru selhal: %s", exc)
                self._tray.refresh_menu()
                self._overlay.toast("Chybí whisper-server nebo model", kind="error")
        finally:
            self._whisper_ready.set()
            self._maybe_schedule_model_reload()
            if self._model_loaded and self._whisper_error is None:
                self._maybe_preload_ai_client()

    def _maybe_preload_ai_client(self) -> None:
        with self._state_lock:
            if self._stopping:
                return
        cfg = self._cfg_store.snapshot()
        if not bool(cfg.get("ai_uprava", False)) or not llm.is_available(self._data):
            return
        threading.Thread(
            target=self._preload_ai_client_bg,
            name="mluvka-ai-preload",
            daemon=True,
        ).start()

    def _preload_ai_client_bg(self) -> None:
        with self._state_lock:
            if self._stopping:
                return
        try:
            self._ai._get_client()
        except Exception as exc:
            log.debug("Přednačtení Claude klienta selhalo: %s", type(exc).__name__)

    def _worker_loop(self) -> None:
        while True:
            item = self._work_queue.get()
            if item is None:
                break
            kind, payload = item
            try:
                if kind == "paste_last":
                    self._handle_paste_last(from_tray=bool(payload))
                elif kind == "reload_model":
                    self._handle_model_reload(Path(payload))
                elif kind in ("dictation", "command"):
                    self._handle_transcription_job(kind, payload)
            except Exception as exc:
                log.exception("Chyba ve worker vlákně (%s): %s", kind, type(exc).__name__)
                self._toast_if_idle("Zpracování přepisu selhalo", kind="error")

    def _hide_if_idle(self) -> None:
        """Worker nesmí schovat overlay nahrávání, které mezitím začalo."""
        with self._state_lock:
            recording = self._recording
        if not recording:
            self._overlay.hide()

    def _toast_if_idle(self, msg: str, *, kind: str) -> None:
        with self._state_lock:
            recording = self._recording
        if recording:
            log.info("Hláška potlačena kvůli běžícímu nahrávání: %s", msg)
        else:
            self._overlay.toast(msg, kind=kind)

    def _handle_model_reload(self, target: Path) -> None:
        try:
            with self._state_lock:
                if self._stopping:
                    return
                if self._pending_model_path is not None:
                    target = self._pending_model_path
                    self._pending_model_path = None
                active = self._active_model_path
            if target == active:
                return
            log.info("Přepínám whisper-server na model: %s", target.name)
            try:
                self._whisper.close()
            except Exception:
                log.warning("Zavření původního whisper-serveru selhalo", exc_info=True)
            new_server = asr.WhisperServer(self._data, model_path=target)
            try:
                new_server.start(timeout_s=60.0)
            except Exception as exc:
                new_server.close()
                with self._state_lock:
                    self._whisper = new_server
                    self._active_model_path = target
                    self._model_loaded = False
                    self._whisper_error = exc
                    stopping = self._stopping
                log.exception("Přepnutí modelu na %s selhalo: %s", target.name, exc)
                if not stopping:
                    self._tray.refresh_menu()
                    self._toast_if_idle(f"Přepnutí modelu selhalo: {target.name}", kind="error")
                return
            with self._state_lock:
                if self._stopping:
                    stopping = True
                else:
                    stopping = False
                    self._whisper = new_server
                    self._active_model_path = target
                    self._model_loaded = True
                    self._whisper_error = None
            if stopping:
                new_server.close()
                return
            log.info("whisper-server přepnut na %s (port %d)", target.name, new_server.port)
            self._tray.refresh_menu()
            with self._state_lock:
                busy = self._recording or self._transcribing > 0
            if not busy:
                self._overlay.toast(f"Model přepnut: {target.name}", kind="info")
        finally:
            with self._state_lock:
                self._reloading_model = False
            self._maybe_schedule_model_reload()

    def _handle_paste_last(self, *, from_tray: bool) -> None:
        txt = self._last_transcript
        if not txt:
            return
        if from_tray:
            # Kliknutím do lišty získá popředí skryté okno pystray, Ctrl+V by šlo do prázdna.
            output.set_clipboard_text(txt)
            self._overlay.toast("Poslední přepis je ve schránce, vlož ho Ctrl+V", kind="info")
            return
        if not output.paste_text(txt):
            self._overlay.toast("Vložení se nepovedlo, text je ve schránce", kind="error")

    def _handle_transcription_job(self, kind: str, payload: tuple[Any, ...]) -> None:
        if len(payload) == 5:
            samples, cfg, fg_app, selection, saved_clip = payload
            release_t = time.monotonic()
            streamer: asr.StreamingTranscriber | None = None
        else:
            samples, cfg, fg_app, selection, saved_clip, release_t, streamer = payload
        audio_s = len(samples) / float(asr.SAMPLE_RATE) if len(samples) > 0 else 0.0
        command_clipboard_handled = kind != "command"

        try:
            self._whisper_ready.wait()
            with self._state_lock:
                if self._stopping:
                    if streamer is not None:
                        streamer.cancel()
                    return
            if self._whisper_error is not None or not self._model_loaded:
                if streamer is not None:
                    streamer.cancel()
                self._toast_if_idle("Chybí whisper-server nebo model", kind="error")
                return

            if asr.peak_block_rms_dbfs(samples) < asr.SILENCE_FLOOR_DBFS:
                if streamer is not None:
                    streamer.cancel()
                self._toast_if_idle(
                    "Mikrofon nic nezachytil — zkontroluj vstup",
                    kind="error",
                )
                return

            t0 = time.monotonic()
            try:
                if streamer is not None:
                    raw = streamer.finish(samples, cfg=cfg)
                else:
                    raw = self._whisper.transcribe(samples, cfg=cfg)
            except Exception as exc:
                if streamer is not None:
                    streamer.cancel()
                with self._state_lock:
                    if self._stopping:
                        return
                # whisper-server mohl spadnout (např. reset ovladače GPU po uspání): jednou restart a nový pokus.
                log.warning("Přepis selhal (%s), restartuji whisper-server", type(exc).__name__)
                self._whisper.close()
                self._whisper.start(timeout_s=60.0)
                t0 = time.monotonic()
                raw = self._whisper.transcribe(samples, cfg=cfg)
            prepis_s = time.monotonic() - t0

            if kind == "command":
                if not llm.is_available(self._data):
                    self._toast_if_idle(
                        "Příkazy potřebují API klíč pro Claude (viz README)",
                        kind="error",
                    )
                    return
                pokyn = text.uprav(raw, cfg)
                if not pokyn:
                    self._hide_if_idle()
                    return
                t_ai0 = time.monotonic()
                try:
                    result = self._ai.proved_prikaz(pokyn, selection)
                except Exception as exc:
                    log.warning("Příkaz přes Claude selhal: %s", type(exc).__name__)
                    self._toast_if_idle("Příkaz se nepovedl, Claude neodpověděl", kind="error")
                    return
                ai_s = time.monotonic() - t_ai0
                celkem_s = time.monotonic() - release_t
                log.info(
                    "Příkaz dokončen: audio_s=%.2f prepis_s=%.2f ai_s=%.2f celkem_s=%.2f chars=%d",
                    audio_s,
                    prepis_s,
                    ai_s,
                    celkem_s,
                    len(result),
                )
                if not result:
                    self._hide_if_idle()
                    return
                command_clipboard_handled = True
                ok = output.paste_text(result, append_space=False, prev_text=saved_clip)
                if ok:
                    self._hide_if_idle()
                else:
                    self._toast_if_idle("Vložení se nepovedlo, text je ve schránce", kind="error")
                return

            if not raw.strip():
                log.info(
                    "Přepis prázdný: audio_s=%.2f prepis_s=%.2f ai_s=0.00 celkem_s=%.2f chars=0",
                    audio_s,
                    prepis_s,
                    time.monotonic() - release_t,
                )
                self._hide_if_idle()
                return

            pre_ai_prepis = (
                text.uprav(raw, cfg, apply_commands=False, apply_snippets=False)
                if bool(cfg.get("trenink_ukladat_zvuk", False))
                else ""
            )
            processed = text.uprav(raw, cfg)
            ai_s = 0.0
            if bool(cfg.get("ai_uprava", False)) and llm.is_available(self._data) and processed:
                priklady = pamet.recent_examples(5, data=self._data)
                t_ai0 = time.monotonic()
                processed = self._ai.upravit_text(processed, cfg, priklady=priklady)
                ai_s = time.monotonic() - t_ai0

            celkem_s = time.monotonic() - release_t
            log.info(
                "Přepis dokončen: audio_s=%.2f prepis_s=%.2f ai_s=%.2f celkem_s=%.2f chars=%d",
                audio_s,
                prepis_s,
                ai_s,
                celkem_s,
                len(processed),
            )
            if not processed.strip():
                self._hide_if_idle()
                return

            self._last_transcript = processed
            ok = output.paste_text(processed, append_space=True)
            if ok:
                self._hide_if_idle()
            else:
                self._toast_if_idle("Vložení se nepovedlo, text je ve schránce", kind="error")

            if bool(cfg.get("historie", True)):
                entry_id = uuid.uuid4().hex
                zvuk = ""
                save_audio = bool(cfg.get("trenink_ukladat_zvuk", False))
                if save_audio:
                    try:
                        zvuk = trenink.save_dictation_wav(
                            entry_id, samples, data=self._data
                        )
                    except Exception:
                        log.warning("Uložení zvuku diktátu selhalo.", exc_info=True)
                history.append_entry(
                    processed,
                    raw,
                    audio_s,
                    prepis_s,
                    fg_app,
                    entry_id=entry_id,
                    zvuk=zvuk,
                    prepis=pre_ai_prepis if (save_audio and zvuk) else "",
                    data=self._data,
                )

            # Aktuální nastavení, ne snímek z konce nahrávání: vypnutý kanál nesmí poslat ani diktát ve frontě.
            live = self._refresh_config(refresh_menu=False)
            webhook_url = str(live.get("kanal_webhook", "") or "").strip()
            if bool(live.get("kanal_zapnuto", True)) and channel.is_valid_webhook_url(webhook_url):
                channel.send_async(
                    webhook_url,
                    processed,
                    on_error=lambda: self._toast_if_idle("Kanál: odeslání selhalo", kind="error"),
                )
        finally:
            if streamer is not None and streamer.is_running:
                streamer.cancel()
            if not command_clipboard_handled and saved_clip is not None and selection:
                output.set_clipboard_text(saved_clip)
            with self._state_lock:
                self._transcribing = max(0, self._transcribing - 1)
                update_tray = (
                    not self._stopping
                    and not self._recording
                    and self._transcribing == 0
                )
            if update_tray:
                self._tray.set_state(self._idle_tray_state())
            self._maybe_schedule_model_reload()

    def _select_mic(self, name: str) -> None:
        self._refresh_config(refresh_menu=False)
        try:
            self._cfg_store.set_value("mikrofon", name)
        except config.ConfigInvalidError as exc:
            log.warning("%s", exc)
        self._tray.refresh_menu()

    def _toggle_ai(self) -> None:
        if not llm.is_available(self._data):
            return
        self._refresh_config(refresh_menu=False)
        try:
            self._cfg_store.set_value("ai_uprava", not bool(self._cfg_store.get("ai_uprava", False)))
        except config.ConfigInvalidError as exc:
            log.warning("%s", exc)
        self._tray.refresh_menu()

    def _toggle_channel(self) -> None:
        self._refresh_config(refresh_menu=False)
        if not channel.is_valid_webhook_url(str(self._cfg_store.get("kanal_webhook", "") or "")):
            self._tray.refresh_menu()
            return
        try:
            self._cfg_store.set_value(
                "kanal_zapnuto", not bool(self._cfg_store.get("kanal_zapnuto", True))
            )
        except config.ConfigInvalidError as exc:
            log.warning("%s", exc)
        self._tray.refresh_menu()

    def _toggle_sounds(self) -> None:
        self._refresh_config(refresh_menu=False)
        try:
            self._cfg_store.set_value("zvuky", not bool(self._cfg_store.get("zvuky", True)))
        except config.ConfigInvalidError as exc:
            log.warning("%s", exc)
        self._tray.refresh_menu()

    def _toggle_paused(self) -> None:
        self._paused = not self._paused
        self._hook.set_paused(self._paused)
        self._tray.set_state(self._idle_tray_state())

    def _toggle_autostart(self) -> None:
        self._refresh_config(refresh_menu=False)
        if self._cfg_store.config_invalid:
            log.warning("%s", config.CONFIG_INVALID_MESSAGE)
            self._tray.refresh_menu()
            return
        new_val = not bool(self._cfg_store.get("autostart", False))
        try:
            tray.set_autostart(new_val, data=self._data)
            self._cfg_store.set_value("autostart", new_val)
        except config.ConfigInvalidError as exc:
            log.warning("%s", exc)
        self._tray.refresh_menu()

    def _open_window(self) -> None:
        okno.launch_window(self._data)

    def _poll_config_timer(self) -> None:
        with self._state_lock:
            stopping = self._stopping
        if stopping:
            self._config_poll_after_id = None
            return
        try:
            self._refresh_config()
            self._maybe_schedule_model_reload()
        except Exception:
            log.exception("Chyba při kontrole změny konfigurace")
        self._config_poll_after_id = self._overlay.root.after(2000, self._poll_config_timer)

    def _request_quit(self) -> None:
        self._overlay.quit()

    def run(self) -> None:
        audio.ensure_sounds(self._data)
        try:
            dny = int(self._cfg_store.get("historie_dny", 30))
        except (TypeError, ValueError):
            log.warning("Neplatné historie_dny v config.json, používám 30")
            dny = 30
        try:
            history.prune_history(dny, data=self._data)
        except Exception:
            log.exception("Chyba při promazávání staré historie při startu")

        loader = threading.Thread(target=self._load_whisper_bg, name="mluvka-loader", daemon=True)
        worker = threading.Thread(target=self._worker_loop, name="mluvka-worker", daemon=True)
        loader.start()
        worker.start()

        self._tray.start()
        self._hook.start()
        self._config_poll_after_id = self._overlay.root.after(2000, self._poll_config_timer)
        try:
            self._overlay.run()
        finally:
            with self._state_lock:
                self._stopping = True
                streamer = self._streamer
                self._streamer = None
            if streamer is not None:
                streamer.cancel(wait=False)
            self._hook.stop()
            self._recorder.cancel()
            self._whisper.close()
            if streamer is not None:
                streamer.cancel(wait=True)
            self._ai.close()
            self._work_queue.put(None)
            worker.join(timeout=2.0)
            self._tray.stop()


def run(data: Path | None = None, *, open_window: bool = False) -> int:
    overlay.enable_dpi_awareness()
    h_mutex = kernel32.CreateMutexW(None, False, "Local\\Mluvka")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        if h_mutex:
            kernel32.CloseHandle(h_mutex)
        okno.launch_window(data)
        return 0

    try:
        setup_logging(data)
        cfg_store = config.ConfigStore(data)
        netguard.install(config.allowed_hosts(cfg_store.snapshot()))
        app = MluvkaApp(cfg_store, data=data)
        if open_window:
            okno.launch_window(data)
        app.run()
        return 0
    finally:
        if h_mutex:
            kernel32.CloseHandle(h_mutex)
