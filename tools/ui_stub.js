(() => {
  const params = new URLSearchParams(window.location.search);
  const isEmpty = params.get("empty") === "1";
  const isInvalidCfg = params.get("invalid") === "1";
  const doladeniParam = params.get("doladeni") || "";
  const diktatyParam = params.get("diktaty") || "";
  window.__stubCounters = { doladeniStavCalls: 0 };

  const now = new Date();
  const isoAt = (daysAgo, hh, mm) => {
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - daysAgo, hh, mm, 12);
    const off = -d.getTimezoneOffset();
    const sign = off >= 0 ? "+" : "-";
    const oh = String(Math.floor(Math.abs(off) / 60)).padStart(2, "0");
    const om = String(Math.abs(off) % 60).padStart(2, "0");
    const yyyy = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${yyyy}-${m}-${day}T${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:12${sign}${oh}:${om}`;
  };

  let history = isEmpty ? [] : [
    {
      id: "h1",
      cas: isoAt(0, 14, 32),
      aplikace: "Slack",
      audio_s: 6.4,
      prepis_s: 0.42,
      text: "Ahoj Petře, návrh rozhraní jsem nahrál do sdílené složky. Prosím mrkni ještě na kontrast tlačítek v tmavém režimu a potvrď termín zítřejšího hovoru.",
    },
    {
      id: "h2",
      cas: isoAt(0, 11, 15),
      aplikace: "Visual Studio Code",
      audio_s: 4.1,
      prepis_s: 0.31,
      text: "Přidej validaci vstupních hodnot před uložením konfigurace přes klód kód a doplň jednotkové testy.",
      surovy: "Přidej validaci vstupních hodnot před uložením konfigurace přes klód kód a doplň jednotkové testy.",
      opraveno: "Přidej validaci vstupních hodnot před uložením konfigurace přes Claude Code a doplň jednotkové testy.",
    },
    {
      id: "h3",
      cas: isoAt(0, 9, 8),
      aplikace: "Outlook",
      audio_s: 11.2,
      prepis_s: 0.58,
      text: "Dobrý den, posílám aktualizovaný harmonogram testování na příští týden. V pondělí projdeme přepis zvuku a v úterý napojení na týmový kanál, aby bylo vše připraveno před čtvrteční prezentací pro vedení projektu.",
    },
    {
      id: "h4",
      cas: isoAt(1, 17, 45),
      aplikace: "Notion",
      audio_s: 5.0,
      prepis_s: 0.35,
      text: "Poznámky ze schůzky: zrychlit spouštění modelu po probuzení systému a zkontrolovat výběr výchozího mikrofonu.",
    },
    {
      id: "h5",
      cas: isoAt(1, 10, 22),
      aplikace: "Chrome",
      audio_s: 3.3,
      prepis_s: 0.28,
      text: "Vyhledat dokumentaci k WebView2 pro nastavení ikony okna na hlavním panelu.",
    },
    {
      id: "h6",
      cas: isoAt(3, 16, 5),
      aplikace: "Word",
      audio_s: 7.8,
      prepis_s: 0.44,
      text: "Shrnutí čtvrtletních výsledků ukazuje padesátiprocentní úsporu času při psaní delších zpráv hlasem.",
    },
  ];

  let apiKeySet = !isEmpty;
  let apiKeyTail = isEmpty ? "" : "7xQ9";

  const cfg = isEmpty
    ? {
        klavesa_vk: 163,
        mikrofon: "",
        zvuky: true,
        ai_uprava: false,
        slovnik_do_promptu: true,
        slovnik: { slova: [], opravy: [] },
        zkratky: [],
        kanal_webhook: "",
        kanal_zapnuto: false,
        historie: true,
        historie_dny: 30,
        uceni_z_oprav: true,
        autostart: false,
        trenink_ukladat_zvuk: false,
        darce_id: "",
        model_soubor: "ggml-czechspeech-q5_0.bin",
        beam_size: 5,
      }
    : {
        klavesa_vk: 163,
        mikrofon: "Mikrofon (Realtek Audio)",
        zvuky: true,
        ai_uprava: true,
        slovnik_do_promptu: true,
        slovnik: {
          slova: ["Mluvka", "CzechSpeech", "Anthropic", "Kubernetes", "Brno"],
          opravy: [
            ["mlúvka", "Mluvka"],
            ["klód", "Claude"],
          ],
        },
        zkratky: [
          ["můj podpis", "S pozdravem,\nAdam Novák\nProduktový manažer"],
          ["schůzka zítra", "Potvrzuji zítřejší schůzku v 10:00 online."],
        ],
        kanal_webhook: "https://hooks.slack.com/services/T000/B000/XXX",
        kanal_zapnuto: true,
        historie: true,
        historie_dny: 30,
        uceni_z_oprav: true,
        autostart: true,
        trenink_ukladat_zvuk: diktatyParam !== "off",
        darce_id: "stubdarce1234",
        model_soubor: "ggml-czechspeech-q5_0.bin",
        beam_size: 5,
      };

  let diktatyStubs = (isEmpty || diktatyParam === "off" || diktatyParam === "on_empty")
    ? []
    : [
        {
          id: "d1",
          cas: isoAt(0, 14, 22),
          audio_s: 6.4,
          prepis: "ehm tak já to zkusím poslat ještě dneska odpoledne petře",
          overeno: false,
          overeno_text: "",
        },
        {
          id: "d2",
          cas: isoAt(0, 11, 5),
          audio_s: 4.8,
          prepis: "no tak schůzka platí zítra v devět třicet v brně",
          overeno: true,
          overeno_text: "No tak schůzka platí zítra v devět třicet v Brně.",
        },
        {
          id: "d3",
          cas: isoAt(1, 16, 40),
          audio_s: 8.2,
          prepis: "prosím tě koukni na ten pull request v repozitáři mluvka",
          overeno: false,
          overeno_text: "",
        },
      ];

  let pamet = isEmpty
    ? []
    : [
        { z: "klód kód", na: "Claude Code", count: 3, cas: isoAt(0, 12, 10) },
        { z: "antropik", na: "Anthropic", count: 2, cas: isoAt(1, 15, 20) },
        { z: "dokr", na: "Docker", count: 1, cas: isoAt(2, 9, 45) },
      ];

  const sampleSentences = Array.from({ length: 98 }, (_, i) => (
    i === 0 ? "Rychlá hnědá liška skáče přes líného psa."
      : i === 1 ? "Prosím otevři Claude Code a zkontroluj poslední testy."
      : i === 14 ? "Zítra ráno pošlu přehled výsledků celé skupině."
      : `Vzorová tréninková věta číslo ${i + 1} pro ověření českého přepisu.`
  ));
  const treninkRecorded = {};
  if (!isEmpty) {
    for (let i = 1; i <= 14; i++) {
      treninkRecorded[String(i).padStart(3, "0")] = 2.4;
    }
  }
  let treninkMeasurement = isEmpty
    ? null
    : {
        date: isoAt(0, 13, 0),
        n: 14,
        wer: 0.042,
        worst: [
          {
            code: "002",
            ref: "Prosím otevři Claude Code a zkontroluj poslední testy.",
            hyp: "Prosím otevři klód kód a zkontroluj poslední test.",
            errors: 3,
            wer: 0.375,
            ref_diff: [
              { word: "Prosím", ok: true },
              { word: "otevři", ok: true },
              { word: "Claude", ok: false },
              { word: "Code", ok: false },
              { word: "a", ok: true },
              { word: "zkontroluj", ok: true },
              { word: "poslední", ok: true },
              { word: "testy.", ok: false },
            ],
            hyp_diff: [
              { word: "Prosím", ok: true },
              { word: "otevři", ok: true },
              { word: "klód", ok: false },
              { word: "kód", ok: false },
              { word: "a", ok: true },
              { word: "zkontroluj", ok: true },
              { word: "poslední", ok: true },
              { word: "test.", ok: false },
            ],
          },
        ],
      };

  let recRunning = false;
  let recIdx = 0;
  let measRunning = false;
  let selectedModel = "ggml-czechspeech-q5_0.bin";
  const modelCatalog = isEmpty ? [] : [
    {
      soubor: "ggml-czechspeech-q5_0.bin",
      nazev: "CzechSpeech (výchozí)",
      vychozi: true,
      wer_text: "Common Voice: 7,1 % · FLEURS: 6,4 %",
      velikost_mb: 547.4,
    },
    {
      soubor: "ggml-osobni-v1-q5_0.bin",
      nazev: "ggml-osobni-v1-q5_0.bin",
      vychozi: false,
      wer_text: "Common Voice: 5,8 % · FLEURS: 5,9 %",
      velikost_mb: 548.1,
    },
  ];
  const makeDoladeniState = (mode) => {
    const sampleLog = [
      "Start tréninku 'osobni-20261113-0915': volná VRAM 4.82/6.00 GB",
      "Plán: total_steps=87, warmup=4, batch=4, accum=4, lr=0.0001",
      "Krok 35/87 | loss=0.0680 | 14.32 s/krok | ETA 12m 24s | lr=6.27e-05",
      "Krok 36/87 | loss=0.0641 | 14.35 s/krok | ETA 12m 11s | lr=6.15e-05",
      "Krok 37/87 | loss=0.0604 | 14.31 s/krok | ETA 11m 55s | lr=6.02e-05",
      "Krok 38/87 | loss=0.0569 | 14.29 s/krok | ETA 11m 40s | lr=5.90e-05",
      "Krok 39/87 | loss=0.0548 | 14.30 s/krok | ETA 11m 26s | lr=5.78e-05",
      "Krok 40/87 | loss=0.0521 | 14.31 s/krok | ETA 11m 12s | lr=5.66e-05",
    ];
    if (mode === "malo") {
      return { stav: "nic", nahrano: 24, potreba: 50, odhad_min: 20 };
    }
    if (mode === "pripraveno") {
      return { stav: "nic", nahrano: 64, potreba: 50, odhad_min: 27 };
    }
    if (mode === "bezi_priprava") {
      return {
        stav: "bezi",
        faze: "priprava",
        krok: 0,
        celkem: 0,
        loss: null,
        eta_s: null,
        pid: 12345,
        name: "osobni-20261113-0915",
        zacatek: "2026-11-13T09:15:00+01:00",
        konec: null,
        log_tail: ["Připravuji vety_train/vety_test…"],
        vysledek: null,
        chyba: null,
      };
    }
    if (mode === "bezi") {
      return {
        stav: "bezi",
        faze: "doladeni",
        krok: 40,
        celkem: 87,
        loss: 0.052,
        eta_s: 700,
        pid: 12345,
        name: "osobni-20261113-0915",
        zacatek: "2026-11-13T09:15:00+01:00",
        konec: null,
        log_tail: sampleLog,
        vysledek: null,
        chyba: null,
      };
    }
    if (mode === "hotovo") {
      return {
        stav: "hotovo",
        faze: "mereni",
        krok: 87,
        celkem: 87,
        loss: 0.048,
        eta_s: 0,
        pid: 12345,
        name: "osobni-20261113-0915",
        zacatek: "2026-11-13T09:15:00+01:00",
        konec: "2026-11-13T09:39:00+01:00",
        log_tail: sampleLog,
        vysledek: {
          pred: { vety_test: 0.084, fleurs: 0.059 },
          po: { vety_test: 0.051, fleurs: 0.061 },
          n_vety: 12,
          lepsi: true,
          model_soubor: "ggml-osobni-20261113-0915-q8_0.bin",
        },
        chyba: null,
      };
    }
    if (mode === "hotovo_stejny") {
      return {
        stav: "hotovo",
        faze: "mereni",
        krok: 87,
        celkem: 87,
        loss: 0.061,
        eta_s: 0,
        pid: 12345,
        name: "osobni-20261113-0915",
        zacatek: "2026-11-13T09:15:00+01:00",
        konec: "2026-11-13T09:37:00+01:00",
        log_tail: sampleLog,
        vysledek: {
          pred: { vety_test: 0.084, fleurs: 0.059 },
          po: { vety_test: 0.081, fleurs: 0.072 },
          n_vety: 12,
          lepsi: false,
          model_soubor: null,
        },
        chyba: null,
      };
    }
    if (mode === "chyba") {
      return {
        stav: "chyba",
        faze: "doladeni",
        krok: 0,
        celkem: 0,
        loss: null,
        eta_s: null,
        pid: 12345,
        name: "osobni-20261113-0915",
        zacatek: "2026-11-13T09:15:00+01:00",
        konec: "2026-11-13T09:15:08+01:00",
        log_tail: ["Chyba: Na GPU je volných jen 2.10 GB z 6.00 GB."],
        vysledek: null,
        chyba: "Grafická karta nemá dost volné paměti. Zavři programy, které ji používají, a zkus to znovu.",
      };
    }
    if (mode === "zastaveno") {
      return {
        stav: "zastaveno",
        faze: "doladeni",
        krok: 18,
        celkem: 87,
        loss: 0.142,
        eta_s: 980,
        pid: 12345,
        name: "osobni-20261113-0915",
        zacatek: "2026-11-13T09:15:00+01:00",
        konec: "2026-11-13T09:19:30+01:00",
        log_tail: ["Krok 18/87 | loss=0.1420 | 14.30 s/krok | ETA 16 min 20 s"],
        vysledek: null,
        chyba: null,
      };
    }
    const nahrano = Object.keys(treninkRecorded).length;
    return {
      stav: "nic",
      nahrano,
      potreba: 50,
      odhad_min: Math.ceil(((nahrano - Math.floor(nahrano / 5)) * 5 + 1000) / 16 * 14.5 / 60) + 8,
    };
  };

  let doladeniState = makeDoladeniState(doladeniParam);
  if (doladeniParam === "hotovo") {
    modelCatalog.push({
      soubor: "ggml-osobni-20261113-0915-q8_0.bin",
      nazev: "Doladěný na tvém hlase (13. 11.)",
      vychozi: false,
      wer_text: "Tvůj hlas: 5,1 % · FLEURS: 6,1 %",
      velikost_mb: 834.2,
    });
  }

  const modelsPayload = () => ({
    ok: true,
    aktivni: selectedModel,
    models: modelCatalog.map((m) => ({ ...m, vybrany: m.soubor === selectedModel })),
  });

  const entryDisplayText = (e) => (e.opraveno && String(e.opraveno).trim()) ? e.opraveno : e.text;

  const pametView = () => {
    const existing = new Set((cfg.slovnik.opravy || []).map(([a, b]) => `${a.toLowerCase()}::${b.toLowerCase()}`));
    return pamet.map((it) => ({
      ...it,
      in_slovnik: existing.has(`${it.z.toLowerCase()}::${it.na.toLowerCase()}`),
    }));
  };

  const computeStats = () => {
    if (!history.length) return { words: 0, count: 0, audio_s: 0, saved_s: 0, wpm: 0 };
    const words = history.reduce((acc, e) => acc + entryDisplayText(e).trim().split(/\s+/).filter(Boolean).length, 0) + 1420;
    const audio_s = history.reduce((acc, e) => acc + Number(e.audio_s || 0), 0) + 610;
    const saved_s = Math.max(0, (words / 40) * 60 - audio_s);
    const wpm = audio_s > 0 ? words / (audio_s / 60) : 0;
    return {
      words,
      count: history.length + 38,
      audio_s: Math.round(audio_s * 100) / 100,
      saved_s: Math.round(saved_s * 10) / 10,
      wpm: Math.round(wpm * 10) / 10,
    };
  };

  const buildAllowedHosts = () => {
    const hosts = [];
    if (cfg.ai_uprava && apiKeySet) hosts.push("api.anthropic.com");
    if (cfg.kanal_zapnuto && cfg.kanal_webhook) {
      try { hosts.push(new URL(cfg.kanal_webhook).hostname); } catch (_) {}
    }
    return hosts;
  };

  const statePayload = () => ({
    ok: true,
    config: JSON.parse(JSON.stringify(cfg)),
    config_invalid: isInvalidCfg,
    api_key_set: apiKeySet,
    api_key_tail: apiKeyTail,
    env_key: false,
    devices: ["Mikrofon (Realtek Audio)", "Shure MV7 USB", "Headset Mic"],
    app_running: !isEmpty,
    allowed_hosts: buildAllowedHosts(),
    stats: computeStats(),
    version: "0.3.0",
  });

  const stripDia = (s) => s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

  window.pywebview = {
    api: {
      async get_state() { return statePayload(); },
      async save_settings(patch) {
        if (isInvalidCfg) return { ok: false, error: "config.json obsahuje chybu. Oprav ho, nebo ho smaž a Mluvka vytvoří nový." };
        if (patch.kanal_webhook && !String(patch.kanal_webhook).startsWith("https://")) {
          return { ok: false, error: "Adresa kanálu musí začínat https:// a mít platnou doménu." };
        }
        if (patch.slovnik) {
          if (patch.slovnik.slova) cfg.slovnik.slova = [...patch.slovnik.slova];
          if (patch.slovnik.opravy) cfg.slovnik.opravy = patch.slovnik.opravy.map((x) => [...x]);
        }
        for (const [k, v] of Object.entries(patch)) {
          if (k !== "slovnik") cfg[k] = v;
        }
        return statePayload();
      },
      async set_api_key(key) {
        if (isInvalidCfg) return { ok: false, error: "config.json obsahuje chybu. Oprav ho, nebo ho smaž a Mluvka vytvoří nový." };
        const k = String(key || "").trim();
        apiKeySet = Boolean(k);
        apiKeyTail = k.length >= 4 ? k.slice(-4) : k;
        return statePayload();
      },
      async test_api_key() { return { ok: true }; },
      async test_webhook() { return { ok: true }; },
      async get_history(query = "", offset = 0, limit = 100) {
        const q = stripDia(String(query || "").trim());
        const filtered = q ? history.filter((x) => stripDia(entryDisplayText(x)).includes(q)) : history;
        const slice = filtered.slice(offset, offset + limit);
        return { ok: true, items: slice, total: filtered.length, has_more: offset + slice.length < filtered.length };
      },
      async delete_history(entry_id = "", cas = "", text = null) {
        const idx = history.findIndex((x) => (entry_id ? x.id === entry_id : x.cas === cas && (text === null || x.text === text)));
        if (idx >= 0) history.splice(idx, 1);
        return { ok: true, deleted: idx >= 0, stats: computeStats() };
      },
      async edit_history(entry_id = "", new_text = "", cas = "", old_text = null) {
        const entry = history.find((x) => (entry_id ? x.id === entry_id : x.cas === cas && (old_text === null || x.text === old_text)));
        if (!entry) return { ok: false, error: "Záznam nebyl nalezen." };
        const prev = entryDisplayText(entry);
        entry.opraveno = new_text.trim();
        const suggestions = prev !== entry.opraveno ? [["ukázková chyba", "opravený výraz"]] : [];
        if (suggestions.length) {
          pamet.unshift({ z: suggestions[0][0], na: suggestions[0][1], count: 1, cas: isoAt(0, 14, 40) });
        }
        return { ok: true, entry, zmeny: suggestions, suggestions, pamet: pametView(), stats: computeStats() };
      },
      async add_oprava(z, na) {
        cfg.slovnik.opravy.push([z, na]);
        return { ...statePayload(), added: true, pamet: pametView() };
      },
      async get_pamet() { return { ok: true, items: pametView() }; },
      async clear_pamet() { const n = pamet.length; pamet = []; return { ok: true, removed: n, items: [] }; },
      async clear_history() { const n = history.length; history = []; return { ok: true, removed: n, stats: computeStats() }; },
      async copy_text() { return { ok: true }; },
      async copy_slovnik_prompt() { return { ok: true }; },
      async preview_slovnik_import(txt) {
        try {
          const m = String(txt || "").match(/\{[\s\S]*\}/);
          if (!m) throw new Error("bad");
          const obj = JSON.parse(m[0]);
          const wSet = new Set(cfg.slovnik.slova.map((x) => x.toLowerCase()));
          const pSet = new Set(cfg.slovnik.opravy.map(([a, b]) => `${a.toLowerCase()}::${b.toLowerCase()}`));
          const slova = (obj.slova || []).filter((w) => !wSet.has(String(w).toLowerCase()));
          const opravy = (obj.opravy || []).filter(([a, b]) => !pSet.has(`${a.toLowerCase()}::${b.toLowerCase()}`));
          const existing_count = ((obj.slova || []).length - slova.length) + ((obj.opravy || []).length - opravy.length);
          return { ok: true, slova, opravy, existing_count };
        } catch (_) {
          return { ok: false, error: "Odpověď se nepodařilo přečíst. Zkontroluj, že je to JSON ve tvaru z promptu." };
        }
      },
      async apply_slovnik_import(dataObj) {
        const s = dataObj.slova || [];
        const o = dataObj.opravy || [];
        cfg.slovnik.slova.push(...s);
        cfg.slovnik.opravy.push(...o);
        return { ...statePayload(), added_slova: s.length, added_opravy: o.length, pamet: pametView() };
      },
      async get_trenink_state() {
        const cnt = Object.keys(treninkRecorded).length;
        return {
          ok: true,
          sentences: sampleSentences,
          total: sampleSentences.length,
          recorded: { ...treninkRecorded },
          recorded_count: cnt,
          next_index: cnt < sampleSentences.length ? cnt + 1 : 1,
          measurement: treninkMeasurement,
          measuring: { running: measRunning, done: 0, total: cnt, result: treninkMeasurement, error: "" },
        };
      },
      async trenink_start(idx) { recRunning = true; recIdx = idx; return { ok: true, index: idx }; },
      async trenink_stop() {
        recRunning = false;
        const code = String(recIdx).padStart(3, "0");
        treninkRecorded[code] = 2.1;
        const cnt = Object.keys(treninkRecorded).length;
        return { ok: true, saved: { code }, trenink: { recorded: { ...treninkRecorded }, recorded_count: cnt, next_index: cnt + 1 } };
      },
      async trenink_cancel() { recRunning = false; return { ok: true }; },
      async trenink_play() { return { ok: true }; },
      async diktaty_k_overeni(offset = 0, limit = 50) {
        const off = Math.max(0, Number(offset || 0));
        const lim = Math.min(500, Math.max(1, Number(limit || 50)));
        const verifiedCount = diktatyStubs.filter((d) => d.overeno && d.overeno_text).length;
        const verifiedS = diktatyStubs
          .filter((d) => d.overeno && d.overeno_text)
          .reduce((acc, d) => acc + Number(d.audio_s || 0), 0);
        const totalS = diktatyStubs.reduce((acc, d) => acc + Number(d.audio_s || 0), 0);
        const items = diktatyStubs.slice(off, off + lim).map((d) => ({ ...d }));
        return {
          ok: true,
          ukladat_zvuk: Boolean(cfg.trenink_ukladat_zvuk),
          items,
          total: diktatyStubs.length,
          verified_count: verifiedCount,
          verified_s: Number(verifiedS.toFixed(2)),
          total_s: Number(totalS.toFixed(2)),
          has_more: (off + items.length) < diktatyStubs.length,
        };
      },
      async overit_diktat(id, text) {
        const idx = diktatyStubs.findIndex((d) => d.id === id);
        if (idx < 0) return { ok: false, error: "Záznam historie nebyl nalezen." };
        const raw = String(text ?? "");
        if (raw.length > 5000) return { ok: false, error: "Přepis může mít nejvýše 5000 znaků." };
        const cleaned = raw.trim();
        if (!cleaned) return { ok: false, error: "Přepis nesmí být prázdný." };
        diktatyStubs[idx] = { ...diktatyStubs[idx], overeno: true, overeno_text: cleaned };
        const verifiedCount = diktatyStubs.filter((d) => d.overeno && d.overeno_text).length;
        const verifiedS = diktatyStubs
          .filter((d) => d.overeno && d.overeno_text)
          .reduce((acc, d) => acc + Number(d.audio_s || 0), 0);
        return {
          ok: true,
          entry: { ...diktatyStubs[idx] },
          verified_count: verifiedCount,
          total: diktatyStubs.length,
          verified_s: Number(verifiedS.toFixed(2)),
        };
      },
      async smazat_diktat_zvuk(id) {
        const idx = diktatyStubs.findIndex((d) => d.id === id);
        if (idx < 0) return { ok: false, error: "Záznam historie nebyl nalezen." };
        diktatyStubs = diktatyStubs.slice(0, idx).concat(diktatyStubs.slice(idx + 1));
        const verifiedCount = diktatyStubs.filter((d) => d.overeno && d.overeno_text).length;
        const verifiedS = diktatyStubs
          .filter((d) => d.overeno && d.overeno_text)
          .reduce((acc, d) => acc + Number(d.audio_s || 0), 0);
        return {
          ok: true,
          deleted: true,
          verified_count: verifiedCount,
          total: diktatyStubs.length,
          verified_s: Number(verifiedS.toFixed(2)),
        };
      },
      async prehrat_diktat(id) {
        const idx = diktatyStubs.findIndex((d) => d.id === id);
        if (idx < 0) return { ok: false, error: "Nahrávka diktátu nebyla nalezena." };
        return { ok: true };
      },
      async get_level() { return { ok: true, recording: recRunning, level: recRunning ? 0.62 : 0, elapsed_s: recRunning ? 2.4 : 0 }; },
      async zmerit() { return { ok: true, running: false, done: 14, total: 14, result: treninkMeasurement, error: "" }; },
      async zmerit_stav() { return { ok: true, running: false, done: 14, total: 14, result: treninkMeasurement, error: "" }; },
      async vytvor_dar(inc, souhlasy) {
        if (!Array.isArray(souhlasy) || !souhlasy.every(Boolean)) return { ok: false, error: "Potvrď souhlas." };
        return { ok: true, path: "C:\\Users\\Demo\\Documents\\Mluvka-dar-2026-11-13.zip", filename: "Mluvka-dar-2026-11-13.zip", folder: "C:\\Users\\Demo\\Documents" };
      },
      async modely() { return modelsPayload(); },
      async vybrat_model(soubor) {
        if (!modelCatalog.some((m) => m.soubor === soubor)) {
          return { ok: false, error: `Model „${soubor}“ není mezi dostupnými modely.` };
        }
        selectedModel = soubor;
        cfg.model_soubor = soubor;
        return { ...modelsPayload(), config: JSON.parse(JSON.stringify(cfg)) };
      },
      async doladeni_stav() {
        window.__stubCounters.doladeniStavCalls += 1;
        if (!doladeniParam) {
          const nahrano = Object.keys(treninkRecorded).length;
          doladeniState = {
            stav: "nic",
            nahrano,
            potreba: 50,
            odhad_min: Math.ceil(((nahrano - Math.floor(nahrano / 5)) * 5 + 1000) / 16 * 14.5 / 60) + 8,
          };
        }
        return { ok: true, ...JSON.parse(JSON.stringify(doladeniState)) };
      },
      async doladeni_spustit() {
        doladeniState = makeDoladeniState("bezi");
        return { ok: true, ...JSON.parse(JSON.stringify(doladeniState)) };
      },
      async doladeni_zastavit() {
        doladeniState = makeDoladeniState("zastaveno");
        return { ok: true, ...JSON.parse(JSON.stringify(doladeniState)) };
      },
      async doladeni_pouzit() {
        if (doladeniState.stav !== "hotovo" || !doladeniState.vysledek || !doladeniState.vysledek.lepsi) {
          return { ok: false, error: "Doladěný model není k dispozici." };
        }
        const soubor = doladeniState.vysledek.model_soubor;
        selectedModel = soubor;
        cfg.model_soubor = soubor;
        return { ...modelsPayload(), config: JSON.parse(JSON.stringify(cfg)) };
      },
      async otevrit_slozku() { return { ok: true }; },
      async open_data_folder() { return { ok: true }; },
      async start_app() { return { ok: true, started: true }; },
      async open_url() { return { ok: true }; },
    },
  };
  window.dispatchEvent(new Event("pywebviewready"));
})();
