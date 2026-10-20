/* Folio web bridge: stands in for the Windows app's pywebview bridge, so ui.html runs unchanged in a
   browser. Calls go to the server; progress events are polled and handed to window.Folio.event. */
(function () {
  window.FOLIO_WEB = true;
  let next = 0, polling = false;
  const post = (name, args) => fetch("/api/call/" + name, {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(args), credentials: "same-origin"
  }).then(r => { if (r.status === 401) { location.href = "/login"; throw new Error("login"); } return r.json(); });

  async function poll() {
    if (polling) return;
    polling = true;
    try {
      for (let idle = 0; idle < 3;) {
        const r = await fetch("/api/events?since=" + next, {credentials: "same-origin"}).then(x => x.json()).catch(() => null);
        if (r) {
          next = r.next;
          for (const e of r.events) { try { window.Folio && window.Folio.event(e); } catch (err) { console.error(err); } }
          idle = r.busy ? 0 : idle + 1;
        }
        await new Promise(res => setTimeout(res, 900));
      }
    } finally { polling = false; }
  }

  function pickFile() {
    return new Promise(done => {
      const inp = document.createElement("input");
      inp.type = "file"; inp.accept = ".txt,.md,.docx,.pdf,.pptx";
      inp.onchange = async () => {
        const f = inp.files[0]; if (!f) return done(null);
        const fd = new FormData(); fd.append("file", f);
        done(await fetch("/api/import", {method: "POST", body: fd, credentials: "same-origin"}).then(r => r.json()));
      };
      inp.click();
    });
  }

  const special = {
    open_path: p => { window.open("/api/file?path=" + encodeURIComponent(p), "_blank"); return Promise.resolve({}); },
    open_url: u => { if (/^https?:\/\//.test(u || "")) window.open(u, "_blank", "noopener"); return Promise.resolve(); },
    import_file: () => pickFile(),
    pick_folder: () => Promise.resolve(null),
    g_status: () => Promise.resolve({has_client: false, connected: false, manual: false}),
  };
  if (!window.FOLIO_CR) special.cr_status = () => Promise.resolve({open: false});
  else {  // the server's own browser stands in for the Classroom window: show it live
    special.cr_open = () => post("cr_open", []).then(r => { LiveCR.open(); return r; });
    special.cr_show = href => post("cr_show", [href]).then(r => { LiveCR.open(); return r; });
  }
  window.pywebview = {api: new Proxy({}, {get: (_, name) => (...args) => {
    if (special[name]) return special[name](...args);
    const p = post(name, args);
    if (name === "generate" || name === "revise") p.then(() => poll());
    return p;
  }})};

  /* ---- Live view of the server's browser: you see it, tap it and type into it; Folio drives it too.
         One factory, two uses: Classroom (base /api/cr) and the optional personal Browser (/api/web). ---- */
  let liveCssDone = false;
  function liveCss() {
    if (liveCssDone) return; liveCssDone = true;
    const css = document.createElement("style");
    css.textContent = `
.flive{position:fixed;inset:0;z-index:9999;display:none;flex-direction:column;background:rgba(6,8,14,.96);
  -webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px);color:#EEF0F8;font:14px -apple-system,system-ui,sans-serif;
  padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}
.flive.on{display:flex}
.flive .bar{display:flex;gap:6px;align-items:center;padding:8px 10px;flex-wrap:wrap}
.flive .bar button,.flive .keys button{all:unset;cursor:pointer;padding:8px 12px;border-radius:12px;background:rgba(255,255,255,.09);
  border:1px solid rgba(255,255,255,.12);font-weight:600;font-size:14px;text-align:center}
.flive .bar .t{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:12px;color:#9AA0B8}
.flive .bar .url{flex:1;min-width:120px;padding:9px 12px;border-radius:12px;border:1px solid rgba(255,255,255,.15);
  background:rgba(10,12,22,.6);color:#EEF0F8;font-size:15px}
.flive .scr{flex:1;overflow:auto;-webkit-overflow-scrolling:touch;display:flex;align-items:flex-start;justify-content:center}
.flive img{width:100%;max-width:min(100%,1100px);height:auto;touch-action:pan-x pinch-zoom;user-select:none;-webkit-user-select:none;display:block}
.flive img.z{width:250%;max-width:none}.flive .scr.z{justify-content:flex-start}
.flive .keys{display:flex;gap:6px;padding:8px 10px;align-items:center}
.flive .keys input{flex:1;min-width:0;padding:10px 12px;border-radius:12px;border:1px solid rgba(255,255,255,.15);
  background:rgba(10,12,22,.6);color:#EEF0F8;font-size:16px}
.flive .keys input.hide{-webkit-text-security:disc}
.flive .hint{padding:0 12px 6px;font-size:12px;color:#9AA0B8}
.flive .hint .kb-only,.flive.kb .hint .touch-only{display:none}.flive.kb .hint .kb-only{display:inline}
.flive .sink{position:fixed;left:-200px;top:0;width:100px;height:40px;opacity:0}
.flive.kb .keys{display:none}.flive.kb .scr{align-items:center}
.flive.kb img{width:auto;max-width:100%;max-height:calc(100vh - 110px);cursor:default}
.flive.kb img.z{max-height:none;width:200%;max-width:none}`;
    document.head.appendChild(css);
  }

  function makeLive(cfg) {  // cfg: {id, base, label, address}
    const L = {el: null, on: false, fast: 0, idle: 0};
    L.build = function () {
      liveCss();
      const d = document.createElement("div");
      d.id = cfg.id; d.className = "flive";
      const barLeft = cfg.address
        ? `<button data-a="back">‹</button><button data-a="forward">›</button><button data-a="reload">⟳</button>
<input class="url" placeholder="Search or type a web address" autocapitalize="off" autocorrect="off" spellcheck="false" enterkeyhint="go">
<button data-a="goto">Go</button><button data-a="close">Done</button><button data-a="zoom">Zoom</button>`
        : `<button data-a="close">Done</button><button data-a="back">‹</button>
<button data-a="reload">⟳</button><button data-a="home">⌂</button><span class="t"></span>
<button data-a="zoom">Zoom</button><button data-a="up">▲</button><button data-a="down">▼</button>`;
      d.innerHTML = `<div class="bar">${barLeft}</div>
<div class="hint">${cfg.label} <span class="touch-only">Tap to click, swipe to scroll, type below.</span><span class="kb-only">Click, scroll and type like a normal page.</span></div>
<div class="scr"><img alt="${cfg.address ? "Browser" : "Classroom"}"></div>
<textarea class="sink" aria-label="Type" autocapitalize="off" autocorrect="off" spellcheck="false"></textarea>
<div class="keys"><input placeholder="Type here, then Send" autocapitalize="off" autocorrect="off" spellcheck="false">
<button data-a="send">Send</button><button data-a="Backspace">⌫</button><button data-a="Enter">⏎</button>
<button data-a="Tab">⇥</button><button data-a="hide">Hide</button></div>`;
      document.body.appendChild(d);
      L.el = d;
      const img = d.querySelector("img"), inp = d.querySelector(".keys input"), title = d.querySelector(".t");
      const urlBar = d.querySelector(".url");
      let chain = Promise.resolve();
      const send = (body) => (chain = chain.then(() => fetch(cfg.base + "/input", {method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})).then(() => L.kick()).catch(() => {}));
      const goTo = () => { const u = (urlBar.value || "").trim(); if (!u) return;
        fetch(cfg.base + "/go", {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({url: u})}).then(() => L.kick()).catch(() => {}); urlBar.blur(); };
      const at = (cx, cy) => { const r = img.getBoundingClientRect(), k = img.naturalWidth / r.width;
        return {x: Math.round((cx - r.left) * k), y: Math.round((cy - r.top) * k)}; };
      let t0 = null, buf = "", timer = null;
      img.addEventListener("touchstart", e => { const p = e.touches[0]; t0 = {x: p.clientX, y: p.clientY, n: e.touches.length}; }, {passive: true});
      img.addEventListener("touchend", e => {
        if (!t0 || t0.n > 1) return; const p = e.changedTouches[0], dy = p.clientY - t0.y, dx = p.clientX - t0.x;
        const k = img.naturalWidth / img.getBoundingClientRect().width;
        if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) send({kind: "scroll", ...at(t0.x, t0.y), dy: Math.round(-dy * k * 1.6)});
        else if (Math.abs(dx) < 10 && Math.abs(dy) < 10) { e.preventDefault(); send({kind: "tap", ...at(p.clientX, p.clientY)}); }
        t0 = null;
      });
      const sink = d.querySelector(".sink");
      L.sink = sink;
      img.addEventListener("pointerup", e => { if (e.pointerType !== "touch" && e.button === 0) { send({kind: "tap", ...at(e.clientX, e.clientY)}); sink.focus(); } });
      sink.addEventListener("input", () => { const v = sink.value; sink.value = ""; if (v) { buf += v; clearTimeout(timer); timer = setTimeout(flush, 60); } });
      img.addEventListener("dragstart", e => e.preventDefault());
      if (matchMedia("(hover: hover) and (pointer: fine)").matches) d.classList.add("kb");
      const flush = () => { clearTimeout(timer); if (buf) { send({kind: "type", text: buf}); buf = ""; } };
      const SPECIAL = ["Enter", "Backspace", "Tab", "Escape", "Delete", "Home", "End", "PageUp", "PageDown",
        "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"];
      const typing = (t) => t === inp || t === urlBar || (t && t.tagName === "INPUT");
      document.addEventListener("keydown", e => {
        if (!L.on || typing(e.target) || e.isComposing) return;
        const mods = (e.ctrlKey || e.metaKey ? "Control+" : "") + (e.altKey ? "Alt+" : "");
        if (mods === "Control+" && /^[vV]$/.test(e.key)) return;
        if (!mods && e.key.length === 1) { if (e.target !== sink) sink.focus(); e.stopPropagation(); return; }
        if (SPECIAL.includes(e.key) || (mods && /^[a-zA-Z0-9]$/.test(e.key))) {
          e.preventDefault(); e.stopPropagation(); flush();
          send({kind: "key", key: mods + (e.shiftKey && SPECIAL.includes(e.key) ? "Shift+" : "") + (e.key.length === 1 ? e.key.toLowerCase() : e.key)});
        }
      }, true);
      document.addEventListener("paste", e => {
        if (!L.on || typing(e.target)) return;
        const t = (e.clipboardData || window.clipboardData).getData("text"); if (t) { e.preventDefault(); flush(); send({kind: "type", text: t}); }
      });
      img.addEventListener("wheel", e => { e.preventDefault(); send({kind: "scroll", ...at(e.clientX, e.clientY), dy: e.deltaY}); }, {passive: false});
      inp.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); typeOut(true); } });
      if (urlBar) urlBar.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); goTo(); } });
      const typeOut = (enter) => { const v = inp.value; inp.value = "";
        (v ? send({kind: "type", text: v}) : Promise.resolve()).then(() => enter && send({kind: "key", key: "Enter"})); };
      d.querySelector(".bar").onclick = d.querySelector(".keys").onclick = e => {
        const a = e.target.dataset && e.target.dataset.a; if (!a) return;
        if (a === "close") return L.close();
        if (a === "goto") return goTo();
        if (a === "send") return typeOut(false);
        if (a === "zoom") { img.classList.toggle("z"); img.parentNode.classList.toggle("z"); return; }
        if (a === "hide") { inp.classList.toggle("hide"); e.target.textContent = inp.classList.contains("hide") ? "Show" : "Hide"; return; }
        if (a === "up" || a === "down") return send({kind: "scroll", x: 550, y: 400, dy: a === "up" ? -500 : 500});
        if (["back", "forward", "reload", "home"].includes(a)) return send({kind: "nav", what: a});
        if (["Backspace", "Enter", "Tab"].includes(a)) { if (inp.value) typeOut(false); return send({kind: "key", key: a}); }
      };
      L.img = img; L.title = title; L.urlBar = urlBar;
    };
    L.beginStream = async function () {
      // wait until Chrome is actually up (it may take a few seconds to launch) before judging the stream
      for (let i = 0; i < 45 && L.on && L.useStream; i++) {
        try { const info = await fetch(cfg.base + "/info", {credentials: "same-origin"}).then(r => r.json()); if (!info.off) break; } catch (e) {}
        await new Promise(r => setTimeout(r, 400));
      }
      if (!L.on || !L.useStream) return;
      L.img.src = cfg.base + "/stream?t=" + Date.now();
      // if no frame renders soon (e.g. a proxy like Cloudflare buffers the stream), fall back to polling
      clearTimeout(L._fb);
      L._fb = setTimeout(() => { if (L.on && L.useStream && !L.img.naturalWidth) L.fallback(); }, 3000);
      L.img.onerror = () => { if (L.useStream) L.fallback(); };
    };
    L.fallback = function () {
      L.useStream = false; clearTimeout(L._fb); L.img.onerror = null;
      if ((L.img.src || "").includes("/stream")) L.img.src = "";  // drop the stream connection; the loop polls now
      L.fast = 8; L.idle = 0;
    };
    L.open = function () {
      if (!L.el) L.build();
      L.el.classList.add("on");
      if (L.el.classList.contains("kb") && !cfg.address) L.sink.focus();
      if (L.useStream === undefined) L.useStream = !!cfg.stream;
      if (L.useStream) L.beginStream();
      if (!L.on) { L.on = true; L.fast = 0; L.idle = 0; L.loop(); }
      if (cfg.address) {  // Browser tab: start Chrome + load a home page if it's not already on one
        fetch(cfg.base + "/info", {credentials: "same-origin"}).then(r => r.json()).then(i => {
          if (i.off || !i.url || i.url.startsWith("about:")) {
            fetch(cfg.base + "/go", {method: "POST", credentials: "same-origin",
              headers: {"Content-Type": "application/json"}, body: JSON.stringify({url: "https://www.google.com"})}).then(() => L.kick());
          }
        }).catch(() => {});
      }
    };
    L.close = function () { L.on = false; L.el && L.el.classList.remove("on"); if (L.img && (L.img.src || "").includes("/stream")) L.img.src = ""; };
    L.kick = function () { L.fast = 8; L.idle = 0; };
    L.loop = async function () {
      let n = 0;
      while (L.on) {
        if (L.useStream) {  // frames arrive via the <img> MJPEG stream; just keep the address bar current
          fetch(cfg.base + "/info", {credentials: "same-origin"}).then(r => r.json()).then(i => {
            if (L.urlBar && document.activeElement !== L.urlBar) L.urlBar.value = i.url || ""; }).catch(() => {});
          await new Promise(r => setTimeout(r, 500));
          continue;
        }
        let changed = false;
        try {
          const first = !L.img.src || !L.img.src.startsWith("blob:");
          const res = await fetch(cfg.base + "/frame?t=" + Date.now() + (first ? "&f=1" : ""), {credentials: "same-origin"});
          if (res.status === 200) {
            const b = await res.blob();
            if (b && b.size) { changed = true; const u = URL.createObjectURL(b), old = L.img.src; L.img.src = u; if (old.startsWith("blob:")) URL.revokeObjectURL(old); }
          }
          if (n++ % 8 === 0) fetch(cfg.base + "/info", {credentials: "same-origin"}).then(r => r.json()).then(i => {
            if (L.title) L.title.textContent = i.title || i.url || "";
            if (L.urlBar && document.activeElement !== L.urlBar) L.urlBar.value = i.url || ""; }).catch(() => {});
        } catch (e) {}
        L.idle = changed ? 0 : Math.min(L.idle + 1, 12);
        let delay = changed ? 45 : Math.min(500, 90 + L.idle * 40);
        if (L.fast > 0) { L.fast--; delay = 35; }
        await new Promise(r => setTimeout(r, delay));
      }
    };
    return L;
  }

  const LiveCR = makeLive({id: "live", base: "/api/cr", address: false,
    label: "Classroom on Folio's server. Sign in yourself if Google asks."});
  const LiveWeb = makeLive({id: "liveweb", base: "/api/web", address: true, stream: true,
    label: "A browser running on your PC."});
  window.FolioLive = LiveCR;
  window.FolioWeb = LiveWeb;
  if (window.FOLIO_WEBTAB) {
    const openWeb = () => LiveWeb.open();
    const nb = document.querySelector('.nav button[data-v="browser"]'); if (nb) nb.addEventListener("click", openWeb);
    const wo = document.getElementById("wb-open"); if (wo) wo.addEventListener("click", openWeb);
  }

  document.addEventListener("DOMContentLoaded", async () => {
    // skip events from before this page was opened, but pick up a project that's still being made
    const r = await fetch("/api/events?since=999999999", {credentials: "same-origin"}).then(x => x.json()).catch(() => null);
    if (r) { next = r.next; if (r.busy) { poll(); } }
    window.dispatchEvent(new Event("pywebviewready"));
  });
})();
