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

  /* ---------- přilepené scény ---------- */
  const progress = (outer) => {
    const r = outer.getBoundingClientRect();
    const total = r.height - window.innerHeight;
    if (total <= 0) return 0;
    return clamp01(-r.top / total);
  };
  const desktopPin = () => window.matchMedia("(min-width: 861px)").matches && !reduce.matches;

  /* scéna 1: rychlost */
  const speedOuter = $("speed-outer");
  const cardSlow = $("card-slow");
  const cardFast = $("card-fast");
  const raceText = $("race-text");
  const raceVideo = $("race-video");
  const VETA_RACE = "Ahoj, posílám shrnutí z porady. Spuštění necháváme na pátek a podklady doplním do zítřka.";
  const RACE_WORDS = VETA_RACE.split(" ");

  scrollJobs.push(() => {
    if (!desktopPin()) {
      cardSlow.style.cssText = "";
      cardFast.style.cssText = "";
      raceText.textContent = VETA_RACE;
      return;
    }
    const p = progress(speedOuter);
    // 0 až 0,35: klid. 0,35 až 0,75: pomalá karta mizí, rychlá se zmenšuje. 0,75 až 1: text.
    const a = clamp01((p - 0.35) / 0.4);
    cardSlow.style.opacity = String(1 - a);
    cardSlow.style.transform = `translateX(${-a * 90}px) scale(${lerp(1, 0.82, a)})`;
    cardFast.style.flexBasis = `${lerp(100, 46, a)}%`;
    // dorovnat misto po zmizele pomale karte (jeji sirka + mezera), at karta skonci na stredu
    cardFast.style.transform = `translateX(${(a * -(cardSlow.offsetWidth + 28)) / 2}px)`;
    const t = clamp01((p - 0.62) / 0.33);
    const n = Math.round(t * RACE_WORDS.length);
    raceText.textContent = RACE_WORDS.slice(0, n).join(" ");
  });

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

  scrollJobs.push(() => {
    if (!desktopPin()) {
      privReset();
      nodeTxt.classList.add("lit");
      flyText.style.opacity = "1";
      flyAudio.style.opacity = "0";
      flyer.style.transform = "translateX(-50%)";
      return;
    }
    const p = progress(privOuter);
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
  });

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

  /* ---------- lepivý seznam vlastností ---------- */
  const featBtns = Array.from(document.querySelectorAll("#feat-list button"));
  const featCards = Array.from(document.querySelectorAll(".feat-card"));
  const showFeat = (idx) => {
    featBtns.forEach((b) => b.setAttribute("aria-current", String(b.dataset.f === String(idx))));
    featCards.forEach((c) => (c.hidden = c.dataset.f !== String(idx)));
    if (idx === 2) playFix();
  };
  featBtns.forEach((b) => b.addEventListener("click", () => showFeat(Number(b.dataset.f))));

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
