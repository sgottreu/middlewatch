// Shared helpers for every page of the review UI (stories, casting, ambience,
// novellas): api() with readable errors, toast(), the two-press "armed" button
// for anything that spends money, page tabs, a job panel that polls /job and
// tails /log, and the artifact list a job panel shows under its log.
// Served at /common.js. Everything hangs off one global, MW.
"use strict";
(function () {
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const money = n => "$" + (Number(n) || 0).toFixed(2);

  function toast(msg, bad) {
    let t = $("toast");
    if (!t) { t = document.createElement("div"); t.id = "toast"; document.body.append(t); }
    t.textContent = msg; t.classList.toggle("bad", !!bad);
    t.classList.add("show"); clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove("show"), bad ? 6000 : 2600);
  }

  // Strip "ValueError: " and say what a bare "not found" from the server means.
  function explain(err) {
    const msg = String(err || "").trim().replace(/^[A-Za-z_][\w.]*(Error|Exception):\s*/, "");
    return /^not found$/i.test(msg)
      ? "The review server is running older code than this page. Restart it (mw-deploy, or sudo systemctl restart mw-review) and reload."
      : msg;
  }

  async function api(path, body) {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify(body) } : undefined);
    const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
    if (!r.ok || j.error) throw new Error(explain(j.error || `HTTP ${r.status}`));
    return j;
  }

  // A paid button arms on the first press and runs on the second, so a stray tap
  // never spends money and nobody has to dismiss a dialog to get on with it.
  // The armed label says "Confirm — spend $X", never "… again": "again" reads as
  // "you already ran this", and the price is what you're deciding on. Clicking
  // anywhere else, or Escape, stands it down.
  const disarm = b => { b.classList.remove("armed"); clearTimeout(b._t); b.textContent = b._label; };
  function armed(btn, label, fn) {
    if (btn.classList.contains("armed")) { disarm(btn); fn(); return; }
    document.querySelectorAll("button.armed").forEach(disarm);
    btn._label = btn.textContent; btn.textContent = label; btn.classList.add("armed");
    btn._t = setTimeout(() => disarm(btn), 5000);
  }
  document.addEventListener("click", ev => document.querySelectorAll("button.armed")
    .forEach(b => { if (!b.contains(ev.target)) disarm(b); }), true);
  document.addEventListener("keydown", ev => {
    if (ev.key === "Escape") document.querySelectorAll("button.armed").forEach(disarm);
  });

  // Page tabs. Add a page here when its route exists on the server.
  const PAGES = [["Stories", "/"], ["Casting", "/casting"],
                 ["Ambience", "/ambience"], ["Novellas", "/novellas"]];
  function nav(el, current) {
    el.classList.add("pages");
    el.innerHTML = PAGES.map(([name, href]) =>
      `<a href="${href}"${name === current ? ' class="on" aria-current="page"' : ""}>${esc(name)}</a>`).join("");
  }

  // Artifacts: files a job wrote, announced by a progress marker line
  //   ::progress {"event": "artifact", "kind": "audio", "path": "...", "label"?: "..."}
  // (emit_artifact() in story_pipeline/cli.py, _progress() in ambience_pipeline/cli.py).
  // The server's log endpoint strips markers, so a page normally passes the job
  // view's `artifacts` (each with a `url` the server serves); raw log text and
  // raw marker lists are accepted too. Newest first, one entry per file.
  const MARKER = "::progress ";
  const AUDIO = /\.(wav|mp3|m4a|ogg|flac)$/i, IMAGE = /\.(png|jpe?g|webp|gif|svg)$/i;
  function artifacts(src) {
    let marks = [];
    if (typeof src === "string") {
      for (const line of src.split("\n")) {
        if (!line.startsWith(MARKER)) continue;
        try { marks.push(JSON.parse(line.slice(MARKER.length))); } catch (e) { /* half a line */ }
      }
    } else if (Array.isArray(src)) marks = src;
    const seen = new Set(), out = [];
    for (const m of marks.slice().reverse()) {
      if (!m || typeof m !== "object" || (m.event && m.event !== "artifact")) continue;
      const key = m.path || m.url;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      const name = String(m.path || m.url).split("?")[0].split("/").pop();
      const kind = ["audio", "image"].includes(m.kind) ? m.kind
                 : AUDIO.test(name) ? "audio" : IMAGE.test(name) ? "image" : "file";
      // Only a URL on this server (or plain http/https) becomes a link.
      const url = typeof m.url === "string" && /^(\/(?!\/)|https?:\/\/)/.test(m.url) ? m.url : null;
      out.push({ kind, url, path: m.path || null, label: m.label || name });
    }
    return out;
  }
  function renderArtifacts(list) {
    return (list || []).map(a => {
      const label = esc(a.label), url = a.url ? esc(a.url) : "";
      if (!url) return `<div class="art"><span title="${esc(a.path || "")}">${label} · not served</span></div>`;
      if (a.kind === "audio")
        return `<div class="art"><span>${label}</span><audio controls preload="none" src="${url}"></audio></div>`;
      if (a.kind === "image")
        return `<div class="art"><a href="${url}" target="_blank" rel="noopener" title="${label}">` +
               `<img src="${url}" alt="${label}" loading="lazy"></a></div>`;
      return `<div class="art"><a href="${url}" target="_blank" rel="noopener">${label}</a></div>`;
    }).join("");
  }

  // A job panel inside `el`, for one job key.
  //   urls(): {job, log, cancel}   — read each time, so the key can change
  //   describe(j): the one-line status for a job view
  //   onDone(j): called once when a running job stops
  function jobPanel(el, { urls, describe, onDone, interval = 1500 }) {
    el.classList.add("job");
    el.innerHTML = `<div class="row"><span class="what"></span>
        <button type="button" class="quiet" data-log>Log</button>
        <button type="button" class="stop" data-stop hidden>Stop</button></div>
      <div class="bar"><i></i></div><pre class="log" hidden></pre>
      <div class="artifacts" hidden></div>`;
    const what = el.querySelector(".what"), bar = el.querySelector(".bar i"),
          log = el.querySelector(".log"), stop = el.querySelector("[data-stop]"),
          arts = el.querySelector(".artifacts");
    let timer = null, from = 0, wasRunning = false;

    // Rebuilt only when the list changes, so a player mid-play is left alone.
    function showArtifacts(j) {
      const html = renderArtifacts(artifacts((j && (j.artifacts || j.progress)) || []));
      if (html !== arts._html) { arts.innerHTML = html; arts._html = html; }
      arts.hidden = log.hidden || !html;
    }

    async function tail() {
      try {
        const c = await api(`${urls().log}?from=${from}`);
        if (c.text) { log.textContent += c.text; log.scrollTop = log.scrollHeight; }
        from = c.offset;
      } catch (e) { /* the next poll tries again */ }
    }

    function show(j) {
      clearTimeout(timer);
      if (!j || j.state === "none") { el.classList.remove("show", "bad", "done"); wasRunning = false; return; }
      const running = j.state === "running";
      el.classList.add("show");
      el.classList.toggle("done", j.state === "done" && !j.failed);
      el.classList.toggle("bad", !running && (j.state !== "done" || !!j.failed));
      const pct = j.total ? Math.round(100 * j.done / j.total) : (running ? 4 : 100);
      bar.style.width = pct + "%";
      stop.hidden = !running;
      what.textContent = describe ? describe(j) : `${j.stage || "Job"}: ${j.state}`;
      showArtifacts(j);
      if (j.state === "failed" && j.error) { log.hidden = false; log.textContent = j.error; }
      if (running) {
        wasRunning = true;
        if (!log.hidden) tail();
        timer = setTimeout(refresh, interval);
      } else if (wasRunning) {
        wasRunning = false;
        if (!log.hidden) tail();
        if (onDone) onDone(j);
      }
    }

    async function refresh() {
      try { show(await api(urls().job)); } catch (e) { timer = setTimeout(refresh, interval * 2); }
    }

    // A new job: clear the old log and start polling.
    function started(j) { from = 0; log.textContent = ""; showArtifacts(null); show(j); }

    el.querySelector("[data-log]").onclick = () => {
      log.hidden = !log.hidden;
      arts.hidden = log.hidden || !arts._html;
      if (!log.hidden) tail();
    };
    stop.onclick = async () => {
      try { show((await api(urls().cancel, {})).job); toast("Stopped. Finished work is kept."); }
      catch (e) { toast(e.message, true); }
    };
    return { show, refresh, started };
  }

  window.MW = { $, esc, money, toast, explain, api, armed, nav, jobPanel, PAGES,
                artifacts, renderArtifacts };
})();
