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
    special.cr_open = () => post("cr_open", []).then(r => { Live.open(); return r; });
    special.cr_show = href => post("cr_show", [href]).then(r => { Live.open(); return r; });
  }
  window.pywebview = {api: new Proxy({}, {get: (_, name) => (...args) => {
    if (special[name]) return special[name](...args);
    const p = post(name, args);
    if (name === "generate" || name === "revise") p.then(() => poll());
    return p;
  }})};

  /* ---- Live view of the server's browser: you see it, tap it and type into it; Folio drives it too ---- */
  const Live = {
    el: null, on: false,
    build() {
      const css = document.createElement("style");
      css.textContent = `
#live{position:fixed;inset:0;z-index:9999;display:none;flex-direction:column;background:rgba(6,8,14,.96);
  -webkit-backdrop-filter:blur(20px);backdrop-filter:blur(20px);color:#EEF0F8;font:14px -apple-system,system-ui,sans-serif;
  padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom)}
#live.on{display:flex}
#live .bar{display:flex;gap:6px;align-items:center;padding:8px 10px}
#live .bar button,#live .keys button{all:unset;cursor:pointer;padding:8px 12px;border-radius:12px;background:rgba(255,255,255,.09);
  border:1px solid rgba(255,255,255,.12);font-weight:600;font-size:14px;text-align:center}
#live .bar .t{flex:1;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-size:12px;color:#9AA0B8}
#live .scr{flex:1;overflow:auto;-webkit-overflow-scrolling:touch;display:flex;align-items:flex-start;justify-content:center}
#live img{width:100%;max-width:min(100%,1100px);height:auto;touch-action:pan-x pinch-zoom;user-select:none;-webkit-user-select:none;display:block}
#live img.z{width:250%;max-width:none}#live .scr.z{justify-content:flex-start}
#live .keys{display:flex;gap:6px;padding:8px 10px;align-items:center}
#live .keys input{flex:1;min-width:0;padding:10px 12px;border-radius:12px;border:1px solid rgba(255,255,255,.15);
  background:rgba(10,12,22,.6);color:#EEF0F8;font-size:16px}
#live .keys input.hide{-webkit-text-security:disc}
#live .hint{padding:0 12px 6px;font-size:12px;color:#9AA0B8}
#live .hint .kb-only,#live.kb .hint .touch-only{display:none}#live.kb .hint .kb-only{display:inline}
/* laptops and Chromebooks: type straight into the page, picture sized to the screen */
#live .sink{position:fixed;left:-200px;top:0;width:100px;height:40px;opacity:0}
#live.kb .keys{display:none}#live.kb .scr{align-items:center}
#live.kb img{width:auto;max-width:100%;max-height:calc(100vh - 90px);cursor:default}
#live.kb img.z{max-height:none;width:200%;max-width:none}`;
      document.head.appendChild(css);
      const d = document.createElement("div");
      d.id = "live";
      d.innerHTML = `<div class="bar"><button data-a="close">Done</button><button data-a="back">‹</button>
<button data-a="reload">⟳</button><button data-a="home">⌂</button><span class="t"></span>
<button data-a="zoom">Zoom</button><button data-a="up">▲</button><button data-a="down">▼</button></div>
<div class="hint">Classroom on Folio's server. <span class="touch-only">Tap to click, swipe to scroll, type below.</span><span class="kb-only">Click, scroll and type like a normal page.</span> Sign in yourself if Google asks.</div>
<div class="scr"><img alt="Classroom"></div>
<textarea class="sink" aria-label="Type into Classroom" autocapitalize="off" autocorrect="off" spellcheck="false"></textarea>
<div class="keys"><input placeholder="Type here, then Send" autocapitalize="off" autocorrect="off" spellcheck="false">
<button data-a="send">Send</button><button data-a="Backspace">⌫</button><button data-a="Enter">⏎</button>
<button data-a="Tab">⇥</button><button data-a="hide">Hide</button></div>`;
      document.body.appendChild(d);
      this.el = d;
      const img = d.querySelector("img"), inp = d.querySelector("input"), title = d.querySelector(".t");
      let chain = Promise.resolve();  // one at a time, so keys arrive in the order they were typed
      const send = (body) => (chain = chain.then(() => fetch("/api/cr/input", {method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)})).then(() => this.kick()).catch(() => {}));
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
      this.sink = sink;
      img.addEventListener("pointerup", e => { if (e.pointerType !== "touch" && e.button === 0) { send({kind: "tap", ...at(e.clientX, e.clientY)}); sink.focus(); } });
      // typed text lands in a hidden box first, so keyboards, autocorrect and paste all come through
      sink.addEventListener("input", () => { const v = sink.value; sink.value = ""; if (v) { buf += v; clearTimeout(timer); timer = setTimeout(flush, 60); } });
      img.addEventListener("dragstart", e => e.preventDefault());
      // keyboard: typing goes straight to the page (text is batched; Ctrl/Alt shortcuts and special keys go as keys)
      if (matchMedia("(hover: hover) and (pointer: fine)").matches) d.classList.add("kb");
      const flush = () => { clearTimeout(timer); if (buf) { send({kind: "type", text: buf}); buf = ""; } };
      const SPECIAL = ["Enter", "Backspace", "Tab", "Escape", "Delete", "Home", "End", "PageUp", "PageDown",
        "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"];
      document.addEventListener("keydown", e => {
        if (!this.on || e.target === inp || e.isComposing) return;
        const mods = (e.ctrlKey || e.metaKey ? "Control+" : "") + (e.altKey ? "Alt+" : "");
        if (mods === "Control+" && /^[vV]$/.test(e.key)) return;              // paste: handled below
        if (!mods && e.key.length === 1) { if (e.target !== sink) sink.focus(); e.stopPropagation(); return; }  // text: see sink
        if (SPECIAL.includes(e.key) || (mods && /^[a-zA-Z0-9]$/.test(e.key))) {
          e.preventDefault(); e.stopPropagation(); flush();
          send({kind: "key", key: mods + (e.shiftKey && SPECIAL.includes(e.key) ? "Shift+" : "") + (e.key.length === 1 ? e.key.toLowerCase() : e.key)});
        }
      }, true);  // before Folio's own shortcuts, which would otherwise fire under the live view
      document.addEventListener("paste", e => {
        if (!this.on || e.target === inp) return;
        const t = (e.clipboardData || window.clipboardData).getData("text"); if (t) { e.preventDefault(); flush(); send({kind: "type", text: t}); }
      });
      img.addEventListener("wheel", e => { e.preventDefault(); send({kind: "scroll", ...at(e.clientX, e.clientY), dy: e.deltaY}); }, {passive: false});
      inp.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); typeOut(true); } });
      const typeOut = (enter) => { const v = inp.value; inp.value = "";
        (v ? send({kind: "type", text: v}) : Promise.resolve()).then(() => enter && send({kind: "key", key: "Enter"})); };
      d.querySelector(".bar").onclick = d.querySelector(".keys").onclick = e => {
        const a = e.target.dataset && e.target.dataset.a; if (!a) return;
        if (a === "close") return this.close();
        if (a === "send") return typeOut(false);
        if (a === "zoom") { img.classList.toggle("z"); img.parentNode.classList.toggle("z"); return; }
        if (a === "hide") { inp.classList.toggle("hide"); e.target.textContent = inp.classList.contains("hide") ? "Show" : "Hide"; return; }
        if (a === "up" || a === "down") return send({kind: "scroll", x: 550, y: 400, dy: a === "up" ? -500 : 500});
        if (["back", "reload", "home"].includes(a)) return send({kind: "nav", what: a});
        if (["Backspace", "Enter", "Tab"].includes(a)) { if (inp.value) typeOut(false); return send({kind: "key", key: a}); }
      };
      this.img = img; this.title = title;
    },
    open() { if (!this.el) this.build(); this.el.classList.add("on"); if (this.el.classList.contains("kb")) this.sink.focus(); if (!this.on) { this.on = true; this.fast = 0; this.idle = 0; this.loop(); } },
    close() { this.on = false; this.el && this.el.classList.remove("on"); },
    kick() { this.fast = 8; this.idle = 0; },  // after you click/type, run smooth for a moment
    async loop() {
      let n = 0;
      while (this.on) {
        let changed = false;
        try {
          const first = !this.img.src || !this.img.src.startsWith("blob:");
          const res = await fetch("/api/cr/frame?t=" + Date.now() + (first ? "&f=1" : ""), {credentials: "same-origin"});
          if (res.status === 200) {  // 204 = unchanged, keep the current frame
            const b = await res.blob();
            if (b && b.size) { changed = true; const u = URL.createObjectURL(b), old = this.img.src; this.img.src = u; if (old.startsWith("blob:")) URL.revokeObjectURL(old); }
          }
          if (n++ % 8 === 0) fetch("/api/cr/info", {credentials: "same-origin"}).then(r => r.json()).then(i => {
            this.title.textContent = i.title || i.url || ""; }).catch(() => {});
        } catch (e) {}
        // smooth while the page moves; ease off when it's still, so a static page costs almost nothing
        this.idle = changed ? 0 : Math.min(this.idle + 1, 12);
        let delay = changed ? 70 : Math.min(500, 110 + this.idle * 40);
        if (this.fast > 0) { this.fast--; delay = 55; }
        await new Promise(r => setTimeout(r, delay));
      }
    },
  };
  window.FolioLive = Live;

  document.addEventListener("DOMContentLoaded", async () => {
    // skip events from before this page was opened, but pick up a project that's still being made
    const r = await fetch("/api/events?since=999999999", {credentials: "same-origin"}).then(x => x.json()).catch(() => null);
    if (r) { next = r.next; if (r.busy) { poll(); } }
    window.dispatchEvent(new Event("pywebviewready"));
  });
})();
