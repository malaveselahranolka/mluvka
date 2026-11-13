(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);
  const lerp = (a, b, t) => a + (b - a) * t;

  /* ---------- jeden scrollovací cyklus pro celou stránku ---------- */
  const scrollJobs = [];
  let ticking = false;
  const runJobs = () => {
    ticking = false;
    for (const job of scrollJobs) job();
  };
  const onScroll = () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(runJobs);
  };
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll, { passive: true });

  /* ---------- lišta ---------- */
  const bar = $("bar");
  scrollJobs.push(() => bar.classList.toggle("stuck", window.scrollY > 8));

  /* ---------- mobilní menu ---------- */
  const menuBtn = $("menu-btn");
  const nav = $("nav");
  const closeMenu = () => {
    nav.classList.remove("open");
    menuBtn.setAttribute("aria-expanded", "false");
  };
  menuBtn.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    menuBtn.setAttribute("aria-expanded", String(open));
  });
  nav.addEventListener("click", (e) => {
    if (e.target.tagName === "A") closeMenu();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && nav.classList.contains("open")) {
      closeMenu();
      menuBtn.focus();
    }
  });

  /* ---------- vstupní animace ---------- */
  requestAnimationFrame(() => document.body.classList.add("loaded"));

  /* ---------- pás aplikací: zdvojit kvůli plynulé smyčce ---------- */
  const track = $("track");
  track.innerHTML += track.innerHTML;

  /* ---------- scény, které se přehrají samy, když jsou vidět ---------- */
  const desktopPin = () => window.matchMedia("(min-width: 861px)").matches && !reduce.matches;
  // render(p) dostane průběh 0..1 za dobu ms; spustí se při každém vstupu do obrazu
  const playOnView = (el, ms, render, threshold = 0.45) => {
    let raf = 0;
    const run = () => {
      cancelAnimationFrame(raf);
      if (reduce.matches) return render(1);
      const t0 = performance.now();
      const step = (now) => {
        const p = clamp01((now - t0) / ms);
        render(p);
        if (p < 1) raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    };
    new IntersectionObserver((en) => en.forEach((e) => e.isIntersecting && run()), { threshold }).observe(el);
    return run;
  };

  /* hero: věta se nadiktuje bez diakritiky, pak na písmena spadnou háčky a čárky */
  const HACKY = [
    "Příliš žluťoučký kůň úpěl ďábelské ódy.",
    "Ve středu pošlu Jiřímu účtenky z Brna.",
    "Děkuji, zítra ráno se ozvu s návrhem.",
  ];
  const hLine = $("hacky-line"), hPill = $("hacky-pill"), hState = $("hacky-state");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // NFD rozloží „ř" na „r" + kombinující háček; podle toho víme, kam co spadne
  const ZNAK = { "\u030C": "ˇ", "\u0301": "´", "\u030A": "˚" };
  const build = (veta) => {
    hLine.textContent = "";
    const marks = [];
    veta.split(" ").forEach((slovo, i) => {
      const w = document.createElement("span");
      w.className = "w";
      w.style.animationDelay = `${i * 90}ms`;
      for (const ch of slovo) {
        const [base, ...comb] = ch.normalize("NFD");
        const c = document.createElement("span");
        c.className = "c";
        c.textContent = comb.length ? base : ch;
        if (comb.length) {
          c.classList.add("bare");
          marks.push([c, ch, ZNAK[comb[0]] || "´"]);
        }
        w.appendChild(c);
      }
      hLine.append(w, " ");
    });
    return marks;
  };
  let heroVisible = true;
  new IntersectionObserver((en) => (heroVisible = en[0].isIntersecting)).observe(hLine);
  (async () => {
    if (reduce.matches) {
      hLine.textContent = HACKY[0];
      hPill.classList.add("idle");
      hState.textContent = "Hotovo";
      return;
    }
    for (let i = 0; ; i = (i + 1) % HACKY.length) {
      while (!heroVisible || document.hidden) await sleep(500);
      hPill.classList.remove("idle");
      hState.textContent = "Mluvka poslouchá";
      const marks = build(HACKY[i]);
      await sleep(HACKY[i].split(" ").length * 90 + 900);
      hState.textContent = "Doplňuji háčky a čárky";
      for (const [c, plny, znak] of marks) {
        const m = document.createElement("span");
        m.className = "mark";
        m.textContent = znak;
        c.appendChild(m);
        // po dopadu vyměnit za skutečné písmeno, ať sedí kerning a tvar
        setTimeout(() => {
          c.textContent = plny;
          c.classList.remove("bare");
          c.classList.add("landed");
          setTimeout(() => c.classList.remove("landed"), 500);
        }, 480);
        await sleep(110);
      }
      await sleep(700);
      hPill.classList.add("idle");
      hState.textContent = "Vloženo";
      await sleep(2600);
    }
  })();

  /* scéna 1: rychlost. Stejná věta psaná a mluvená, čas zrychlený 2,5×. */
  const slowText = $("slow-text"), slowTime = $("slow-time");
  const fastTime = $("fast-time");
  const raceText = $("race-text");
  const raceVideo = $("race-video");
  const VETA_RACE = "Ahoj, posílám shrnutí z porady. Spuštění necháváme na pátek a podklady doplním do zítřka.";
  const RACE_WORDS = VETA_RACE.split(" ");
  const SLOW_S = (RACE_WORDS.length / 40) * 60;  // skutečné sekundy při 40 slovech za minutu
  const FAST_S = (RACE_WORDS.length / 150) * 60; // při 150
  const fmt = (x) => `${x.toFixed(1).replace(".", ",")} s`;
  const replayRace = playOnView($("race"), (SLOW_S * 1000) / 2.5, (p) => {
    const t = p * SLOW_S;
    const pf = clamp01(t / FAST_S);
    slowText.textContent = VETA_RACE.slice(0, Math.round(p * VETA_RACE.length));
    raceText.textContent = RACE_WORDS.slice(0, Math.round(pf * RACE_WORDS.length)).join(" ");
    slowTime.textContent = p < 1 ? fmt(t) : `Hotovo za ${fmt(SLOW_S)}`;
    fastTime.textContent = pf < 1 ? fmt(t) : `Hotovo za ${fmt(FAST_S)}`;
    slowTime.classList.toggle("done", p >= 1);
    fastTime.classList.toggle("done", pf >= 1);
  });
  $("race-replay").addEventListener("click", replayRace);

  /* scéna 2: soukromí. Hlas vyrazí k cizímu serveru, narazí na hranici
     a vrátí se dovnitř jako text. */
  const privOuter = $("priv-outer");
  const flyer = $("flyer");
  const flyAudio = $("fly-audio");
  const flyText = $("fly-text");
  const wall = $("wall");
  const cloud = $("cloud");
  const nodeMic = $("node-mic");
  const nodeGpu = $("node-gpu");
  const nodeTxt = $("node-txt");

  const privReset = () => {
    [flyer, flyAudio, flyText].forEach((el) => (el.style.cssText = el === flyer ? "" : ""));
    flyAudio.style.opacity = "1";
    flyText.style.opacity = "0";
    wall.classList.remove("hit");
    cloud.classList.remove("off");
    [nodeMic, nodeGpu, nodeTxt].forEach((n) => n.classList.remove("lit"));
  };

  const privRender = (p) => {
    if (!desktopPin()) {
      privReset();
      nodeTxt.classList.add("lit");
      flyText.style.opacity = "1";
      flyAudio.style.opacity = "0";
      flyer.style.transform = "translateX(-50%)";
      return;
    }
    const up = clamp01((p - 0.12) / 0.26);   // hlas stoupá k serveru
    const bounce = clamp01((p - 0.38) / 0.1); // náraz na hranici
    const down = clamp01((p - 0.48) / 0.26);  // vrací se jako text dovnitř

    const y = lerp(0, -118, up) + lerp(0, 96, down);
    const squash = 1 - bounce * (1 - down) * 0.3;
    flyer.style.transform = `translateX(-50%) translateY(${y}px) scaleY(${squash.toFixed(3)})`;
    flyAudio.style.opacity = String(1 - down);
    flyText.style.opacity = String(down);
    wall.classList.toggle("hit", bounce > 0.25 && down < 0.5);
    cloud.classList.toggle("off", bounce > 0.4);
    nodeMic.classList.toggle("lit", p > 0.04 && up < 1);
    nodeGpu.classList.toggle("lit", down > 0.25 && down < 0.9);
    nodeTxt.classList.toggle("lit", down > 0.8);
  };
  privRender(0);
  playOnView(privOuter, 4200, privRender);
  window.addEventListener("resize", () => desktopPin() || privRender(1));

  /* ---------- demo diktátu ---------- */
  const VETY = [
    "Ahoj, posílám shrnutí z porady. Spuštění necháváme na pátek a podklady doplním do zítřka.",
    "Na Claude Code jsem připravil zadání pro nový formulář, mrkni prosím na odstavec o platbách.",
    "Díky všem za dnešek. Další schůzka bude ve středu v deset, pozvánku pošlu odpoledne.",
  ];
  const key = $("key");
  const pill = $("pill");
  const pillTxt = $("pill-txt");
  const recBars = $("rec-bars").querySelectorAll("i");
  const mailText = $("mail-text");

  let holding = false;
  let startedAt = 0;
  let veta = 0;
  let barsTimer = null, clockTimer = null, typeTimer = null, hideTimer = null;

  const beginHold = () => {
    if (holding) return;
    holding = true;
    clearInterval(barsTimer); clearInterval(clockTimer);
    clearInterval(typeTimer); clearTimeout(hideTimer);
    startedAt = performance.now();
    key.classList.add("down");
    pill.hidden = false;
    pillTxt.textContent = "0:00";
    if (!reduce.matches) {
      barsTimer = setInterval(() => {
        recBars.forEach((b) => {
          b.style.transform = `scaleY(${(0.25 + Math.random() * 0.75).toFixed(2)})`;
        });
      }, 120);
    }
    clockTimer = setInterval(() => {
      const s = Math.floor((performance.now() - startedAt) / 1000);
      pillTxt.textContent = `0:${String(s).padStart(2, "0")}`;
    }, 250);
  };

  const type = (text) => {
    if (reduce.matches) {
      mailText.textContent = text;
      return;
    }
    const words = text.split(" ");
    let i = 0;
    typeTimer = setInterval(() => {
      mailText.textContent = words.slice(0, ++i).join(" ");
      if (i >= words.length) clearInterval(typeTimer);
    }, 35);
  };

  const endHold = () => {
    if (!holding) return;
    holding = false;
    key.classList.remove("down");
    clearInterval(barsTimer);
    clearInterval(clockTimer);
    recBars.forEach((b) => (b.style.transform = ""));

    if (performance.now() - startedAt < 300) {
      pillTxt.textContent = "Podrž klávesu déle";
      hideTimer = setTimeout(() => (pill.hidden = true), 1400);
      return;
    }
    pillTxt.textContent = "Přepisuji…";
    hideTimer = setTimeout(() => {
      pill.hidden = true;
      if (veta >= VETY.length) {
        veta = 0;
        mailText.textContent = "";
      }
      type(VETY[veta++]);
    }, 600);
  };

  key.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    key.focus();
    beginHold();
  });
  window.addEventListener("pointerup", endHold);
  window.addEventListener("pointercancel", endHold);
  key.addEventListener("keydown", (e) => {
    if ((e.key === " " || e.key === "Enter") && !e.repeat) {
      e.preventDefault();
      beginHold();
    }
  });
  key.addEventListener("keyup", (e) => {
    if (e.key === " " || e.key === "Enter") endHold();
  });
  window.addEventListener("keydown", (e) => {
    if (e.code === "ControlRight" && !e.repeat) beginHold();
  });
  window.addEventListener("keyup", (e) => {
    if (e.code === "ControlRight") endHold();
  });
  window.addEventListener("blur", endHold);

  /* jedno automatické přehrání, až je sekce vidět */
  const demoIo = new IntersectionObserver(
    (entries) => {
      entries.forEach((en) => {
        if (!en.isIntersecting) return;
        demoIo.disconnect();
        beginHold();
        setTimeout(endHold, reduce.matches ? 0 : 1600);
      });
    },
    { threshold: 0.4 }
  );
  demoIo.observe($("mail"));

  /* ---------- videa ---------- */
  const autoplay = (el) => {
    if (!el || reduce.matches) return;
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((en) => {
          if (en.isIntersecting) el.play().catch(() => {});
          else el.pause();
        });
      },
      { threshold: 0.2 }
    );
    io.observe(el);
  };
  autoplay(raceVideo);
  autoplay($("loop"));
  autoplay(document.querySelector(".final-bg"));

  /* ---------- demo učení oprav ---------- */
  const fixWord = $("fix-word");
  const fixToast = $("fix-toast");
  let fixTimers = [];
  function playFix() {
    fixTimers.forEach(clearTimeout);
    fixTimers.forEach(clearInterval);
    fixTimers = [];
    fixWord.textContent = "klódovi";
    fixWord.classList.remove("sel");
    fixToast.hidden = true;
    if (reduce.matches) {
      fixWord.textContent = "Claudovi";
      fixToast.hidden = false;
      return;
    }
    const at = (ms, fn) => fixTimers.push(setTimeout(fn, ms));
    at(500, () => fixWord.classList.add("sel"));
    at(1100, () => {
      const cil = "Claudovi";
      let i = 0;
      const t = setInterval(() => {
        fixWord.textContent = cil.slice(0, ++i);
        if (i >= cil.length) clearInterval(t);
      }, 55);
      fixTimers.push(t);
    });
    at(2100, () => fixWord.classList.remove("sel"));
    at(2300, () => (fixToast.hidden = false));
  }
  $("fix-replay").addEventListener("click", playFix);
  const fixIo = new IntersectionObserver((en) => {
    if (en[0].isIntersecting) { fixIo.disconnect(); playFix(); }
  }, { threshold: 0.6 });
  fixIo.observe($("fix"));

  /* ---------- taby aplikace ---------- */
  const POPISKY = {
    prehled: "Kolik jsi tento týden nadiktoval a kolik času to ušetřilo.",
    historie: "Každý diktát dohledáš, zkopíruješ nebo opravíš.",
    slovnik: "Jména, názvy projektů a opravy, které má Mluvka znát.",
    trenink: "Nahraj pár vět a změř, jak přesně Mluvka rozumí právě tobě.",
    soukromi: "Přehled toho, co zůstává v počítači a co může odejít.",
  };
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  const shotLight = $("shot-light");
  const shotDark = $("shot-dark");
  const caption = $("app-caption");
  const panel = $("panel-app");
  let mobileDark = false;

  const swap = (img, src) => {
    if (img.getAttribute("src") === src) return;
    img.classList.add("fading");
    const next = new Image();
    next.onload = () => {
      img.src = src;
      img.classList.remove("fading");
    };
    next.onerror = () => img.classList.remove("fading");
    next.src = src;
  };

  const currentPage = () =>
    (tabs.find((t) => t.getAttribute("aria-selected") === "true") || tabs[0]).dataset.page;

  const select = (tab) => {
    tabs.forEach((t) => {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
    });
    const page = tab.dataset.page;
    swap(shotLight, `shots/${page}-${mobileDark ? "dark" : "light"}.webp`);
    swap(shotDark, `shots/${page}-dark.webp`);
    caption.textContent = POPISKY[page];
    panel.setAttribute("aria-labelledby", tab.id);
  };

  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (e) => {
      let next = null;
      if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
      if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
      if (e.key === "Home") next = tabs[0];
      if (e.key === "End") next = tabs[tabs.length - 1];
      if (next) {
        e.preventDefault();
        select(next);
        next.focus();
      }
    });
  });

  const modeBtns = Array.from(document.querySelectorAll("#mode-switch button"));
  modeBtns.forEach((b) => {
    b.addEventListener("click", () => {
      modeBtns.forEach((o) => o.setAttribute("aria-pressed", String(o === b)));
      mobileDark = b.dataset.mode === "dark";
      swap(shotLight, `shots/${currentPage()}-${mobileDark ? "dark" : "light"}.webp`);
      shotLight.alt = mobileDark ? "Okno Mluvky v tmavém vzhledu" : "Okno Mluvky ve světlém vzhledu";
    });
  });

  /* ---------- kopírování příkazu ---------- */
  const copyBtn = $("copy");
  const copyLive = $("copy-live");
  copyBtn.addEventListener("click", async () => {
    const txt = $("cmd-text").textContent.trim();
    try {
      await navigator.clipboard.writeText(txt);
      copyBtn.textContent = "Zkopírováno";
      copyLive.textContent = "Příkaz je ve schránce.";
    } catch {
      copyLive.textContent = "Kopírování se nepovedlo, označ příkaz a zkopíruj ho ručně.";
      return;
    }
    setTimeout(() => {
      copyBtn.textContent = "Zkopírovat";
      copyLive.textContent = "";
    }, 2000);
  });

  onScroll();
})();
