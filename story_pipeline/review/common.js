// Shared helpers for the review UI's newer pages (ambience, novellas).
// Lifted from casting.html: api() with readable errors, toast(), the two-press
// "armed" button for anything that spends money, page tabs, and a job panel
// that polls /job and tails /log the way the story and casting pages do.
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

  // A paid button arms on the first press and runs on the second.
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

  // A job panel inside `el`, for one job key.
  //   urls(): {job, log, cancel}   — read each time, so the key can change
  //   describe(j): the one-line status for a job view
  //   onDone(j): called once when a running job stops
  function jobPanel(el, { urls, describe, onDone, interval = 1500 }) {
    el.classList.add("job");
    el.innerHTML = `<div class="row"><span class="what"></span>
        <button type="button" class="quiet" data-log>Log</button>
        <button type="button" class="stop" data-stop hidden>Stop</button></div>
      <div class="bar"><i></i></div><pre class="log" hidden></pre>`;
    const what = el.querySelector(".what"), bar = el.querySelector(".bar i"),
          log = el.querySelector(".log"), stop = el.querySelector("[data-stop]");
    let timer = null, from = 0, wasRunning = false;

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
    function started(j) { from = 0; log.textContent = ""; show(j); }

    el.querySelector("[data-log]").onclick = () => { log.hidden = !log.hidden; if (!log.hidden) tail(); };
    stop.onclick = async () => {
      try { show((await api(urls().cancel, {})).job); toast("Stopped. Finished work is kept."); }
      catch (e) { toast(e.message, true); }
    };
    return { show, refresh, started };
  }

  window.MW = { $, esc, money, toast, explain, api, armed, nav, jobPanel, PAGES };
})();
