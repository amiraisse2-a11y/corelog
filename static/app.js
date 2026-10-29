"use strict";
/* CoreLog : PWA sans étape de build. Écrans : Sondages, Capture (+dictée), Analyse, Validation, Journal, Architecture. */
const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const LS = { get(k) { try { return localStorage.getItem(k) } catch { return null } }, set(k, v) { try { localStorage.setItem(k, v) } catch { } } };
const LITHO = { sandstone: ["grès", "#C9A86A"], limestone: ["calcaire", "#D8D4C4"], shale: ["schiste", "#3E4C5A"], claystone: ["argilite", "#7F93A6"],
  unlithified_clay: ["argile meuble", "#A08A6E"], soil_or_fill: ["sol, remblai", "#7B5432"], other: ["autre", "#8A938D"], unknown: ["inconnu", "#B9BEB6"] };
const LEN = { sandstone: "sandstone", limestone: "limestone", shale: "shale", claystone: "claystone", unlithified_clay: "unconsolidated clay", soil_or_fill: "soil, fill", other: "other", unknown: "unknown" };
const lname = k => lang === "en" ? (LEN[k] || k) : (LITHO[k] || [k])[0], lcol = k => (LITHO[k] || [0, "#B9BEB6"])[1];
const app = $("#app");
let token = LS.get("corelog_token") || "";
let lang = LS.get("corelog_lang") || ((navigator.language || "fr").toLowerCase().startsWith("en") ? "en" : "fr");
let RULES = [];
function tr(s) { if (lang !== "en") return s; const m = String(s).match(/^(\s*)([\s\S]*?)(\s*)$/); let c = m[2]; for (const [re, r] of RULES) c = c.replace(re, r); return m[1] + c + m[3]; }
const obs = new MutationObserver(() => walk(document.body));
function walk(root) {
  if (lang !== "en") return; obs.disconnect();
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT); let n;
  while ((n = w.nextNode())) { const pn = n.parentNode && n.parentNode.nodeName; if (pn === "SCRIPT" || pn === "STYLE" || !n.nodeValue.trim()) continue; const v = tr(n.nodeValue); if (v !== n.nodeValue) n.nodeValue = v; }
  root.querySelectorAll("[placeholder],[aria-label],[alt]").forEach(el => ["placeholder", "aria-label", "alt"].forEach(a => { const x = el.getAttribute(a); if (x) { const y = tr(x); if (y !== x) el.setAttribute(a, y); } }));
  obs.observe(document.body, { childList: true, subtree: true, characterData: true });
}
function setLang(l) { lang = l; LS.set("corelog_lang", l); document.documentElement.lang = l; const b = $("#lang"); if (b) b.textContent = l === "en" ? "FR" : "EN"; route(); }
let timer = null, camStream = null, qcTimer = null;

/* ---------- API ---------- */
async function api(path, opt = {}) {
  const h = { "x-token": token, ...(opt.headers || {}) };
  let body = opt.body;
  if (body && !(body instanceof FormData) && typeof body === "object") { body = JSON.stringify(body); h["content-type"] = "application/json"; }
  const r = await fetch("/api" + path, { method: opt.method || "GET", headers: h, body });
  if (r.status === 401) { login(); throw new Error("accès refusé"); }
  if (opt.raw) return r;
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.erreur || ("erreur " + r.status));
  return j;
}
function toast(msg, ms = 3500) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, ms); }
const dec = x => lang === "en" ? x : String(x).replace(".", ",");
const money = v => dec((v || 0).toFixed(2)) + " $";
const num = (v, d = 2) => (typeof v === "number" && v >= 0) ? dec(String(+v.toFixed(d))) : "—";

function login(msg) {
  stopAll();
  app.innerHTML = `<div class="mono">CoreLog</div><h1>Code d'accès</h1>
  <p class="mut">${esc(msg || "Ce serveur est protégé. Saisis le code d'accès fourni.")}</p>
  <input id="tk" type="password" autocomplete="current-password" placeholder="Code d'accès"><p></p>
  <button class="full" id="go">Entrer</button>`;
  $("#go").onclick = async () => { token = $("#tk").value.trim(); LS.set("corelog_token", token);
    try { await api("/sondages"); route(); } catch { } };
}

/* ---------- routeur ---------- */
function stopAll() { clearTimeout(timer); clearInterval(qcTimer); if (camStream) { camStream.getTracks().forEach(t => t.stop()); camStream = null; } const c = $("#cam"); if (c) c.remove(); }
function tabs(active) {
  const t = $("#tabs"); const sid = LS.get("corelog_sondage");
  t.hidden = false;
  t.innerHTML = `<a href="#/" class="${active === "runs" ? "on" : ""}"><svg viewBox="0 0 24 24"><path d="M5 6h14M5 12h14M5 18h14"/></svg>Sondages</a>
  <a href="${sid ? "#/s/" + sid + "/journal" : "#/"}" class="${active === "journal" ? "on" : ""}"><svg viewBox="0 0 24 24"><path d="M8 3v18M8 7h8M8 13h11M8 18h6"/></svg>Journal</a>
  <a href="#/archi" class="${active === "archi" ? "on" : ""}"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="7" height="6" rx="1"/><rect x="14" y="14" width="7" height="6" rx="1"/><path d="M10 7h4a3 3 0 0 1 3 3v4"/></svg>Architecture</a>`;
}
let DOMAINE = null;
async function loadDomain() {   // les lithologies viennent du serveur (domains/*.json) : changer de type de roche ne touche pas l'interface
  if (DOMAINE) return;
  try { const c = await api("/config"); DOMAINE = c.domaine;
    for (const k in LITHO) delete LITHO[k]; for (const k in LEN) delete LEN[k];
    DOMAINE.lithologies.forEach(l => { LITHO[l.id] = [l.fr, l.couleur]; LEN[l.id] = l.en; }); } catch (e) { if (e.message === "accès refusé") throw e; }
}
async function route() {
  stopAll(); await loadDomain();
  const h = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  try {
    if (!h.length) return await vHome();
    if (h[0] === "s" && h[2] === "capture") return await vCapture(h[1]);
    if (h[0] === "s" && h[2] === "journal") return await vJournal(h[1]);
    if (h[0] === "s") return await vRuns(h[1]);
    if (h[0] === "p") return await vPhoto(h[1]);
    if (h[0] === "archi") return await vArchi();
    location.hash = "#/";
  } catch (e) { if (e.message !== "accès refusé") { app.innerHTML = `<h1>Erreur</h1><p>${esc(e.message)}</p><a class="btn sec" href="#/">Retour</a>`; } }
}
addEventListener("hashchange", route);

/* ---------- Sondages ---------- */
async function vHome() {
  tabs("runs");
  const l = await api("/sondages");
  app.innerHTML = `<div class="mono">CoreLog</div><h1>Sondages</h1>
  ${l.length ? l.map(s => `<a class="card link" href="#/s/${s.id}"><b>${esc(s.nom)}</b><div class="mut">${s.photos} photo(s)</div></a>`).join("") : `<p class="mut">Aucun sondage. Crée-en un, puis photographie tes carottes run par run.</p>`}
  <label for="nom">Nouveau sondage</label><input id="nom" type="text" placeholder="ex. PH-016B" maxlength="80"><p></p>
  <button class="full" id="add">Créer le sondage</button>`;
  $("#add").onclick = async () => { const nom = $("#nom").value.trim(); if (!nom) return toast("Donne un nom au sondage.");
    const s = await api("/sondages", { method: "POST", body: { nom } }); LS.set("corelog_sondage", s.id); location.hash = "#/s/" + s.id; };
}

const DEC = d => d === "VALIDÉ" ? ["ok", "Validé"] : d === "AUTO (relecture légère)" ? ["ok", "Auto · relecture légère"] : d === "NOUVELLE PHOTO" ? ["bad", "Photo à refaire"] : ["warn", "À valider"];
function litLabel(it) {
  const v = it.validation && it.validation.valide ? it.validation.lithologies : (it.row && it.row.lithologies) || [];
  return v.map(lname).join(", ") || "—";
}
async function vRuns(sid) {
  LS.set("corelog_sondage", sid); tabs("runs");
  const s = await api("/sondages/" + sid);
  const items = s.photos.slice().sort((a, b) => ((a.row && a.row.debut >= 0 ? a.row.debut : 1e9) - (b.row && b.row.debut >= 0 ? b.row.debut : 1e9)));
  const pend = s.photos.some(p => p.statut === "en file" || p.statut === "en cours");
  app.innerHTML = `<a href="#/" class="mut">← Sondages</a><div class="mono" style="margin-top:6px">${s.photos.length} photo(s) · ${money(s.cout_total)}</div><h1>${esc(s.nom)}</h1>
  <a class="btn full" href="#/s/${sid}/capture">Photographier un run</a><p></p>
  ${s.controles.length ? `<div class="card"><b>Contrôles entre runs</b><ul class="flags" style="margin-top:8px">${s.controles.map(c => `<li>${esc(c)}</li>`).join("")}</ul></div>` : ""}
  ${items.map(it => {
    if (it.statut !== "terminé") return `<a class="card link" href="#/p/${it.id}"><div class="row" style="align-items:center"><div class="spin" style="flex:none;width:18px"></div><div><b>Analyse en cours…</b><div class="mut">${esc(it.statut)}</div></div></div></a>`;
    const r = it.row || {}, [cl, tx] = DEC(it.decision || r.decision);
    const title = r.run >= 0 ? `Run ${r.run}` : "Run illisible";
    const dep = r.debut >= 0 ? `${num(r.debut)} – ${num(r.fin)} m` : "profondeur inconnue";
    return `<a class="card link" href="#/p/${it.id}"><div style="display:flex;justify-content:space-between;gap:8px;align-items:center"><b>${title} · ${dep}</b><span class="chip ${cl}">${tx}</span></div><div class="mut">${esc(litLabel(it))}${r.recup_m >= 0 ? ` · récup. ${num(r.recup_m)} m` : ""}</div></a>`;
  }).join("")}
  <div class="row" style="margin-top:6px"><a class="btn sec" href="#/s/${sid}/journal">Journal</a><button class="sec" id="del">Supprimer</button></div>`;
  $("#del").onclick = async () => { if (confirm(tr("Supprimer ce sondage et toutes ses photos ?"))) { await api("/sondages/" + sid, { method: "DELETE" }); LS.set("corelog_sondage", ""); location.hash = "#/"; } };
  if (pend) timer = setTimeout(route, 3000);
}

/* ---------- Capture + dictée ---------- */
async function vCapture(sid) {
  $("#tabs").hidden = true;
  let blob = null, audio = null, rec = null, chunks = [], recStart = 0;
  const v = document.createElement("div"); v.id = "cam";
  v.innerHTML = `<video playsinline muted autoplay></video>
  <div class="guides"><div class="zone core">Carotte entière</div><div class="zone tape">Ruban : première → dernière graduation</div><div class="zone label">Étiquette + cartes</div></div>
  <div class="top" id="qc"><span class="chip">Contrôle qualité…</span></div>
  <div class="bar">
    <textarea id="note" placeholder="Note écrite (facultatif) : lithologie, nombre de fractures…" style="min-height:44px;height:44px;font-size:14px"></textarea>
    <div id="recinfo" class="mut" style="color:#cfd6d1" hidden></div>
    <div style="display:flex;align-items:center;justify-content:space-between;gap:12px">
      <button class="ghost" id="close" style="color:#fff">Annuler</button>
      <button class="shutter" id="shot" aria-label="Prendre la photo"></button>
      <button class="mic sec" id="mic" aria-label="Dicter une note"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg></button>
    </div>
    <label class="btn sec" style="min-height:40px;font-size:14px">Choisir un fichier<input id="file" type="file" accept="image/*" hidden></label>
  </div>`;
  document.body.appendChild(v);
  const vid = $("video", v), qcBox = $("#qc", v);
  $("#close", v).onclick = () => { location.hash = "#/s/" + sid; };
  const chips = (probs, m) => { const ok = !probs.length;
    qcBox.innerHTML = (ok ? `<span class="chip ok">Netteté et lumière OK</span>` : probs.map(p => `<span class="chip bad">${esc(p)}</span>`).join("")) +
      (m ? `<span class="chip">netteté ${Math.round(m.nettete)}</span><span class="chip">lumière ${Math.round(m.luminosite)}</span>` : ""); };
  const frame = (maxSide, q) => new Promise(res => { const w = vid.videoWidth, h = vid.videoHeight; if (!w) return res(null);
    const k = Math.min(1, maxSide / Math.max(w, h)), c = document.createElement("canvas"); c.width = Math.round(w * k); c.height = Math.round(h * k);
    c.getContext("2d").drawImage(vid, 0, 0, c.width, c.height); c.toBlob(res, "image/jpeg", q); });
  const qcCheck = async b => { if (!b) return; const f = new FormData(); f.append("photo", b, "f.jpg");
    try { const r = await api("/qc", { method: "POST", body: f }); chips(r.problemes, r.mesures); return r; } catch { } };
  async function send(photoBlob) {
    const f = new FormData(); f.append("photo", photoBlob, "photo.jpg");
    const note = $("#note", v).value.trim(); if (note) f.append("note", note);
    if (audio) f.append("audio", audio, "note." + ((audio.type.split("/")[1] || "webm").split(";")[0].replace("mp4", "m4a")));
    $("#shot", v).disabled = true; qcBox.innerHTML = `<span class="chip">Envoi…</span>`;
    try { const r = await api(`/sondages/${sid}/photos`, { method: "POST", body: f }); location.hash = "#/p/" + r.id; }
    catch (e) { toast("Envoi impossible : " + e.message); $("#shot", v).disabled = false; }
  }
  $("#shot", v).onclick = async () => { const b = blob || await frame(2560, .92); if (!b) return toast("Caméra pas prête.");
    const r = await qcCheck(blob ? blob : (await frame(1000, .8) || b));
    if (r && r.problemes.length && !confirm(tr("Qualité douteuse (" + r.problemes.map(tr).join(", ") + "). Envoyer quand même ?"))) return;
    send(b); };
  $("#file", v).onchange = async e => { const f = e.target.files[0]; if (!f) return; blob = f; vid.style.display = "none"; clearInterval(qcTimer);
    const r = await qcCheck(f); if (r && r.problemes.length) toast("Qualité douteuse : " + r.problemes.join(", ") + ". Tu peux choisir une autre photo."); else toast("Fichier prêt : appuie sur le déclencheur."); };
  /* dictée : MediaRecorder -> fichier envoyé au serveur -> Muse Voice Transcribe */
  $("#mic", v).onclick = async () => { const mic = $("#mic", v), info = $("#recinfo", v);
    if (rec && rec.state === "recording") { rec.stop(); return; }
    try { const s = await navigator.mediaDevices.getUserMedia({ audio: true }); chunks = []; rec = new MediaRecorder(s);
      rec.ondataavailable = e => e.data.size && chunks.push(e.data);
      rec.onstop = () => { s.getTracks().forEach(t => t.stop()); audio = new Blob(chunks, { type: rec.mimeType || "audio/webm" }); mic.classList.remove("rec");
        info.textContent = `Note dictée prête (${Math.round((Date.now() - recStart) / 1000)} s). Elle sera transcrite par le serveur.`; info.hidden = false; };
      recStart = Date.now(); rec.start(); mic.classList.add("rec"); info.textContent = "Enregistrement… appuie à nouveau pour arrêter. Dis directement ta note."; info.hidden = false;
    } catch { toast("Micro refusé ou indisponible."); } };
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 2560 } }, audio: false });
    vid.srcObject = camStream; await vid.play().catch(() => { });
    qcTimer = setInterval(async () => { if (!blob && !$("#shot", v).disabled) qcCheck(await frame(480, .6)); }, 2500);
  } catch { vid.style.display = "none"; qcBox.innerHTML = `<span class="chip warn">Caméra indisponible : choisis un fichier</span>`; }
}

/* ---------- Analyse + Validation ---------- */
function stepLabel(e) {
  const s = e.etape || "";
  if (/qualit/i.test(s)) return ["Contrôle qualité de la photo", "OpenCV · code déterministe"];
  if (/transcription/i.test(s)) return ["Transcription de la note dictée", "Muse Voice Transcribe"];
  if (/départage/i.test(s)) return ["Troisième lecture (départage)", "Muse Spark Open"];
  if (/second avis/i.test(s)) return ["Seconde lecture indépendante", e.modele === "glimmer" ? "Glimmer" : "Muse Spark Open"];
  if (/extraction principale|nouvelle extraction/i.test(s)) return ["Lecture de la carotte et de l'étiquette", "Muse Spark Open"];
  if (/sam|étiquette|label/i.test(s) || /sam/i.test(e.modele || "")) return ["Relecture ciblée de l'étiquette", /sam/i.test(e.modele || "") ? "SAM 3.1 (recadrage)" : "Muse Spark Open"];
  return [s, e.modele || ""];
}
async function vPhoto(pid) {
  tabs("runs");
  const p = await api("/photos/" + pid);
  LS.set("corelog_sondage", p.sondage);
  if (p.statut !== "terminé" && p.statut !== "erreur") {
    const done = p.etapes.map(e => { const [t, m] = stepLabel(e); return `<div class="step"><div class="ic ${e.erreur ? "err" : ""}">${e.erreur ? "!" : "✓"}</div><div>${esc(t)}<small>${esc(m)}${e.s ? " · " + e.s + " s" : ""}${e.erreur ? " · " + esc(e.erreur) : ""}</small></div></div>`; }).join("");
    app.innerHTML = `<a href="#/s/${p.sondage}" class="mut">← Sondage</a><div class="mono" style="margin-top:6px">Analyse</div><h1>${p.statut === "en file" ? "En file d'attente…" : "Analyse en cours…"}</h1>
    <div class="card">${done}<div class="step"><div class="spin"></div><div>${p.statut === "en file" ? "En attente d'un créneau" : "Étape suivante…"}<small>environ 20 à 40 s par photo</small></div></div></div>
    <p class="mut">Tu peux quitter cet écran : l'analyse continue sur le serveur.</p>
    <img class="thumb" src="/api/photos/${pid}/image?token=${encodeURIComponent(token)}" alt="Photo envoyée">`;
    timer = setTimeout(route, 1500); return;
  }
  const r = p.row || {}, [cl, tx] = DEC(p.decision || r.decision);
  const retake = p.statut === "erreur" || r.decision === "NOUVELLE PHOTO" || r.run === undefined;
  const flags = (r.drapeaux_total || r.drapeaux || []), infos = r.infos || [];
  const agent = r.lithologies || [], val = p.validation && p.validation.valide ? p.validation : null;
  let sel = new Set(val ? val.lithologies : agent), fr = val && val.fractures !== undefined ? val.fractures : (r.fractures >= 0 ? r.fractures : 0);
  const lit = (r.second_lithos && r.second_lithos.length) ? [...new Set([...agent, ...r.second_lithos])] : agent;
  app.innerHTML = `<a href="#/s/${p.sondage}" class="mut">← Sondage</a>
  <div class="mono" style="margin-top:6px">Validation${r.trou ? " · " + esc(r.trou) : ""}</div>
  <h1>${r.run >= 0 ? "Run " + r.run : "Run illisible"}${r.debut >= 0 ? " · " + num(r.debut) + " – " + num(r.fin) + " m" : ""}</h1>
  <div class="chips" style="margin-bottom:12px"><span class="chip ${cl}">${tx}</span>${p.cout ? `<span class="chip">${money(p.cout)} · ${Math.round(p.duree || 0)} s</span>` : ""}</div>
  <img class="thumb" id="img" src="/api/photos/${pid}/image?token=${encodeURIComponent(token)}" alt="Photo du run">
  ${flags.length || infos.length ? `<h2>Pourquoi cette décision</h2><ul class="flags">${flags.map(f => `<li>${esc(f)}</li>`).join("")}${infos.map(f => `<li class="info">${esc(f)}</li>`).join("")}</ul>` : ""}
  ${retake ? `<p></p><div class="row"><a class="btn" href="#/s/${p.sondage}/capture" id="again">Refaire la photo</a><button class="sec" id="retry">Réessayer l'analyse</button></div>` : `
  <h2>Mesures</h2><div class="card kv">
    <div><b>Foré</b><span>${num(r.foree_m)} m</span></div><div><b>Récupéré</b><span>${num(r.recup_m)} m${r.taux_recup >= 0 ? ` (${r.taux_recup} %)` : ""}</span></div>
    <div><b>Visible (ruban)</b><span>${num(r.visible_m)} m</span></div><div><b>Fractures</b><span>${r.fractures >= 0 ? "~" + r.fractures : "—"}</span></div></div>
  ${r.note ? `<h2>Note</h2><div class="card">${esc(r.note)}</div>` : (p.note ? `<h2>Note</h2><div class="card">${esc(p.note)}</div>` : "")}
  <h2>Lithologie</h2><p class="mut" style="margin-top:-4px">Proposée par l'agent : ${esc(agent.map(lname).join(", ") || "—")}${r.conf_min !== undefined ? " · confiance min. " + r.conf_min : ""}</p>
  <div class="chips" id="lits"></div>
  <h2>Fractures (corriger)</h2>
  <div class="row" style="align-items:center"><button class="sec" id="m" aria-label="Moins">−</button><div style="text-align:center;font-size:24px;font-weight:600" id="fr"></div><button class="sec" id="pl" aria-label="Plus">+</button></div>
  <label for="com">Commentaire (facultatif)</label><input id="com" type="text" maxlength="300" value="${esc(val ? val.commentaire : "")}">
  <p></p><div class="row"><button id="ok">${val ? "Mettre à jour" : "Valider"}</button><a class="btn sec" href="#/s/${p.sondage}/capture">Refaire la photo</a></div>
  <button class="ghost full" id="del" style="margin-top:6px">Supprimer cette photo</button>`}`;
  if (retake) { $("#retry").onclick = async () => { await api(`/photos/${pid}/retry`, { method: "POST" }); route(); };
    const a = $("#again"); if (a) a.onclick = async () => { try { await api("/photos/" + pid, { method: "DELETE" }); } catch { } }; return; }
  const drawL = () => { $("#lits").innerHTML = Object.keys(LITHO).filter(k => k !== "unknown" || sel.has(k) || lit.includes(k))
      .map(k => `<button class="chip ${sel.has(k) ? "on" : ""}" data-k="${k}"><span class="dot" style="background:${lcol(k)}"></span>${esc(lname(k))}${lit.includes(k) ? " ·" : ""}</button>`).join("");
    document.querySelectorAll("#lits button").forEach(b => b.onclick = () => { sel.has(b.dataset.k) ? sel.delete(b.dataset.k) : sel.add(b.dataset.k); drawL(); });
    $("#fr").textContent = fr; };
  drawL();
  $("#m").onclick = () => { fr = Math.max(0, fr - 1); drawL(); }; $("#pl").onclick = () => { fr++; drawL(); };
  $("#ok").onclick = async () => { if (!sel.size) return toast("Choisis au moins une lithologie.");
    await api(`/photos/${pid}/validate`, { method: "POST", body: { valide: true, lithologies: [...sel], fractures: fr, commentaire: $("#com").value } });
    toast("Validé."); location.hash = "#/s/" + p.sondage; };
  $("#del").onclick = async () => { if (confirm(tr("Supprimer cette photo ?"))) { await api("/photos/" + pid, { method: "DELETE" }); location.hash = "#/s/" + p.sondage; } };
}

/* ---------- Journal ---------- */
async function download(path, name) {
  const r = await api(path + (path.includes("?") ? "&" : "?") + "lang=" + lang, { raw: true }); if (!r.ok) return toast("Export impossible.");
  const u = URL.createObjectURL(await r.blob()), a = document.createElement("a"); a.href = u; a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(u), 4000);
}
async function vJournal(sid) {
  LS.set("corelog_sondage", sid); tabs("journal");
  const s = await api("/sondages/" + sid);
  const done = s.photos.filter(i => i.statut === "terminé" && i.row && i.row.run >= 0 && i.row.debut >= 0 && i.row.fin > i.row.debut);
  const byRun = new Map(); done.forEach(i => { if (!byRun.has(i.row.run)) byRun.set(i.row.run, i); });
  const runs = [...byRun.values()].sort((a, b) => a.row.debut - b.row.debut);
  const maxD = Math.max(1, ...runs.map(i => i.row.fin)), S = 16, top = 8, H = Math.ceil(maxD * S + 24);
  let ticks = "", blocks = "", prev = null;
  const step = maxD > 60 ? 10 : maxD > 25 ? 5 : 2;
  for (let d = 0; d <= maxD; d += step) ticks += `<div class="ln" style="top:${top + d * S}px"></div><div class="tk" style="top:${top + d * S - 8}px">${d}${d === 0 ? " m" : ""}</div>`;
  runs.forEach(i => { const r = i.row, y = top + r.debut * S, h = Math.max(6, (r.fin - r.debut) * S - 2);
    if (prev !== null && r.debut - prev > 0.05) { const gy = top + prev * S; blocks += `<div class="gap" style="top:${gy}px;height:${Math.max(4, (r.debut - prev) * S - 2)}px"></div><div class="lb" style="top:${gy + 4}px;color:var(--mut)">Pas de photo · ${num(r.debut - prev, 1)} m</div>`; }
    const v = i.validation && i.validation.valide ? i.validation.lithologies : (r.lithologies || []);
    const bg = v.length > 1 ? `linear-gradient(180deg,${v.map((k, n) => `${lcol(k)} ${Math.round(n * 100 / v.length)}% ${Math.round((n + 1) * 100 / v.length)}%`).join(",")})` : lcol(v[0] || "unknown");
    const warn = i.decision === "À VALIDER";
    blocks += `<a href="#/p/${i.id}" class="blk" style="top:${y}px;height:${h}px;background:${bg}"></a><div class="lb" style="top:${y + Math.min(h / 2 - 9, 12)}px;${warn ? "font-weight:600;color:var(--o)" : ""}">Run ${r.run} · ${esc(v.map(lname).join(", ") || "—")}${warn ? " · à valider" : ""}</div>`;
    prev = r.fin; });
  const used = [...new Set(runs.flatMap(i => (i.validation && i.validation.valide ? i.validation.lithologies : i.row.lithologies) || []))];
  const unplaced = s.photos.filter(i => i.statut === "terminé" && !byRun.has(i.row && i.row.run) && !(i.row && i.row.run >= 0 && i.row.debut >= 0 && i.row.fin > i.row.debut));
  app.innerHTML = `<div class="mono">Journal · ${esc(s.nom)}</div><h1>${runs.length ? "Profondeur " + num(Math.min(...runs.map(i => i.row.debut)), 1) + " – " + num(maxD, 1) + " m" : "Aucun run placé"}</h1>
  ${runs.length ? `<div class="strip" style="height:${H}px">${ticks}${blocks}</div>
  <div class="chips" style="margin-top:10px">${used.map(k => `<span class="chip"><span class="dot" style="background:${lcol(k)}"></span>${esc(lname(k))}</span>`).join("")}<span class="chip"><span class="dot" style="border:1px dashed #8A938D;background:#E3E6DF"></span>sans photo</span></div>` : `<p class="mut">Photographie au moins un run avec une étiquette lisible.</p>`}
  ${unplaced.length ? `<h2>Photos non placées</h2>${unplaced.map(i => `<a class="card link" href="#/p/${i.id}">Étiquette illisible ou photo à refaire</a>`).join("")}` : ""}
  ${s.controles.length ? `<h2>Contrôles</h2><ul class="flags">${s.controles.map(c => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
  ${s.infos.length ? `<h2>Couverture</h2><ul class="flags">${s.infos.map(c => `<li class="info">${esc(c)}</li>`).join("")}</ul>` : ""}
  <p></p><div class="row"><button class="sec" id="csv">Exporter CSV</button><button class="sec" id="js">Exporter JSON</button></div>
  <p class="mut">Le brouillon vient de modèles de vision : tout run « à valider » doit être relu avant usage.</p>`;
  const nm = s.nom.replace(/\s+/g, "_");
  $("#csv").onclick = () => download(`/sondages/${sid}/export.csv`, `corelog_${nm}.csv`);
  $("#js").onclick = () => download(`/sondages/${sid}/export.json`, `corelog_${nm}.json`);
}

/* ---------- Architecture ---------- */
async function vArchi() {
  tabs("archi");
  const c = await api("/config").catch(() => ({ modeles: {} })), m = c.modeles || {};
  const st = ok => `<span class="chip ${ok ? "ok" : "warn"}">${ok ? "branché" : "non branché"}</span>`;
  app.innerHTML = `<div class="mono">Architecture</div><h1>Qui fait quoi</h1>
  <div class="arch">
  <div class="node"><b>PWA (ce téléphone)<span class="chip ok">ici</span></b><div class="mut">Caméra et guides, dictée, validation, journal. Aucune clé, aucun modèle ici.</div></div><div class="arrow">↓ photo + audio + note</div>
  <div class="node"><b>Serveur CoreLog<span class="chip ok">ici</span></b><div class="mut">API, file de travail, SQLite. Les clés restent côté serveur.</div></div><div class="arrow">↓</div>
  <div class="node"><b>OpenCV · code<span class="chip ok">code</span></b><div class="mut">Netteté, lumière, bande de carotte. Contrôles déterministes à la fin : arithmétique des étiquettes, ordre, trous, chevauchements, note contre photo.</div></div>
  <div class="node"><b>Muse Spark Open<span class="chip ${m["muse-spark-open"] ? "ok" : "warn"}">${m["muse-spark-open"] ? "branché" : "clé absente"}</span></b><div class="mut">Lit la photo en JSON strict, relit l'étiquette, départage les désaccords (vote majoritaire).</div></div>
  <div class="node"><b>SAM 3.1${st(m["sam-3.1"])}</b><div class="mut">Localise et recadre l'étiquette pour une relecture ciblée.</div></div>
  <div class="node"><b>Muse Voice Transcribe${st(m["muse-voice-transcribe"])}</b><div class="mut">Transcrit la note dictée (français).</div></div>
  <div class="node"><b>Glimmer${st(m.glimmer)}</b><div class="mut">Second avis indépendant, quand il est hébergé. Sinon : deuxième lecture du même modèle (auto-cohérence).</div></div>
  <div class="node"><b>Domaine géologique<span class="chip ${c.domaine && c.domaine.id !== "sedimentary" ? "warn" : "ok"}">${esc(c.domaine ? c.domaine.id : "—")}</span></b><div class="mut">${c.domaine ? esc(c.domaine.nom[lang] || c.domaine.nom.fr) + " · " + esc(c.domaine.statut) : ""}</div></div>
  <div class="node"><b>Décision<span class="chip warn">humain</span></b><div class="mut">« Auto » = relecture légère, jamais validation définitive. Toute incertitude renvoie à l'humain.</div></div></div>
  <p class="mut">Serveur protégé par code : ${c.acces_protege ? "oui" : "non"} · file : ${c.file ?? 0}</p>
  <button class="ghost" id="out">Se déconnecter</button>`;
  $("#out").onclick = () => { token = ""; LS.set("corelog_token", ""); location.hash = "#/"; route(); };
}

/* ---------- démarrage ---------- */
if ("serviceWorker" in navigator) addEventListener("load", () => navigator.serviceWorker.register("sw.js").catch(() => { }));
document.documentElement.lang = lang;
{ const b = $("#lang"); b.textContent = lang === "en" ? "FR" : "EN"; b.onclick = () => setLang(lang === "en" ? "fr" : "en"); }
fetch("i18n.json").then(r => r.json()).then(j => { RULES = j.map(([p, r]) => [new RegExp(p, "g"), r]); }).catch(() => { })
  .then(() => { obs.observe(document.body, { childList: true, subtree: true, characterData: true }); walk(document.body);
    return fetch("/api/ping").then(r => r.json()).then(p => { if (p.protege && !token) login(); else route(); }).catch(() => route()); });
