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
    cr_status: () => Promise.resolve({open: false}),
  };
  window.pywebview = {api: new Proxy({}, {get: (_, name) => (...args) => {
    if (special[name]) return special[name](...args);
    const p = post(name, args);
    if (name === "generate" || name === "revise") p.then(() => poll());
    return p;
  }})};

  document.addEventListener("DOMContentLoaded", async () => {
    // skip events from before this page was opened, but pick up a project that's still being made
    const r = await fetch("/api/events?since=999999999", {credentials: "same-origin"}).then(x => x.json()).catch(() => null);
    if (r) { next = r.next; if (r.busy) { poll(); } }
    window.dispatchEvent(new Event("pywebviewready"));
  });
})();
