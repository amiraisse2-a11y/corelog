"""CoreLog : serveur (Starlette + SQLite). Les clés des modèles ne quittent jamais ce serveur.

Variables d'environnement :
  META_API_KEY, BASETEN_API_KEY      clés des modèles (obligatoires pour analyser)
  GLIMMER_URL, GLIMMER_KEY           second avis Glimmer (optionnel)
  CORELOG_TOKEN                      code administrateur (voit tout, y compris l'écran Architecture)
  CORELOG_DEMO_TOKENS                codes de démonstration séparés par des virgules : chacun a son espace privé, sans Architecture,
                                     et un quota de photos (DEMO_MAX_PHOTOS, défaut 40 par code)
  DATA_DIR                           dossier des photos et de la base (défaut ./data)
Lancement : uvicorn server:app --host 0.0.0.0 --port 7860
"""
import os, io, csv, json, time, queue, sqlite3, threading, traceback, hmac, uuid
from pathlib import Path

import numpy as np, cv2
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response, FileResponse
from starlette.routing import Route, Mount
from starlette.staticfiles import StaticFiles
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

import agent_core as A

BASE = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR", BASE / "data")); (DATA / "img").mkdir(parents=True, exist_ok=True)
TOKEN = os.environ.get("CORELOG_TOKEN", "")
DEMO_TOKENS = [t.strip() for t in os.environ.get("CORELOG_DEMO_TOKENS", "").split(",") if t.strip()]
DEMO_MAX_PHOTOS = int(os.environ.get("DEMO_MAX_PHOTOS", "40"))
PROTEGE = bool(TOKEN or DEMO_TOKENS)
MAX_UPLOAD = 25 * 1024 * 1024
DONE_OK = "AUTO (relecture légère)"
import re
try:   # mêmes règles de traduction que l'interface (static/i18n.json) : une seule source
    _RULES = [(re.compile(p), re.sub(r"\$(\d)", r"\\g<\1>", r)) for p, r in json.loads((BASE / "static" / "i18n.json").read_text(encoding="utf-8"))]
except Exception:
    _RULES = []
def trn(text):
    for rx, r in _RULES: text = rx.sub(r, text)
    return text
EN_HEAD = {"photo": "photo", "trou": "hole_id", "run": "run", "debut": "start_m", "fin": "end_m", "foree_m": "drilled_m", "recup_m": "recovered_m", "taux_recup": "recovery_pct",
           "visible_m": "visible_m", "fractures": "fractures", "lithologies_agent": "lithologies_agent", "lithologies_validees": "lithologies_validated",
           "fractures_validees": "fractures_validated", "decision": "decision", "drapeaux": "flags", "note": "note", "commentaire": "comment"}
EN_DEC = {"VALIDÉ": "VALIDATED", "AUTO (relecture légère)": "AUTO (light review)", "À VALIDER": "TO VALIDATE"}

# ---------------------------------------------------------------- base
_db_lock = threading.RLock()
_db = sqlite3.connect(DATA / "corelog.db", check_same_thread=False)
_db.row_factory = sqlite3.Row
_db.executescript("""
CREATE TABLE IF NOT EXISTS sondages (id TEXT PRIMARY KEY, nom TEXT, cree REAL, owner TEXT);
CREATE TABLE IF NOT EXISTS photos (
  id TEXT PRIMARY KEY, sondage TEXT, fichier TEXT, note TEXT, statut TEXT, etapes TEXT, row TEXT,
  cout REAL, duree REAL, validation TEXT, cree REAL);
""")


for _ddl in ("ALTER TABLE sondages ADD COLUMN owner TEXT", "ALTER TABLE photos ADD COLUMN opts TEXT"):
    try: _db.execute(_ddl); _db.commit()
    except sqlite3.OperationalError: pass   # colonne déjà présente


def q(sql, *a):
    with _db_lock:
        cur = _db.execute(sql, a); _db.commit(); return cur.fetchall()


def j(x): return json.dumps(x, ensure_ascii=False, default=str)


def interdit(request, sid):
    """Un code de démonstration ne voit que ses propres sondages ; l'administrateur voit tout."""
    if request.state.role == "admin": return False
    r = q("SELECT owner FROM sondages WHERE id=?", sid)
    return (not r) or r[0]["owner"] != request.state.owner


def interdit_photo(request, pid):
    r = q("SELECT sondage FROM photos WHERE id=?", pid)
    return (not r) or interdit(request, r[0]["sondage"])


NF = lambda: JSONResponse({"erreur": "introuvable"}, 404)


# ---------------------------------------------------------------- file de travail (un seul job à la fois : TRACE est global)
JOBS = queue.Queue()
_current = {"id": None}


def _on_trace(item):
    pid = _current["id"]
    if not pid: return
    with _db_lock:
        r = q("SELECT etapes FROM photos WHERE id=?", pid)
        if not r: return
        et = json.loads(r[0]["etapes"] or "[]")
        et.append({k: item[k] for k in ("etape", "modele", "s", "cout_usd", "resultat", "erreur") if k in item})
        q("UPDATE photos SET etapes=? WHERE id=?", j(et), pid)


A.TRACE.ON_TRACE = _on_trace


def _worker():
    while True:
        pid, audio = JOBS.get()
        try:
            _run(pid, audio)
        except Exception as e:
            traceback.print_exc()
            q("UPDATE photos SET statut='erreur', row=? WHERE id=?",
              j({"photo": pid, "decision": "À VALIDER", "drapeaux": [f"ERREUR serveur : {str(e)[:200]}"], "infos": []}), pid)
        finally:
            _current["id"] = None; JOBS.task_done()


def _run(pid, audio):
    r = q("SELECT * FROM photos WHERE id=?", pid)[0]
    _current["id"] = pid; n0 = len(A.TRACE); t0 = time.time()
    q("UPDATE photos SET statut='en cours' WHERE id=?", pid)
    note = r["note"] or None
    if audio:
        try:
            note = A.transcribe_audio(audio[0], audio[1])
            q("UPDATE photos SET note=? WHERE id=?", note, pid)
        except Exception as e:
            _on_trace({"etape": "transcription impossible", "modele": "muse-voice-transcribe-1.0", "erreur": str(e)[:150]})
    data = (DATA / "img" / r["fichier"]).read_bytes()
    o = json.loads(r["opts"] or "{}")
    row = A.safe_process(r["fichier"], data, note, etiquette=o.get("etiquette", True), meta=o.get("meta"))
    cout = sum(t.get("cout_usd", 0) for t in list(A.TRACE)[n0:])
    q("UPDATE photos SET statut='terminé', row=?, cout=?, duree=? WHERE id=?", j(row), round(cout, 4), round(time.time() - t0, 1), pid)


threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------- logique de lecture d'un sondage
def sondage_view(sid):
    s = q("SELECT * FROM sondages WHERE id=?", sid)
    if not s: return None
    ps = q("SELECT * FROM photos WHERE sondage=? ORDER BY cree", sid)
    rows, items = [], []
    for p in ps:
        row = json.loads(p["row"]) if p["row"] else None
        items.append({"id": p["id"], "statut": p["statut"], "row": row, "note": p["note"], "cout": p["cout"], "duree": p["duree"],
                      "etapes": json.loads(p["etapes"] or "[]"), "validation": json.loads(p["validation"]) if p["validation"] else None})
        if row and p["statut"] == "terminé": rows.append(row)
    # contrôles inter-photos : recalculés à chaque lecture, sur des copies
    xc, info, par = A.cross_checks([dict(r) for r in rows]) if rows else ([], [], {})
    for it in items:
        r = it["row"]
        if not r or it["statut"] != "terminé": continue
        extra = ["CONTRÔLE : " + m for m in par.get(r["photo"], [])]
        r["drapeaux_total"] = list(r.get("drapeaux", [])) + extra
        auto = r.get("decision") == DONE_OK and not extra
        v = it["validation"]
        it["decision"] = "VALIDÉ" if v and v.get("valide") else ("AUTO (relecture légère)" if auto else "À VALIDER")
    return {"id": sid, "nom": s[0]["nom"], "photos": items, "controles": xc, "infos": info,
            "cout_total": round(sum(i["cout"] or 0 for i in items), 4)}


def export_rows(sid, lang="fr"):
    v = sondage_view(sid)
    en = lang == "en"
    out = []
    for it in v["photos"]:
        r = it["row"]
        if not r or it["statut"] != "terminé": continue
        val = it["validation"] or {}
        out.append({
            "photo": r.get("photo"), "trou": r.get("trou", ""), "run": r.get("run", ""), "debut": r.get("debut", ""), "fin": r.get("fin", ""),
            "foree_m": r.get("foree_m", ""), "recup_m": r.get("recup_m", ""), "taux_recup": r.get("taux_recup", ""),
            "visible_m": r.get("visible_m", ""), "fractures": r.get("fractures", ""),
            "lithologies_agent": ";".join(r.get("lithologies", [])),
            "lithologies_validees": ";".join(val.get("lithologies", [])) if val.get("valide") else "",
            "fractures_validees": val.get("fractures", "") if val.get("valide") else "",
            "decision": EN_DEC.get(it["decision"], it["decision"]) if en else it["decision"],
            "drapeaux": " | ".join(trn(x) if en else x for x in r.get("drapeaux_total", [])), "note": it["note"] or "",
            "commentaire": val.get("commentaire", "")})
    out.sort(key=lambda d: (d["debut"] if isinstance(d["debut"], (int, float)) else 1e9, d["photo"]))
    if en: out = [{EN_HEAD[k]: x for k, x in d.items()} for d in out]
    return out, v


# ---------------------------------------------------------------- API
def body(request):
    async def _b():
        try: return await request.json()
        except Exception: return {}
    return _b()


async def sondages_list(request):
    adm = request.state.role == "admin"
    rows = q("SELECT s.id, s.nom, (SELECT COUNT(*) FROM photos p WHERE p.sondage=s.id) AS n FROM sondages s " + ("" if adm else "WHERE s.owner=? ") + "ORDER BY s.cree DESC",
             *([] if adm else [request.state.owner]))
    return JSONResponse([{"id": r["id"], "nom": r["nom"], "photos": r["n"]} for r in rows])


async def sondage_create(request):
    nom = ((await body(request)).get("nom") or "").strip()[:80]
    if not nom: return JSONResponse({"erreur": "nom requis"}, 400)
    sid = uuid.uuid4().hex[:10]; q("INSERT INTO sondages (id, nom, cree, owner) VALUES (?,?,?,?)", sid, nom, time.time(), request.state.owner)
    return JSONResponse({"id": sid, "nom": nom})


async def sondage_get(request):
    if interdit(request, request.path_params["sid"]): return NF()
    v = sondage_view(request.path_params["sid"])
    return JSONResponse(v) if v else JSONResponse({"erreur": "introuvable"}, 404)


async def photo_add(request):
    sid = request.path_params["sid"]
    if not q("SELECT 1 FROM sondages WHERE id=?", sid) or interdit(request, sid): return JSONResponse({"erreur": "sondage introuvable"}, 404)
    if request.state.role == "demo":
        n = q("SELECT COUNT(*) AS n FROM photos p JOIN sondages s ON s.id=p.sondage WHERE s.owner=?", request.state.owner)[0]["n"]
        if n >= DEMO_MAX_PHOTOS: return JSONResponse({"erreur": f"quota de démonstration atteint ({DEMO_MAX_PHOTOS} photos)"}, 429)
    form = await request.form()
    ph = form.get("photo")
    if ph is None or not hasattr(ph, "read"): return JSONResponse({"erreur": "champ « photo » manquant"}, 400)
    data = await ph.read()
    if len(data) > MAX_UPLOAD: return JSONResponse({"erreur": "photo trop lourde (25 Mo max)"}, 413)
    im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if im is None: return JSONResponse({"erreur": "fichier image illisible"}, 400)
    audio = None
    au = form.get("audio")
    if au is not None and hasattr(au, "read"):
        ab = await au.read()
        if ab: audio = (ab, au.filename or "note.webm")
    pid = uuid.uuid4().hex[:12]; fichier = f"{pid}.jpg"
    (DATA / "img" / fichier).write_bytes(cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes())
    note = (form.get("note") or "").strip()[:2000] or None
    opts = {"etiquette": str(form.get("etiquette", "1")) != "0"}
    meta = {}
    for k, cast in (("run", int), ("debut", float), ("fin", float)):
        try: meta[k] = cast(str(form.get(k)).replace(",", ".")) if form.get(k) not in (None, "") else None
        except ValueError: return JSONResponse({"erreur": f"valeur invalide pour {k}"}, 400)
    if any(x is not None for x in meta.values()):
        if meta["debut"] is not None and meta["fin"] is not None and meta["fin"] <= meta["debut"]: return JSONResponse({"erreur": "la profondeur de fin doit dépasser le début"}, 400)
        opts["meta"] = meta
    q("INSERT INTO photos (id,sondage,fichier,note,statut,etapes,cree,opts) VALUES (?,?,?,?,?,?,?,?)", pid, sid, fichier, note, "en file", "[]", time.time(), j(opts))
    JOBS.put((pid, audio))
    return JSONResponse({"id": pid, "statut": "en file", "file": JOBS.qsize()})


async def photo_get(request):
    if interdit_photo(request, request.path_params["pid"]): return NF()
    pid = request.path_params["pid"]
    r = q("SELECT sondage FROM photos WHERE id=?", pid)
    if not r: return JSONResponse({"erreur": "introuvable"}, 404)
    v = sondage_view(r[0]["sondage"])
    it = next(i for i in v["photos"] if i["id"] == pid)
    it["sondage"] = r[0]["sondage"]; it["controles"] = [c for c in v["controles"] if it["row"] and it["row"].get("photo") in c]
    return JSONResponse(it)


async def photo_image(request):
    if interdit_photo(request, request.path_params["pid"]): return NF()
    r = q("SELECT fichier FROM photos WHERE id=?", request.path_params["pid"])
    if not r: return Response(status_code=404)
    return FileResponse(DATA / "img" / r[0]["fichier"], media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


async def photo_validate(request):
    """Corps : {valide: bool, lithologies: [..], fractures: int?, commentaire: str?}. valide=false annule la validation."""
    if interdit_photo(request, request.path_params["pid"]): return NF()
    pid = request.path_params["pid"]
    if not q("SELECT 1 FROM photos WHERE id=?", pid): return JSONResponse({"erreur": "introuvable"}, 404)
    b = await body(request)
    litho = [l for l in b.get("lithologies", []) if l in A.LITHO] if isinstance(b.get("lithologies"), list) else []
    if b.get("valide") and not litho: return JSONResponse({"erreur": "au moins une lithologie valide requise", "valides": list(A.LITHO)}, 400)
    fr = b.get("fractures")
    val = {"valide": bool(b.get("valide")), "lithologies": litho, "commentaire": str(b.get("commentaire") or "")[:500], "le": time.strftime("%Y-%m-%d %H:%M:%S")}
    if isinstance(fr, int) and 0 <= fr <= 200: val["fractures"] = fr
    q("UPDATE photos SET validation=? WHERE id=?", j(val), pid)
    return JSONResponse(val)


async def photo_delete(request):
    if interdit_photo(request, request.path_params["pid"]): return NF()
    pid = request.path_params["pid"]
    r = q("SELECT fichier FROM photos WHERE id=?", pid)
    if not r: return JSONResponse({"erreur": "introuvable"}, 404)
    try: (DATA / "img" / r[0]["fichier"]).unlink()
    except OSError: pass
    q("DELETE FROM photos WHERE id=?", pid); return JSONResponse({"ok": True})


async def photo_retry(request):
    if interdit_photo(request, request.path_params["pid"]): return NF()
    pid = request.path_params["pid"]
    if not q("SELECT 1 FROM photos WHERE id=?", pid): return JSONResponse({"erreur": "introuvable"}, 404)
    q("UPDATE photos SET statut='en file', etapes='[]', row=NULL, validation=NULL WHERE id=?", pid); JOBS.put((pid, None))
    return JSONResponse({"ok": True})


async def qc(request):
    """Contrôle qualité OpenCV d'une image (aperçu de la caméra) : aucun modèle, aucun coût."""
    form = await request.form(); ph = form.get("photo")
    if ph is None or not hasattr(ph, "read"): return JSONResponse({"erreur": "photo manquante"}, 400)
    im = cv2.imdecode(np.frombuffer(await ph.read(), np.uint8), cv2.IMREAD_COLOR)
    if im is None: return JSONResponse({"erreur": "image illisible"}, 400)
    qq = A.quality(im)
    return JSONResponse({"mesures": {k: (round(float(v), 3) if isinstance(v, (int, float, np.floating)) else str(v)) for k, v in qq.items()},
                         "problemes": A.quality_flags(qq)})


async def export_csv(request):
    if interdit(request, request.path_params["sid"]): return NF()
    lang = "en" if request.query_params.get("lang") == "en" else "fr"
    rows, v = export_rows(request.path_params["sid"], lang)
    if v is None: return JSONResponse({"erreur": "introuvable"}, 404)
    buf = io.StringIO(); w = csv.DictWriter(buf, fieldnames=list(rows[0]) if rows else ["photo"])
    w.writeheader(); w.writerows(rows)
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="corelog_{v["nom"].replace(" ", "_")}.csv"'})


async def export_json(request):
    if interdit(request, request.path_params["sid"]): return NF()
    lang = "en" if request.query_params.get("lang") == "en" else "fr"
    rows, v = export_rows(request.path_params["sid"], lang)
    if v is None: return JSONResponse({"erreur": "introuvable"}, 404)
    en = lang == "en"
    return Response(j({"borehole" if en else "sondage": v["nom"], "log": rows, "checks" if en else "controles": [trn(c) for c in v["controles"]] if en else v["controles"],
                       "info": [trn(c) for c in v["infos"]] if en else v["infos"], "cost_usd" if en else "cout_usd": v["cout_total"]}),
                    media_type="application/json", headers={"Content-Disposition": f'attachment; filename="corelog_{v["nom"].replace(" ", "_")}.json"'})


async def sondage_delete(request):
    if interdit(request, request.path_params["sid"]): return NF()
    sid = request.path_params["sid"]
    for p in q("SELECT fichier FROM photos WHERE sondage=?", sid):
        try: (DATA / "img" / p["fichier"]).unlink()
        except OSError: pass
    q("DELETE FROM photos WHERE sondage=?", sid); q("DELETE FROM sondages WHERE id=?", sid)
    return JSONResponse({"ok": True})


async def config(request):
    if request.state.role != "admin":   # le testeur de démonstration ne voit ni l'architecture ni l'état des modèles
        return JSONResponse({"role": "demo", "lithologies": list(A.LITHO), "domaine": {"id": A.DOMAIN_ID, "nom": A.DOMAIN["nom"], "statut": A.DOMAIN["statut"], "lithologies": A.DOMAIN["lithologies"]}})
    """État du serveur pour l'écran Architecture : quels modèles sont branchés (jamais les clés)."""
    return JSONResponse({"modeles": {"muse-spark-open": bool(A.SPARK_OPEN_KEY), "sam-3.1": bool(A.KEY and A.USE_SAM), "muse-voice-transcribe": bool(A.KEY),
                                      "glimmer": bool(A.GLIMMER_URL)},
                         "lithologies": list(A.LITHO), "role": "admin", "acces_protege": PROTEGE, "file": JOBS.qsize(),
                         "domaine": {"id": A.DOMAIN_ID, "nom": A.DOMAIN["nom"], "statut": A.DOMAIN["statut"], "lithologies": A.DOMAIN["lithologies"]}})


async def health(request): return JSONResponse({"ok": True})


# ---------------------------------------------------------------- accès
class Garde(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        import hashlib
        p = request.url.path
        request.state.role, request.state.owner = "admin", "public"
        if PROTEGE and p.startswith("/api/") and p != "/api/ping":
            sent = request.headers.get("x-token") or request.query_params.get("token") or ""
            if TOKEN and hmac.compare_digest(sent, TOKEN): request.state.role, request.state.owner = "admin", "admin"
            elif any(hmac.compare_digest(sent, t) for t in DEMO_TOKENS):
                request.state.role, request.state.owner = "demo", hashlib.sha256(sent.encode()).hexdigest()[:12]
            else: return JSONResponse({"erreur": "code d'accès invalide"}, 401)
        return await call_next(request)


async def ping(request): return JSONResponse({"ok": True, "protege": PROTEGE})


routes = [
    Route("/api/ping", ping),
    Route("/api/config", config),
    Route("/api/qc", qc, methods=["POST"]),
    Route("/api/sondages", sondages_list),
    Route("/api/sondages", sondage_create, methods=["POST"]),
    Route("/api/sondages/{sid}", sondage_get),
    Route("/api/sondages/{sid}", sondage_delete, methods=["DELETE"]),
    Route("/api/sondages/{sid}/photos", photo_add, methods=["POST"]),
    Route("/api/sondages/{sid}/export.csv", export_csv),
    Route("/api/sondages/{sid}/export.json", export_json),
    Route("/api/photos/{pid}", photo_get),
    Route("/api/photos/{pid}", photo_delete, methods=["DELETE"]),
    Route("/api/photos/{pid}/image", photo_image),
    Route("/api/photos/{pid}/validate", photo_validate, methods=["POST"]),
    Route("/api/photos/{pid}/retry", photo_retry, methods=["POST"]),
    Route("/health", health),
    Mount("/", StaticFiles(directory=BASE / "static", html=True)),
]
app = Starlette(routes=routes, middleware=[Middleware(Garde)])
