"use strict";
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;
    else if (v !== false && v != null) el.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) el.append(c);
  return el;
}

async function api(path, opts = {}) {
  const r = await fetch("/api" + path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : {},
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 401 && path !== "/login") { showLogin(); throw new Error("Sesión expirada"); }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || "Error " + r.status);
  return data;
}

let toastT;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("show"), 2600);
}
const fmtDur = s => s ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "";
const fmtLabel = f => f === "flac" ? "FLAC · Lossless" : "MP3 · 320 kbps";

/* ───── Login ───── */
function showLogin() { $("#app").classList.add("hidden"); $("#login").classList.remove("hidden"); stopPoll(); }
async function boot() {
  try { await api("/me"); } catch { return showLogin(); }
  $("#login").classList.add("hidden"); $("#app").classList.remove("hidden");
  await loadSettings(); route(); pollJobs();
}
$("#login-form").addEventListener("submit", async e => {
  e.preventDefault(); $("#l-err").textContent = "";
  try {
    await api("/login", { method: "POST", body: { username: $("#l-user").value, password: $("#l-pass").value } });
    $("#l-pass").value = ""; boot();
  } catch (err) { $("#l-err").textContent = err.message; }
});
$("#logout").addEventListener("click", async () => { await api("/logout", { method: "POST" }); showLogin(); });

/* ───── Router ───── */
function route() {
  const view = { "#/queue": "queue", "#/settings": "settings" }[location.hash] || "search";
  $$(".view").forEach(v => v.classList.toggle("hidden", v.id !== "v-" + view));
  $$(".sidebar nav a").forEach(a => a.classList.toggle("active", a.dataset.view === view));
  if (view === "settings") renderSettings();
  if (view === "queue") pollJobs();
}
window.addEventListener("hashchange", route);

/* ───── Ajustes ───── */
let settings = { format: "mp3", output_dir: "", root: "/music" };
let browsePath = "";
async function loadSettings() { settings = await api("/settings"); browsePath = settings.output_dir; }
function renderSettings() {
  $$("#fmt-seg button").forEach(b => b.classList.toggle("on", b.dataset.fmt === settings.format));
  $("#root").textContent = settings.root;
  showPath(); loadFolders();
}
const showPath = () => $("#cur-path").textContent = settings.root + (settings.output_dir ? "/" + settings.output_dir : "");
async function loadFolders() {
  const ul = $("#folders");
  try {
    const d = await api("/folders?path=" + encodeURIComponent(browsePath));
    browsePath = d.path; ul.replaceChildren();
    $("#up").style.visibility = browsePath ? "visible" : "hidden";
    const here = h("li", { class: "none" }, h("span", {}, "📍 " + (settings.root + (browsePath ? "/" + browsePath : ""))),
      h("button", { class: "link", style: "margin-left:auto", onclick: () => { settings.output_dir = browsePath; showPath(); toast("Carpeta seleccionada"); } }, "Usar esta carpeta"));
    ul.append(here);
    if (!d.folders.length) ul.append(h("li", { class: "none" }, "Sin subcarpetas"));
    for (const f of d.folders) ul.append(h("li", { onclick: () => { browsePath = (browsePath ? browsePath + "/" : "") + f; loadFolders(); } },
      h("span", {}, "📁 " + f)));
  } catch (e) { toast(e.message); }
}
$("#fmt-seg").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  settings.format = b.dataset.fmt; renderSettings();
});
$("#up").addEventListener("click", () => { browsePath = browsePath.split("/").slice(0, -1).join("/"); loadFolders(); });
$("#mk").addEventListener("click", async () => {
  const name = prompt("Nombre de la nueva carpeta:"); if (!name) return;
  try { await api("/folders", { method: "POST", body: { path: browsePath, name } }); loadFolders(); } catch (e) { toast(e.message); }
});
$("#save").addEventListener("click", async () => {
  try {
    settings = await api("/settings", { method: "PUT", body: { format: settings.format, output_dir: settings.output_dir } });
    showPath(); $("#saved").textContent = "✓ Guardado"; setTimeout(() => $("#saved").textContent = "", 2500);
  } catch (e) { toast(e.message); }
});

/* ───── Buscar / vista previa ───── */
let current = null; // playlist cargada
$("#search-form").addEventListener("submit", async e => {
  e.preventDefault();
  const url = $("#url").value.trim(); if (!url) return;
  const msg = $("#search-msg"); msg.className = "muted center";
  msg.replaceChildren(h("span", { class: "spinner" }), "Leyendo playlist…");
  $("#preview").replaceChildren();
  try {
    current = await api("/playlist", { method: "POST", body: { url } });
    msg.textContent = ""; renderPreview();
  } catch (err) { msg.className = "center err"; msg.id = "search-msg"; msg.textContent = err.message; }
});

function renderPreview() {
  const p = current, box = $("#preview");
  const title = h("input", { class: "a-title", value: p.title, "aria-label": "Álbum" });
  const artist = h("input", { class: "a-artist", value: p.artist, placeholder: "Artista del álbum", "aria-label": "Artista" });
  const meta = h("div", { class: "meta" });
  const btn = h("button", { class: "btn primary", style: "align-self:flex-start" }, "Descargar");
  const all = h("button", { class: "link", style: "align-self:flex-start" }, "Seleccionar todo / nada");
  const checks = [];
  const upd = () => {
    const n = checks.filter(c => c.checked).length;
    meta.textContent = `${n} de ${p.tracks.length} canciones · ${fmtLabel(settings.format)}`;
    btn.disabled = !n;
  };
  const list = h("div", { class: "tracks" }, p.tracks.map((t, i) => {
    const c = h("input", { type: "checkbox", class: "chk", checked: "" }); c.checked = true; checks.push(c);
    const row = h("label", { class: "track" },
      h("span", { class: "num" }, String(i + 1)),
      t.thumb ? h("img", { src: t.thumb, loading: "lazy", alt: "" }) : h("div", { class: "ph" }),
      h("div", { class: "t-main" }, h("div", { class: "t-title" }, t.title), h("div", { class: "t-sub" }, t.artist)),
      h("div", { class: "t-right" }, fmtDur(t.duration), c));
    c.addEventListener("change", () => { row.classList.toggle("off", !c.checked); upd(); });
    return row;
  }));
  all.addEventListener("click", () => { const v = !checks.every(c => c.checked); checks.forEach(c => { c.checked = v; c.dispatchEvent(new Event("change")); }); });
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    try {
      await api("/jobs", { method: "POST", body: {
        title: title.value, artist: artist.value, cover: p.cover,
        tracks: p.tracks.filter((_, i) => checks[i].checked),
      } });
      toast("Añadido a descargas"); location.hash = "#/queue";
    } catch (e) { toast(e.message); }
    upd();
  });
  box.replaceChildren(
    h("div", { class: "album-head" },
      h("img", { class: "cover", src: p.cover, alt: "Carátula" }),
      h("div", { class: "album-info" }, title, artist, meta, h("div", { class: "row" }, btn, all))),
    list);
  upd();
}

/* ───── Descargas ───── */
const cards = new Map();
let pollTimer = null, polling = false;
function stopPoll() { clearTimeout(pollTimer); polling = false; }
async function pollJobs() {
  clearTimeout(pollTimer);
  let list;
  try { list = await api("/jobs"); } catch { return; }
  renderJobs(list);
  const active = list.filter(j => j.status === "queued" || j.status === "running").length;
  const b = $("#badge"); b.textContent = active; b.classList.toggle("hidden", !active);
  const onQueue = location.hash === "#/queue";
  pollTimer = setTimeout(pollJobs, active || onQueue ? 900 : 4000);
}
const STATUS = { queued: "En cola", downloading: "Descargando", done: "Listo", error: "Error", cancelled: "Cancelado" };
const JSTATUS = { queued: "En cola", running: "Descargando", done: "Completada", error: "Con errores", cancelled: "Cancelada" };

function makeCard(j) {
  const c = { open: false };
  c.cover = h("img", { class: "cover", src: j.cover, alt: "" });
  c.title = h("div", { class: "job-title" });
  c.sub = h("div", { class: "job-sub" });
  c.fill = h("i"); c.bar = h("div", { class: "bar" }, c.fill);
  c.pct = h("div", { class: "pct" });
  c.cancel = h("button", { class: "link", onclick: () => api("/jobs/" + j.id, { method: "DELETE" }).then(pollJobs) });
  c.toggle = h("button", { class: "link", onclick: () => { c.open = !c.open; c.list.classList.toggle("hidden", !c.open); c.toggle.textContent = c.open ? "Ocultar" : "Canciones"; } }, "Canciones");
  c.rows = j.tracks.map((t, i) => {
    const r = { n: h("span", { class: "n" }, String(i + 1)), t: h("span", { class: "tt" }, t.title), f: h("i"), st: h("span", { class: "st" }) };
    r.bar = h("div", { class: "bar" }, r.f);
    r.el = h("div", { class: "jt" }, r.n, r.t, r.bar, r.st);
    return r;
  });
  c.list = h("div", { class: "job-tracks hidden" }, c.rows.map(r => r.el));
  c.el = h("div", { class: "job" },
    h("div", { class: "job-top" }, c.cover,
      h("div", { class: "job-main" }, c.title, c.sub, c.bar),
      h("div", { class: "job-actions" }, c.pct, c.toggle, c.cancel)),
    c.list);
  return c;
}
function renderJobs(list) {
  const box = $("#jobs"), ids = new Set(list.map(j => j.id));
  for (const [id, c] of cards) if (!ids.has(id)) { c.el.remove(); cards.delete(id); }
  list.forEach((j, idx) => {
    let c = cards.get(j.id);
    if (!c) { c = makeCard(j); cards.set(j.id, c); }
    if (box.children[idx] !== c.el) box.insertBefore(c.el, box.children[idx] || null);
    c.title.textContent = j.title;
    const running = j.status === "queued" || j.status === "running";
    c.sub.textContent = `${j.artist ? j.artist + " · " : ""}${fmtLabel(j.format)} · ${j.done_count}/${j.tracks.length} · ${JSTATUS[j.status]}${j.error && j.status !== "running" ? " (" + j.error + ")" : ""}`;
    c.fill.style.width = j.progress + "%";
    c.bar.classList.toggle("ok", j.status === "done");
    c.pct.textContent = Math.round(j.progress) + "%";
    c.cancel.textContent = running ? "Cancelar" : "Quitar";
    j.tracks.forEach((t, i) => {
      const r = c.rows[i]; if (!r) return;
      r.f.style.width = t.progress + "%";
      r.bar.classList.toggle("ok", t.status === "done");
      r.st.className = "st " + t.status;
      r.st.textContent = t.status === "downloading" ? Math.round(t.progress) + "%" : STATUS[t.status];
      r.st.title = t.error || "";
    });
  });
  $("#jobs-empty").classList.toggle("hidden", list.length > 0);
}
$("#clear").addEventListener("click", () => api("/jobs/clear", { method: "POST" }).then(pollJobs));

boot();
