"use strict";

const state = {
  meta: null,
  songs: [],
  byId: new Map(),
  setlists: [],
  current: null,   // setlist being edited: {id, name, target_minutes, mood, notes, song_ids, rpl_file}
  saved: null,     // JSON snapshot of current as last saved, for dirty tracking
  libSort: { key: "title", desc: false },
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// ---------- helpers ----------

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function fmt(sec) {
  if (sec == null || isNaN(sec)) return "–";
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

function parseTime(text) {
  text = text.trim();
  if (!text) return null;
  const parts = text.split(":").map(Number);
  if (parts.some(n => isNaN(n) || n < 0)) return NaN;
  return parts.reduce((total, n) => total * 60 + n, 0);
}

function moodClass(mood) {
  return "mood-" + mood.replace(/[^A-Za-z0-9 ]/g, "").replace(/ /g, "-");
}

function moodChip(mood) {
  return mood ? `<span class="chip ${moodClass(mood)}">${esc(mood)}</span>` : `<span class="chip none">no mood</span>`;
}

function effortTag(effort) {
  if (!effort) return `<span class="chip none">no effort</span>`;
  const n = state.meta.efforts.indexOf(effort) + 1;
  return `<span class="effort effort-${effort}" title="Voice effort: ${effort}"><span class="dots">${"●".repeat(n)}${"○".repeat(3 - n)}</span> ${effort}</span>`;
}

function shuffle(list) {
  for (let i = list.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [list[i], list[j]] = [list[j], list[i]];
  }
  return list;
}

async function api(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("error", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), isError ? 6000 : 2800);
}

function songLength(id) {
  return state.byId.get(id)?.length || 0;
}

function setTotal(set) {
  return set.song_ids.reduce((total, id) => total + songLength(id), 0);
}

function isDirty() {
  return state.current && JSON.stringify(snapshot(state.current)) !== state.saved;
}

function snapshot(set) {
  return { name: set.name, target_minutes: Number(set.target_minutes), mood: set.mood || null, notes: set.notes, song_ids: set.song_ids };
}

function confirmDiscard() {
  return !isDirty() || confirm(`Discard unsaved changes to "${state.current.name}"?`);
}

// ---------- loading ----------

async function loadSongs() {
  state.songs = await api("GET", "/api/songs");
  state.byId = new Map(state.songs.map(s => [s.id, s]));
}

async function loadSetlists() {
  state.setlists = await api("GET", "/api/setlists");
}

async function openSetlist(id) {
  const s = await api("GET", `/api/setlists/${id}`);
  state.current = { id: s.id, name: s.name, target_minutes: s.target_minutes, mood: s.mood || "", notes: s.notes, song_ids: s.song_ids, rpl_file: s.rpl_file };
  state.saved = JSON.stringify(snapshot(state.current));
  history.replaceState(null, "", `#set=${s.id}`);
  renderSetlistsView();
}

// ---------- setlist sidebar ----------

function renderSetList() {
  const ul = $("#set-list");
  const items = [...state.setlists];
  const cur = state.current;
  if (cur && cur.id == null) items.unshift({ id: null, name: cur.name, target_minutes: cur.target_minutes, mood: cur.mood, song_count: cur.song_ids.length, total_seconds: setTotal(cur) });
  if (!items.length) {
    ul.innerHTML = `<li class="muted" style="cursor:default">No setlists yet.</li>`;
    return;
  }
  ul.innerHTML = items.map(s => {
    const active = cur && cur.id === s.id;
    const live = active ? { ...s, name: cur.name, song_count: cur.song_ids.length, total_seconds: setTotal(cur), target_minutes: cur.target_minutes, mood: cur.mood } : s;
    return `<li data-id="${s.id ?? ""}" class="${active ? "active" : ""}">
      <div class="set-name">${esc(live.name)}${active && isDirty() ? `<span class="dirty-dot" title="Unsaved changes"></span>` : ""}</div>
      <div class="set-meta">${fmt(live.total_seconds)} / ${fmt(live.target_minutes * 60)} · ${live.song_count} songs · ${esc(live.mood || "Mixed")}</div>
    </li>`;
  }).join("");
}

$("#set-list").addEventListener("click", async e => {
  const li = e.target.closest("li[data-id]");
  if (!li) return;
  const id = li.dataset.id ? Number(li.dataset.id) : null;
  if (state.current && state.current.id === id) return;
  if (!confirmDiscard()) return;
  if (id == null) return;
  try { await openSetlist(id); } catch (err) { toast(err.message, true); }
});

function uniqueName(base) {
  const names = new Set(state.setlists.map(s => s.name.toLowerCase()));
  let name = base, n = 2;
  while (names.has(name.toLowerCase())) name = `${base} ${n++}`;
  return name;
}

$("#btn-new-set").addEventListener("click", () => {
  if (!confirmDiscard()) return;
  state.current = { id: null, name: uniqueName("New Set"), target_minutes: 45, mood: "", notes: "", song_ids: [], rpl_file: null };
  state.saved = null;
  renderSetlistsView();
  $("#set-name")?.select();
});

// ---------- editor ----------

function renderSetlistsView() {
  renderSetList();
  renderEditor();
  renderPicker();
}

function renderEditor() {
  const el = $("#editor");
  const cur = state.current;
  if (!cur) {
    el.innerHTML = `<div class="empty">
      <p>Pick a setlist on the left, create a new one, or import your existing <code>.rpl</code> files.</p>
    </div>`;
    return;
  }
  const moods = state.meta.moods.map(m => `<option ${cur.mood === m ? "selected" : ""}>${esc(m)}</option>`).join("");
  el.innerHTML = `
    <div class="editor-head">
      <input id="set-name" class="name-input" value="${esc(cur.name)}" aria-label="Setlist name" spellcheck="false">
      <label class="field">Target (min)
        <input id="set-target" type="number" min="0" step="1" value="${esc(cur.target_minutes)}">
      </label>
      <label class="field">Mood
        <select id="set-mood"><option value="">Mixed</option>${moods}</select>
      </label>
      <div class="editor-actions">
        <button id="btn-save" class="primary" title="Save and write the .rpl file (Ctrl+S)">Save &amp; write .rpl</button>
        <button id="btn-revert">Revert</button>
        <span class="spacer"></span>
        <button id="btn-duplicate">Duplicate</button>
        <button id="btn-download" title="Download a copy of the .rpl">Download .rpl</button>
        <button id="btn-delete" class="danger">Delete</button>
      </div>
    </div>
    <div class="stats" id="stats"></div>
    <ul class="warnings" id="warnings"></ul>
    <table class="set-table">
      <thead><tr><th></th><th>#</th><th>Song</th><th>Mood</th><th>Voice</th><th class="num">Length</th><th class="num">Ends at</th><th></th></tr></thead>
      <tbody id="set-rows"></tbody>
    </table>
    <label class="notes-field">Notes
      <textarea id="set-notes" placeholder="Venue, date, reminders…">${esc(cur.notes)}</textarea>
    </label>
    <div class="file-path" id="file-path"></div>`;

  $("#set-name").addEventListener("input", e => { cur.name = e.target.value; refreshEditor(); });
  $("#set-target").addEventListener("input", e => { cur.target_minutes = e.target.value === "" ? 0 : Number(e.target.value); refreshEditor(); });
  $("#set-mood").addEventListener("change", e => { cur.mood = e.target.value; refreshEditor(); renderPicker(); });
  $("#set-notes").addEventListener("input", e => { cur.notes = e.target.value; renderSetList(); });
  $("#btn-save").addEventListener("click", saveCurrent);
  $("#btn-revert").addEventListener("click", revertCurrent);
  $("#btn-duplicate").addEventListener("click", duplicateCurrent);
  $("#btn-download").addEventListener("click", () => {
    if (cur.id == null || isDirty()) return toast("Save the setlist first, then download it.", true);
    location.href = `/api/setlists/${cur.id}/rpl`;
  });
  $("#btn-delete").addEventListener("click", deleteCurrent);
  bindRowEvents();
  refreshEditor();
}

/** Re-render the parts of the editor that depend on the song order, without touching the inputs. */
function refreshEditor() {
  const cur = state.current;
  if (!cur) return;
  renderStats();
  renderWarnings();
  renderRows();
  renderSetList();
  const cfg = state.meta.config;
  const fileName = cur.rpl_file && cur.id != null && state.setlists.find(s => s.id === cur.id)?.name === cur.name
    ? cur.rpl_file
    : `${cfg.setlist_dir}\\${cur.name.replace(/[<>:"/\\|?*]/g, "_").trim() || "setlist"}.RPL`;
  $("#file-path").innerHTML = `Writes to <code>${esc(fileName)}</code> with song paths under <code>${esc(cfg.rpl_path_root || cfg.project_root)}</code>`;
  $("#btn-revert").disabled = !isDirty() || cur.id == null;
  $("#btn-save").disabled = !cur.name.trim();
}

function renderStats() {
  const cur = state.current;
  const total = setTotal(cur);
  const target = Number(cur.target_minutes) * 60;
  const diff = total - target;
  let cls = "good", label = "On target";
  if (target > 0 && diff < -60) { cls = "warn"; label = `${fmt(-diff)} short`; }
  else if (target > 0 && diff > 60) { cls = "bad"; label = `${fmt(diff)} over`; }
  else if (target > 0) label = `On target (${diff >= 0 ? "+" : "−"}${fmt(Math.abs(diff))})`;
  const pct = target > 0 ? Math.min(100, (total / target) * 100) : 0;

  const counts = { Low: 0, Medium: 0, High: 0, none: 0 };
  for (const id of cur.song_ids) counts[state.byId.get(id)?.voice_effort || "none"]++;
  const n = cur.song_ids.length || 1;
  const seg = (k, color) => counts[k] ? `<span style="width:${(counts[k] / n) * 100}%;background:${color}"></span>` : "";

  $("#stats").innerHTML = `
    <div>
      <div class="stat-time">
        <span class="big">${fmt(total)}</span><span class="of">of ${fmt(target)} target · ${cur.song_ids.length} songs</span>
        ${target > 0 ? `<span class="stat-diff ${cls}">${label}</span>` : ""}
      </div>
      <div class="bar ${cls}"><span style="width:${pct}%"></span></div>
    </div>
    <div>
      <div class="stat-label">Voice effort</div>
      <div class="effort-bar">${seg("Low", "var(--good)")}${seg("Medium", "var(--warn)")}${seg("High", "var(--bad)")}${seg("none", "var(--line)")}</div>
      <div class="effort-legend"><span>Low ${counts.Low}</span><span>Medium ${counts.Medium}</span><span>High ${counts.High}</span>${counts.none ? `<span>Unrated ${counts.none}</span>` : ""}</div>
    </div>`;
}

function setWarnings(cur) {
  const warnings = [];
  const songs = cur.song_ids.map(id => state.byId.get(id));

  let run = [];
  const flush = () => {
    if (run.length >= 3) warnings.push({ text: `${run.length} high-effort songs in a row (#${run[0] + 1}–#${run[run.length - 1] + 1}). Consider breaking them up with an easier song.` });
    run = [];
  };
  songs.forEach((s, i) => (s?.voice_effort === "High" ? run.push(i) : flush()));
  flush();

  if (cur.mood) {
    const off = songs.filter(s => s?.mood && s.mood !== cur.mood);
    if (off.length) warnings.push({ text: `${off.length} song${off.length > 1 ? "s don't" : " doesn't"} match the set mood (${cur.mood}): ${off.map(s => s.title).join(", ")}.` });
  }
  const seen = new Set(), dupes = new Set();
  for (const id of cur.song_ids) (seen.has(id) ? dupes : seen).add(id);
  if (dupes.size) warnings.push({ bad: true, text: `Listed more than once: ${[...dupes].map(id => state.byId.get(id)?.title).join(", ")}.` });

  const missing = songs.filter(s => s?.missing);
  if (missing.length) warnings.push({ bad: true, text: `Project file not found on the last scan: ${missing.map(s => s.title).join(", ")}.` });
  const noLength = songs.filter(s => s && !s.length);
  if (noLength.length) warnings.push({ text: `No length known for ${noLength.map(s => s.title).join(", ")}; set one in the Song library.` });
  return warnings;
}

function renderWarnings() {
  $("#warnings").innerHTML = setWarnings(state.current).map(w => `<li class="${w.bad ? "bad" : ""}">${esc(w.text)}</li>`).join("");
}

function renderRows() {
  const cur = state.current;
  const tbody = $("#set-rows");
  if (!cur.song_ids.length) {
    tbody.innerHTML = `<tr class="set-empty"><td colspan="8">No songs yet. Add them from the list on the right, or use <b>Auto-fill</b>.</td></tr>`;
    return;
  }
  let elapsed = 0;
  tbody.innerHTML = cur.song_ids.map((id, i) => {
    const s = state.byId.get(id) || { title: `Unknown song #${id}`, original_artist: "" };
    elapsed += s.length || 0;
    const off = cur.mood && s.mood && s.mood !== cur.mood;
    return `<tr draggable="true" data-index="${i}" class="${off ? "off-mood" : ""} ${s.missing ? "missing" : ""}">
      <td class="handle" title="Drag to reorder">⠿</td>
      <td class="pos">${i + 1}</td>
      <td><div class="song-title">${esc(s.title)}</div><div class="song-artist">${esc(s.original_artist)}</div></td>
      <td>${moodChip(s.mood)}</td>
      <td>${effortTag(s.voice_effort)}</td>
      <td class="num">${fmt(s.length)}</td>
      <td class="num muted">${fmt(elapsed)}</td>
      <td class="row-actions">
        <button class="icon" data-act="up" title="Move up" ${i === 0 ? "disabled" : ""}>↑</button>
        <button class="icon" data-act="down" title="Move down" ${i === cur.song_ids.length - 1 ? "disabled" : ""}>↓</button>
        <button class="icon" data-act="remove" title="Remove from set">✕</button>
      </td>
    </tr>`;
  }).join("");
}

function moveSong(from, to) {
  const ids = state.current.song_ids;
  if (to < 0 || to >= ids.length || from === to) return;
  const [id] = ids.splice(from, 1);
  ids.splice(to, 0, id);
  refreshEditor();
}

function bindRowEvents() {
  const tbody = $("#set-rows");
  let dragFrom = null;

  tbody.addEventListener("click", e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const i = Number(btn.closest("tr").dataset.index);
    if (btn.dataset.act === "up") moveSong(i, i - 1);
    if (btn.dataset.act === "down") moveSong(i, i + 1);
    if (btn.dataset.act === "remove") {
      state.current.song_ids.splice(i, 1);
      refreshEditor();
      renderPicker();
    }
  });

  const clearMarks = () => $$("tr", tbody).forEach(tr => tr.classList.remove("drop-before", "drop-after", "dragging"));
  tbody.addEventListener("dragstart", e => {
    const tr = e.target.closest("tr[data-index]");
    if (!tr) return;
    dragFrom = Number(tr.dataset.index);
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", String(dragFrom));
    tr.classList.add("dragging");
  });
  tbody.addEventListener("dragover", e => {
    const tr = e.target.closest("tr[data-index]");
    if (!tr || dragFrom == null) return;
    e.preventDefault();
    const after = e.clientY > tr.getBoundingClientRect().top + tr.offsetHeight / 2;
    $$("tr", tbody).forEach(r => r.classList.remove("drop-before", "drop-after"));
    tr.classList.add(after ? "drop-after" : "drop-before");
  });
  tbody.addEventListener("drop", e => {
    const tr = e.target.closest("tr[data-index]");
    if (!tr || dragFrom == null) return;
    e.preventDefault();
    let to = Number(tr.dataset.index) + (tr.classList.contains("drop-after") ? 1 : 0);
    if (to > dragFrom) to--;
    const from = dragFrom;
    dragFrom = null;
    clearMarks();
    moveSong(from, to);
  });
  tbody.addEventListener("dragend", () => { dragFrom = null; clearMarks(); });
}

async function saveCurrent() {
  const cur = state.current;
  if (!cur || !cur.name.trim()) return;
  const body = snapshot(cur);
  try {
    const saved = cur.id == null
      ? await api("POST", "/api/setlists", body)
      : await api("PUT", `/api/setlists/${cur.id}`, body);
    cur.id = saved.id;
    cur.rpl_file = saved.rpl_file;
    state.saved = JSON.stringify(snapshot(cur));
    await Promise.all([loadSetlists(), loadSongs()]);
    refreshEditor();
    renderPicker();
    toast(`Saved. Wrote ${saved.written_to}`);
  } catch (err) {
    toast(err.message, true);
  }
}

async function revertCurrent() {
  if (state.current?.id != null && confirmDiscard()) {
    state.saved = null;
    await openSetlist(state.current.id);
  }
}

function duplicateCurrent() {
  if (!confirmDiscard()) return;
  const cur = state.current;
  state.current = { ...cur, id: null, name: uniqueName(`${cur.name} copy`), song_ids: [...cur.song_ids], rpl_file: null };
  state.saved = null;
  renderSetlistsView();
  toast("Duplicated. Rename it and Save to write a new .rpl.");
}

async function deleteCurrent() {
  const cur = state.current;
  if (cur.id == null) {
    state.current = null;
    return renderSetlistsView();
  }
  const dlg = $("#dlg-delete");
  $("#delete-text").textContent = `"${cur.name}" will be removed from the database.`;
  $("#delete-file").checked = false;
  dlg.returnValue = "";
  dlg.showModal();
  dlg.addEventListener("close", async () => {
    if (dlg.returnValue !== "confirm") return;
    try {
      const res = await api("DELETE", `/api/setlists/${cur.id}?delete_file=${$("#delete-file").checked ? 1 : 0}`);
      state.current = null;
      await Promise.all([loadSetlists(), loadSongs()]);
      renderSetlistsView();
      toast(res.file_removed ? "Setlist and .rpl file deleted (backup kept)." : "Setlist deleted. The .rpl file was left in place.");
    } catch (err) {
      toast(err.message, true);
    }
  }, { once: true });
}

// ---------- song picker ----------

const picker = { search: "", mood: "set", effort: "", unusedOnly: false };

function pickerCandidates() {
  const cur = state.current;
  const inSet = new Set(cur.song_ids);
  const q = picker.search.trim().toLowerCase();
  const mood = picker.mood === "set" ? cur.mood : picker.mood;
  return state.songs.filter(s =>
    !s.hidden && !inSet.has(s.id) &&
    (!mood || s.mood === mood) &&
    (!picker.effort || s.voice_effort === picker.effort) &&
    (!picker.unusedOnly || otherSets(s).length === 0) &&
    (!q || `${s.title} ${s.original_artist}`.toLowerCase().includes(q)));
}

function otherSets(song) {
  const savedName = state.setlists.find(s => s.id === state.current?.id)?.name;
  return song.setlists.filter(n => n !== savedName);
}

function renderPicker() {
  const el = $("#picker");
  const cur = state.current;
  if (!cur) { el.innerHTML = ""; return; }
  if (!$("#pick-list", el)) {
    const opt = (v, label, sel) => `<option value="${esc(v)}" ${sel === v ? "selected" : ""}>${esc(label)}</option>`;
    el.innerHTML = `
      <div class="pane-head"><h2>Add songs</h2><span class="pick-count" id="pick-count"></span></div>
      <input id="pick-search" type="search" placeholder="Search songs" value="${esc(picker.search)}">
      <div class="filters">
        <select id="pick-mood">${opt("set", "Set's mood", picker.mood)}${opt("", "Any mood", picker.mood)}${state.meta.moods.map(m => opt(m, m, picker.mood)).join("")}</select>
        <select id="pick-effort">${opt("", "Any effort", picker.effort)}${state.meta.efforts.map(m => opt(m, `${m} effort`, picker.effort)).join("")}</select>
      </div>
      <label class="check"><input id="pick-unused" type="checkbox" ${picker.unusedOnly ? "checked" : ""}> Only songs not in other setlists</label>
      <button id="btn-autofill" title="Add songs that fit the set's mood until the target time is reached">Auto-fill to target time</button>
      <ul class="pick-list" id="pick-list"></ul>`;
    $("#pick-search").addEventListener("input", e => { picker.search = e.target.value; renderPickList(); });
    $("#pick-mood").addEventListener("change", e => { picker.mood = e.target.value; renderPickList(); });
    $("#pick-effort").addEventListener("change", e => { picker.effort = e.target.value; renderPickList(); });
    $("#pick-unused").addEventListener("change", e => { picker.unusedOnly = e.target.checked; renderPickList(); });
    $("#btn-autofill").addEventListener("click", autoFill);
    $("#pick-list").addEventListener("click", e => {
      const li = e.target.closest("li[data-id]");
      if (li && (e.target.closest("button.add") || e.detail === 2)) addSong(Number(li.dataset.id));
    });
  }
  renderPickList();
}

function renderPickList() {
  const cur = state.current;
  const list = pickerCandidates();
  const mood = picker.mood === "set" ? cur.mood : picker.mood;
  $("#pick-count").textContent = `${list.length} available`;
  $("#pick-list").innerHTML = list.length ? list.map(s => {
    const others = otherSets(s);
    return `<li data-id="${s.id}" title="Double-click to add">
      <div><div class="song-title">${esc(s.title)}</div></div>
      <button class="add small" title="Add to end of set">+</button>
      <div class="pick-meta">
        <span>${esc(s.original_artist || "—")}</span>
        ${mood ? "" : moodChip(s.mood)}
        ${effortTag(s.voice_effort)}
        <span>${fmt(s.length)}</span>
        ${others.length ? `<span class="used" title="Also in: ${esc(others.join(", "))}">in ${others.length} set${others.length > 1 ? "s" : ""}</span>` : ""}
      </div>
    </li>`;
  }).join("") : `<li class="muted" style="display:block">No songs match these filters${mood ? ` (mood: ${esc(mood)})` : ""}.</li>`;
}

function addSong(id) {
  state.current.song_ids.push(id);
  refreshEditor();
  renderPickList();
}

/** Add songs matching the set's mood until the target is reached, avoiding 3 high-effort songs in a row. */
function autoFill() {
  const cur = state.current;
  const target = Number(cur.target_minutes) * 60;
  let total = setTotal(cur);
  if (!target) return toast("Set a target time first.", true);
  if (total >= target - 60) return toast("This set is already at its target time.");

  const pool = shuffle(pickerCandidates().filter(s => s.length && !s.missing && s.mood != null))
    .sort((a, b) => otherSets(a).length - otherSets(b).length);
  const added = [];
  const effortAt = k => state.byId.get(cur.song_ids.concat(added).at(k))?.voice_effort;
  const slack = 45;

  while (target - total >= 60) {
    const remaining = target - total;
    const fits = s => s.length <= remaining + slack;
    const tired = effortAt(-1) === "High" && effortAt(-2) === "High";
    let i = pool.findIndex(s => fits(s) && !(tired && s.voice_effort === "High"));
    if (i < 0) i = pool.findIndex(fits);
    if (i < 0) break;
    const [song] = pool.splice(i, 1);
    added.push(song.id);
    total += song.length;
  }
  if (!added.length) return toast("No matching songs fit the remaining time.", true);
  cur.song_ids.push(...added);
  refreshEditor();
  renderPickList();
  toast(`Added ${added.length} song${added.length > 1 ? "s" : ""}. Reorder or swap as you like, then Save.`);
}

// ---------- library ----------

function initLibraryFilters() {
  const { moods, efforts } = state.meta;
  $("#lib-mood").innerHTML = `<option value="">All moods</option><option value="__none">No mood set</option>` + moods.map(m => `<option>${esc(m)}</option>`).join("");
  $("#lib-effort").innerHTML = `<option value="">All voice efforts</option><option value="__none">No effort set</option>` + efforts.map(m => `<option>${esc(m)}</option>`).join("");
  ["#lib-search", "#lib-mood", "#lib-effort", "#lib-hidden"].forEach(sel => $(sel).addEventListener("input", renderLibrary));
  $$(".lib-table th[data-sort]").forEach(th => th.addEventListener("click", () => {
    const key = th.dataset.sort;
    state.libSort = { key, desc: state.libSort.key === key ? !state.libSort.desc : false };
    renderLibrary();
  }));
}

function renderLibrary() {
  const q = $("#lib-search").value.trim().toLowerCase();
  const mood = $("#lib-mood").value, effort = $("#lib-effort").value, showHidden = $("#lib-hidden").checked;
  const match = (value, filter) => !filter || (filter === "__none" ? !value : value === filter);
  const { key, desc } = state.libSort;
  const sortVal = s => key === "setlists" ? s.setlists.length : key === "length" ? (s.length || 0) : String(s[key] ?? "").toLowerCase();
  const rows = state.songs
    .filter(s => (showHidden || !s.hidden) && match(s.mood, mood) && match(s.voice_effort, effort) &&
      (!q || `${s.title} ${s.original_artist} ${s.rel_path}`.toLowerCase().includes(q)))
    .sort((a, b) => (sortVal(a) > sortVal(b) ? 1 : sortVal(a) < sortVal(b) ? -1 : 0) * (desc ? -1 : 1));

  $$(".lib-table th[data-sort]").forEach(th => {
    th.classList.toggle("sorted", th.dataset.sort === key);
    th.classList.toggle("desc", th.dataset.sort === key && desc);
  });
  const visible = state.songs.filter(s => !s.hidden);
  const unrated = visible.filter(s => !s.mood || !s.voice_effort).length;
  $("#lib-count").textContent = `${rows.length} shown · ${visible.length} songs${unrated ? ` · ${unrated} need a mood or effort` : ""}`;

  const select = (field, options, value) => `<select data-field="${field}" class="${value ? "" : "unset"}">
      <option value="">—</option>${options.map(o => `<option ${o === value ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  $("#lib-rows").innerHTML = rows.map(s => `
    <tr data-id="${s.id}" class="${s.hidden ? "is-hidden" : ""} ${s.missing ? "is-missing" : ""}">
      <td class="c-title"><input data-field="title" value="${esc(s.title)}"></td>
      <td class="c-artist" title="${esc(s.auto_info || "")}"><input data-field="original_artist" value="${esc(s.original_artist)}" placeholder="Unknown"></td>
      <td>${select("mood", state.meta.moods, s.mood)}</td>
      <td>${select("voice_effort", state.meta.efforts, s.voice_effort)}</td>
      <td class="c-length" title="Detected from project: ${fmt(s.detected_length)}">
        <input data-field="length_override" value="${s.length ? fmt(s.length) : ""}" placeholder="m:ss">
        <span class="len-src ${s.length_override != null ? "manual" : ""}">${s.length_override != null ? "manual" : "auto"}</span>
      </td>
      <td class="c-sets">${esc(s.setlists.join(", "))}</td>
      <td class="c-path" title="${esc(s.rel_path)}${s.missing ? " (not found on last scan)" : ""}">${esc(s.rel_path)}</td>
      <td><input type="checkbox" data-field="hidden" ${s.hidden ? "checked" : ""} title="Hide from the song picker"></td>
    </tr>`).join("");
}

$("#lib-rows").addEventListener("change", async e => {
  const input = e.target.closest("[data-field]");
  if (!input) return;
  const id = Number(input.closest("tr").dataset.id);
  const field = input.dataset.field;
  const song = state.byId.get(id);
  let value = input.type === "checkbox" ? input.checked : input.value;

  if (field === "length_override") {
    const secs = parseTime(value);
    if (Number.isNaN(secs)) { toast("Enter length as m:ss, e.g. 4:05", true); return renderLibrary(); }
    // Typing the detected length back in (or clearing it) returns to automatic.
    value = secs == null || (song.detected_length && Math.abs(secs - song.detected_length) < 1) ? null : secs;
  }
  try {
    const updated = await api("PATCH", `/api/songs/${id}`, { [field]: value });
    Object.assign(song, updated);
    toast(`Saved ${updated.title}`);
  } catch (err) {
    toast(err.message, true);
  }
  renderLibrary();
});

// ---------- top bar, dialogs ----------

function showView(name) {
  $$(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
  $("#view-setlists").hidden = name !== "setlists";
  $("#view-library").hidden = name !== "library";
  history.replaceState(null, "", name === "library" ? "#library" : state.current?.id ? `#set=${state.current.id}` : location.pathname);
  if (name === "library") renderLibrary();
  else if (state.current) { refreshEditor(); renderPicker(); }
}
$$(".tab").forEach(t => t.addEventListener("click", () => showView(t.dataset.view)));

$("#btn-classify").addEventListener("click", async () => {
  try {
    await api("POST", "/api/classify", {});
    watchClassify();
  } catch (err) {
    toast(err.message, true);
  }
});

/** Poll the background classifier, showing progress on the button, until it finishes. */
async function watchClassify() {
  const btn = $("#btn-classify");
  btn.disabled = true;
  try {
    for (;;) {
      const job = await api("GET", "/api/classify");
      if (!job.running) {
        await loadSongs();
        renderLibrary();
        if (job.error) toast(`Auto-classify failed: ${job.error}`, true);
        else if (job.errors.length) toast(`Updated ${job.changed} songs; ${job.errors.length} could not be looked up (${job.errors[0]}).`, true);
        else toast(job.total ? `Auto-classify updated ${job.changed} of ${job.total} songs. Hover an artist to see why.` : "Every song already has an artist, mood and effort.");
        return;
      }
      btn.textContent = job.total ? `Classifying ${job.done + 1}/${job.total}…` : "Classifying…";
      await new Promise(r => setTimeout(r, 1000));
    }
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Auto-classify";
  }
}

$("#btn-scan").addEventListener("click", runScan);

async function runScan() {
  const btn = $("#btn-scan");
  btn.disabled = true;
  btn.textContent = "Scanning…";
  try {
    const r = await api("POST", "/api/scan");
    await loadSongs();
    if (!$("#view-library").hidden) renderLibrary();
    else if (state.current) { refreshEditor(); renderPicker(); }
    const parts = [`${r.found} projects`, `${r.added} new`, `${r.updated} changed`];
    if (r.missing) parts.push(`${r.missing} no longer found`);
    toast(`Scan done: ${parts.join(", ")}.` + (r.errors.length ? ` ${r.errors.length} errors (see server window).` : ""), r.errors.length > 0);
  } catch (err) {
    toast(err.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = "Rescan projects";
  }
}

function openSettings() {
  const form = $("#dlg-settings form");
  for (const key of ["project_root", "setlist_dir", "rpl_path_root"]) form.elements[key].value = state.meta.config[key] || "";
  $("#settings-intro").hidden = state.meta.configured;
  $("#settings-error").hidden = true;
  $("#dlg-settings").showModal();
}
$("#btn-settings").addEventListener("click", openSettings);

$("#settings-save").addEventListener("click", async e => {
  e.preventDefault();
  const form = $("#dlg-settings form");
  const body = Object.fromEntries(["project_root", "setlist_dir", "rpl_path_root"].map(k => [k, form.elements[k].value]));
  try {
    const firstSetup = !state.meta.configured;
    state.meta.config = await api("PUT", "/api/config", body);
    state.meta.configured = true;
    $("#dlg-settings").close();
    if (state.current) refreshEditor();
    if (firstSetup) {
      showView("library");
      await runScan();
      if (state.songs.length) toast(`Found ${state.songs.length} projects. Click Auto-classify to fill in artists, moods and voice effort.`);
    } else {
      toast("Settings saved. Rescan if you changed the project folder.");
    }
  } catch (err) {
    const p = $("#settings-error");
    p.textContent = err.message;
    p.hidden = false;
  }
});

$("#btn-import").addEventListener("click", async () => {
  $("#import-results").innerHTML = "";
  try {
    const files = await api("GET", "/api/rpl-files");
    $("#import-files").innerHTML = files.length ? files.map(f => `
      <label class="check ${f.imported ? "done" : ""}">
        <input type="checkbox" value="${esc(f.name)}" ${f.imported ? "disabled" : "checked"}>
        ${esc(f.name)} <span class="muted">· ${f.entries} entries${f.imported ? " · already imported" : ""}</span>
      </label>`).join("") : `<p class="muted">No .rpl files in ${esc(state.meta.config.setlist_dir)}.</p>`;
    $("#import-go").disabled = !files.some(f => !f.imported);
    $("#dlg-import").showModal();
  } catch (err) {
    toast(err.message, true);
  }
});

$("#import-go").addEventListener("click", async e => {
  e.preventDefault();
  const files = $$("#import-files input:checked:not(:disabled)").map(i => i.value);
  if (!files.length) return;
  try {
    const results = await api("POST", "/api/import", { files });
    $("#import-results").innerHTML = results.map(r => r.error
      ? `<div class="import-result">${esc(r.file)}: <span class="error">${esc(r.error)}</span></div>`
      : `<div class="import-result">${esc(r.file)} → <b>${esc(r.name)}</b>: ${r.matched} songs
          ${r.unmatched.length ? `<ul>${r.unmatched.map(u => `<li>Not found: ${esc(u)}</li>`).join("")}</ul>` : ""}</div>`).join("");
    $$("#import-files input:checked:not(:disabled)").forEach(i => { i.checked = false; i.disabled = true; });
    $("#import-go").disabled = true;
    await Promise.all([loadSetlists(), loadSongs()]);
    renderSetList();
  } catch (err) {
    toast(err.message, true);
  }
});

document.addEventListener("keydown", e => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s" && state.current && !$("#view-setlists").hidden) {
    e.preventDefault();
    saveCurrent();
  }
});

window.addEventListener("beforeunload", e => {
  if (isDirty()) { e.preventDefault(); e.returnValue = ""; }
});

// ---------- start ----------

(async function init() {
  try {
    state.meta = await api("GET", "/api/meta");
    await Promise.all([loadSongs(), loadSetlists()]);
    initLibraryFilters();
    renderSetlistsView();
    const setId = Number(location.hash.match(/^#set=(\d+)$/)?.[1]);
    if (setId && state.setlists.some(s => s.id === setId)) await openSetlist(setId);
    if (location.hash === "#library") showView("library");
    if (!state.meta.configured) openSettings();
    else if ((await api("GET", "/api/classify")).running) { showView("library"); watchClassify(); }
  } catch (err) {
    $("#editor").innerHTML = `<div class="empty error">Couldn't reach the server: ${esc(err.message)}</div>`;
  }
})();
