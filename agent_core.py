# ===== CoreLog : module agent (importé par server.py). Clés lues dans les variables d'environnement. =====
import os, io, re, json, time, base64, subprocess
from collections import Counter, defaultdict
import numpy as np, cv2
from concurrent.futures import ThreadPoolExecutor
from openai import OpenAI

# ---------- Réglages ----------
MODE = "open"         # "meta" = Spark 1.3 + Wonder (API Meta) | "open" = Spark Open sur Baseten (+ Glimmer si GLIMMER_URL est rempli)
SECOND_AVIS = "always" # "always" (recommandé : la confiance de Spark ne signale pas les cas difficiles) | "auto" | "never"
NOTE_CHECK = "code"    # "code" = le code compare la note à la lecture de la photo (recommandé) | "modele" = jugement du modèle (trop sévère : 9 notes sur 10 refusées)
CONF_FLAG = 0.5        # confiance en dessous de laquelle on signale la photo
CONF_SECOND = 0.6      # confiance en dessous de laquelle on demande le second avis
WORKERS = 3            # photos traitées en parallèle

KEY = os.environ.get("META_API_KEY", "")

SPARK_OPEN_KEY = os.environ.get("BASETEN_API_KEY", "")   # clé Baseten
GLIMMER_URL = os.environ.get("GLIMMER_URL", "")   # colle ici l'adresse affichée par deploy_glimmer.py (se termine par /v1)
GLIMMER_KEY = os.environ.get("GLIMMER_KEY", "")

META_URL = "https://api.meta.ai/v1"
# modèle -> (adresse, clé, prix $ par million de tokens entrée/sortie)
ENDPOINTS = {
    "muse-spark-1.3": (META_URL, KEY, (1.25, 4.25)),
    "muse-wonder-1.0": (META_URL, KEY, (4.0, 20.0)),
    "meta-models/muse-spark-open": ("https://inference.baseten.co/v1", SPARK_OPEN_KEY, (1.25, 4.25)),
    "glimmer": (GLIMMER_URL, GLIMMER_KEY, (0, 0)),   # coût réel = secondes de GPU sur Modal
}
if MODE == "open":
    PRIMARY = "meta-models/muse-spark-open"
    SECOND = "glimmer" if GLIMMER_URL else PRIMARY   # sans Glimmer : 2e lecture indépendante du même modèle (auto-cohérence, ~0,02 $)
else:
    PRIMARY, SECOND = "muse-spark-1.3", "muse-wonder-1.0"
if SECOND is None: SECOND_AVIS = "never"
TIEBREAK = True        # 3e lecture (départage) seulement si les deux premières lectures de lithologie diffèrent ; la lithologie retenue = vote majoritaire
SELF = SECOND == PRIMARY   # auto-cohérence : on compare les lithologies exactes (claystone ≠ shale)
PRICE = {m: v[2] for m, v in ENDPOINTS.items()}
_clients = {}


def get_client(model):
    if model not in _clients:
        url, k, _ = ENDPOINTS[model]
        _clients[model] = OpenAI(api_key=k, base_url=url, timeout=300)
    return _clients[model]


class _Trace(list):
    """Liste de traces ; ON_TRACE(item) est appelée à chaque nouvelle entrée (le serveur s'en sert pour la progression)."""
    ON_TRACE = None
    def append(self, item):
        super().append(item)
        if self.ON_TRACE:
            try: self.ON_TRACE(item)
            except Exception: pass


TRACE = _Trace()


# ---------- 1) Contrôle de qualité déterministe (OpenCV, aucun modèle) ----------
def core_band(im):
    h, w = im.shape[:2]
    hsv = cv2.cvtColor(im, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    wood = (H >= 5) & (H <= 28) & (S > 90) & (V > 90)
    top = int(h * 0.62)
    nonwood = cv2.morphologyEx((~wood[:top]).astype(np.uint8), cv2.MORPH_OPEN, np.ones((7, 7), np.uint8))
    rows = nonwood.mean(axis=1) > 0.55
    best, cur = (0, 0), None
    for i, r in enumerate(rows):
        if r and cur is None: cur = i
        if (not r or i == len(rows) - 1) and cur is not None:
            e = i if not r else i + 1
            if e - cur > best[1] - best[0]: best = (cur, e)
            cur = None
    return best if best[1] - best[0] > 0.1 * h else (0, h)


def quality(im, width=1000):
    s = width / im.shape[1]
    im = cv2.resize(im, (width, int(im.shape[0] * s)), interpolation=cv2.INTER_AREA)
    y0, y1 = core_band(im)
    g = cv2.cvtColor(im[y0:y1], cv2.COLOR_BGR2GRAY)
    ref = cv2.cvtColor(im[int(im.shape[0] * 0.62):], cv2.COLOR_BGR2GRAY)
    return {"nettete": round(float(cv2.Laplacian(ref, cv2.CV_64F).var()), 1),
            "luminosite": round(float(g.mean()), 1),
            "pct_noir": round(100 * float((g < 20).mean()), 1),
            "pct_blanc": round(100 * float((g > 235).mean()), 1)}


def quality_flags(q):
    r = []
    if q["nettete"] < 300: r.append("très flou")
    elif q["nettete"] < 1000: r.append("flou probable")
    if q["luminosite"] < 40 or q["pct_noir"] > 25: r.append("trop sombre")
    if q["pct_blanc"] > 20: r.append("surexposé")
    return r


# ---------- 2) Appels de modèles (avec trace d'orchestration) ----------
# Le « domaine » (jeu de lithologies, règles de description, lexique des notes) est un fichier de domains/ : CORELOG_DOMAIN=auto|sedimentary|crystalline.
# Le reste de l'agent (contrôle qualité, étiquette, ruban, vote, contrôles par le code) ne dépend pas du type de roche.
DOMAIN_ID = os.environ.get("CORELOG_DOMAIN", "auto")
_DD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "domains")
def _charge(i): return json.load(open(os.path.join(_DD, i + ".json"), encoding="utf-8"))
if DOMAIN_ID == "auto":   # fusion : l'agent choisit lui-même entre roches sédimentaires et cristallines/structurales
    _s, _c = _charge("sedimentary"), _charge("crystalline")
    _vus = set(); _lit = []
    for l in _s["lithologies"] + _c["lithologies"]:
        if l["id"] not in _vus: _vus.add(l["id"]); _lit.append(l)
    DOMAIN = {"id": "auto", "nom": {"fr": "Auto : sédimentaire + socle/structural", "en": "Auto: sedimentary + crystalline/structural"},
              "statut": "sédimentaire testé sur 10 photos réelles ; socle et structural NON TESTÉS",
              "lithologies": _lit,
              "regles_prompt": "First decide whether the core is sedimentary (layered, grains cemented, clay, carbonate) or crystalline/structural (interlocking crystals, foliation, fault rock), then choose only within the matching rock types. If you cannot decide between the two groups, use 'unknown'.\n\n"
                               + "SEDIMENTARY RULES:\n" + _s["regles_prompt"] + "\n\nCRYSTALLINE AND STRUCTURAL RULES:\n" + _c["regles_prompt"],
              "lexique_note": _c["lexique_note"] + _s["lexique_note"]}   # termes cristallins d'abord (micaschiste avant schiste)
else:
    DOMAIN = _charge(DOMAIN_ID)
# Listes globales = TOUS les types de roches (quel que soit le domaine par défaut du serveur) : le domaine choisi par photo
# restreint ce que voit le modèle (voir _ctx), mais la validation, les exports et les contrôles doivent connaître toutes les roches.
_tous, _vus2 = [], set()
for _n in ("sedimentary", "crystalline"):
    for _l in _charge(_n)["lithologies"]:
        if _l["id"] not in _vus2: _vus2.add(_l["id"]); _tous.append(_l)
for _l in DOMAIN["lithologies"]:
    if _l["id"] not in _vus2: _vus2.add(_l["id"]); _tous.append(_l)
DOMAIN["lithologies"] = _tous
LITHO = [l["id"] for l in _tous]
COARSE = {l["id"]: l["famille"] for l in _tous}
assert "unknown" in LITHO and "other" in LITHO, "un domaine doit contenir « other » et « unknown »"
SCHEMA = {"type": "object", "additionalProperties": False,
 "required": ["etiquette_lue", "run", "profondeur_debut_m", "profondeur_fin_m", "longueur_foree_m", "recuperation_m",
              "longueur_visible_estimee_m", "tape_gauche_cm", "tape_droite_cm",
              "intervalles", "fractures_estimees", "incertitudes"],
 "properties": {
  "etiquette_lue": {"type": "string", "description": "Exact characters of the handwritten paper label, one line per label line, starting with the borehole ID (e.g. 'PH-016B'). Copy only what is written. Never describe the photo, never repeat these instructions. Empty string if unreadable"},
  "run": {"type": "integer", "description": "Run number, -1 if unreadable"},
  "profondeur_debut_m": {"type": "number", "description": "Start depth in metres from the label, -1 if unreadable"},
  "profondeur_fin_m": {"type": "number", "description": "End depth in metres from the label, -1 if unreadable"},
  "longueur_foree_m": {"type": "number", "description": "Drilled run length in metres: the number BEFORE the slash on the last label line (format 'drilled / recovered'), -1 if unreadable"},
  "recuperation_m": {"type": "number", "description": "Recovered length in metres: the number AFTER the slash on the last label line, -1 if unreadable"},
  "longueur_visible_estimee_m": {"type": "number", "description": "Rough estimate of the core length visible in THIS photo, in metres"},
  "tape_gauche_cm": {"type": "number", "description": "Tape measure reading (cm) at the left edge of the core in the photo, -1 if unreadable"},
  "tape_droite_cm": {"type": "number", "description": "Tape measure reading (cm) at the right edge of the core in the photo, -1 if unreadable"},
  "intervalles": {"type": "array", "items": {"type": "object", "additionalProperties": False,
     "required": ["zone", "lithologie", "description", "confiance"],
     "properties": {"zone": {"type": "string", "enum": ["gauche", "milieu", "droite"]},
                    "lithologie": {"type": "string", "enum": LITHO},
                    "description": {"type": "string"},
                    "confiance": {"type": "number", "description": "Confidence between 0 and 1"}}}},
  "fractures_estimees": {"type": "integer"},
  "incertitudes": {"type": "string"}}}
def _system(regles):
    return (
  "You are a field geology assistant. You receive ONE photo of a drill core tray with a handwritten label, "
  "a tape measure and colour scales. Describe only what is visible. Read the label exactly "
  "(first line = borehole ID, last line = 'drilled length / recovered length' in metres). Never invent values: use -1 for unreadable numbers. "
  "Use 'unknown' only if the core is truly not visible; otherwise give your best lithology with a lower "
  "confidence. Wet glare alone is not a reason for 'unknown'. "
  + regles +
  "Report the tape readings (cm) at the left and right edge of the core. "
  "Left of the photo is usually shallower. Confidence values are between 0 and 1. "
  "Write descriptions in French.")
SYSTEM_EXTRACT = _system(DOMAIN["regles_prompt"])


# Domaine choisi PAR PHOTO (sedimentary | crystalline | auto) : restreint la liste des roches et les règles données au modèle.
import contextvars, copy
_CUR = contextvars.ContextVar("domaine", default=DOMAIN_ID)
_CTX = {}
def _ctx(d):
    if d not in ("sedimentary", "crystalline"): d = "auto" if DOMAIN_ID == "auto" or d == "auto" else DOMAIN_ID
    if d not in _CTX:
        if d == "auto" and DOMAIN_ID == "auto": dom = DOMAIN
        elif d == "auto":
            _s, _c = _charge("sedimentary"), _charge("crystalline"); _v = set(); _l = []
            for l in _s["lithologies"] + _c["lithologies"]:
                if l["id"] not in _v: _v.add(l["id"]); _l.append(l)
            dom = {"lithologies": _l, "regles_prompt": "First decide whether the core is sedimentary or crystalline/structural, then choose only within the matching rock types; if you cannot decide, use 'unknown'.\n\nSEDIMENTARY RULES:\n" + _s["regles_prompt"] + "\n\nCRYSTALLINE AND STRUCTURAL RULES:\n" + _c["regles_prompt"]}
        else: dom = _charge(d)
        litho = [l["id"] for l in dom["lithologies"]]
        sch = copy.deepcopy(SCHEMA); sch["properties"]["intervalles"]["items"]["properties"]["lithologie"]["enum"] = litho
        _CTX[d] = {"id": d, "litho": litho, "schema": sch, "system": _system(dom["regles_prompt"])}
    return _CTX[d]


SCHEMA_NOTE = {"type": "object", "additionalProperties": False, "required": ["coherent", "action", "raison"],
  "properties": {"coherent": {"type": "boolean"},
                 "action": {"type": "string", "enum": ["valider", "demander_confirmation", "demander_nouvelle_photo"]},
                 "raison": {"type": "string"}}}


def call(model, step, messages, name, schema):
    fmt = {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}
    err, t0 = None, time.time()
    for k in range(4):
        try:
            r = get_client(model).chat.completions.create(model=model, messages=messages, response_format=fmt, max_tokens=8000 + 4000 * k)
            txt = r.choices[0].message.content
            if txt:
                u = r.usage; pi, po = PRICE[model]
                TRACE.append({"etape": step, "modele": model, "s": round(time.time() - t0, 1),
                              "tokens_in": u.prompt_tokens, "tokens_out": u.completion_tokens,
                              "cout_usd": round((u.prompt_tokens * pi + u.completion_tokens * po) / 1e6, 4)})
                return json.loads(txt)
            err = "réponse vide"
        except Exception as e:
            err = str(e)[:200]
        time.sleep(2 * (k + 1))
    TRACE.append({"etape": step, "modele": model, "erreur": err})
    return None


def data_uri(im, side=1600):
    h, w = im.shape[:2]; s = min(1.0, side / max(h, w))
    if s < 1: im = cv2.resize(im, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()).decode()


def extract(model, step, uri, hint=""):
    c = _ctx(_CUR.get())
    msgs = [{"role": "system", "content": c["system"]},
            {"role": "user", "content": [{"type": "text", "text": "Extract the log for this core photo." + hint},
                                         {"type": "image_url", "image_url": {"url": uri}}]}]
    return call(model, step, msgs, "extraction", c["schema"])


def check_note(uri, note):
    msgs = [{"role": "system", "content": "Tu vérifies la cohérence entre une note dictée par un géologue et la photo d'une carotte de forage. Si la note ne correspond pas à l'image, coherent=false."},
            {"role": "user", "content": [{"type": "text", "text": f"Note du géologue : « {note} »."},
                                         {"type": "image_url", "image_url": {"url": uri}}]}]
    return call(PRIMARY, "cohérence note/photo", msgs, "coherence", SCHEMA_NOTE)


# ---------- Contrôle note / photo par le code ----------
# Le modèle lit la photo ; le code lit la note (mots-clés) et compare les familles de lithologie.
NOTE_LEX = [(p, l) for p, l in DOMAIN["lexique_note"]]
if DOMAIN_ID != "auto":   # la note est toujours lue avec les deux vocabulaires, pour détecter une roche hors du domaine choisi
    _o = [x for x in ("sedimentary", "crystalline") if x != DOMAIN_ID]
    NOTE_LEX = NOTE_LEX + [(p, l) for x in _o for p, l in _charge(x)["lexique_note"]]
NOMBRES = {"un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10}


def note_lithos(note):
    t, trouve = (note or "").lower(), set()
    for pat, litho in NOTE_LEX:
        if re.search(pat, t): trouve.add(litho)
        t = re.sub(pat, " ", t)    # un mot reconnu ne sert qu'une fois (« argile meuble » n'est pas aussi « argile »)
    return trouve


def _noms(ids):
    """Noms français lisibles (jamais de listes Python dans les messages montrés à l'utilisateur)."""
    fr = {l["id"]: re.sub(r"\s*\(.*\)", "", l["fr"]).split(",")[0] for l in DOMAIN["lithologies"]}
    return ", ".join(fr.get(i, i) for i in ids) or "—"


def note_fractures(note):
    t = (note or "").lower()
    if re.search(r"(aucune|z[ée]ro|pas\s+de|sans)\s+(aucune\s+)?fractur", t): return 0
    m = re.search(r"(?<![a-zà-ÿ])(\d+|" + "|".join(NOMBRES) + r")\s+(seule\s+)?fractures?", t)
    return None if not m else (int(m.group(1)) if m.group(1).isdigit() else NOMBRES[m.group(1)])


def check_note_code(note, lithos_photo, fractures):
    """Retourne (drapeaux, infos)."""
    dom = _ctx(_CUR.get()); nl = note_lithos(note)
    _fr = {l["id"]: re.sub(r"\s*\(.*\)", "", l["fr"]).split(",")[0] for l in DOMAIN["lithologies"]}
    nom = lambda ids: ", ".join(sorted(_fr.get(i, i) for i in ids)) or "—"
    if dom["id"] == "crystalline": nl = {("schist" if l == "shale" else l) for l in nl}   # « schiste » dit en langage courant pour un schiste cristallin
    hors = sorted(l for l in nl if l not in dom["litho"]) if dom["id"] != "auto" else []
    fam_note = {COARSE[l] for l in nl}
    fam_photo = {COARSE[l] for l in lithos_photo if l != "unknown"}
    drap, infos = [], []
    if not fam_note: infos.append("note : aucune lithologie reconnue, non comparée")
    elif fam_photo and not (fam_note & fam_photo):
        drap.append(f"NOTE incohérente avec la photo : la note parle de {nom(nl)}, la lecture de la photo donne {nom(l for l in lithos_photo if l != 'unknown')}")
    if hors: drap.append(f"NOTE hors du domaine choisi ({dom['id']}) : la note évoque {nom(hors)}")
    nf = note_fractures(note)
    if nf is not None and fractures >= 0 and abs(nf - fractures) > 2:
        infos.append(f"note : {nf} fracture(s) ; lecture de la photo : {fractures} (comptage peu fiable)")
    return drap, infos


# ---------- 3) Traitement d'une photo ----------
def clean_label(t):
    t = re.sub(r"(?i)ligne\s*_?\d+\s*(\([^)]*\))?\s*[=:]\s*", " | ", t or "")   # « Ligne 1 (ID forage) = » -> séparateur
    return t.strip(" |\n")


def parse_label(t):
    """Lit par du code ce que dit le texte d'étiquette transcrit (le modèle ne fait que recopier)."""
    t = clean_label(t); n = lambda x: float(x.replace(",", "."))
    out = {}
    m = re.search(r"R[UV]N\s*(\d+)", t, re.I)
    if m: out["run"] = int(m.group(1))
    m = re.search(r"(\d+[.,]\d+)\s*[-–—]{1,2}\s*(\d+[.,]\d+)", t)
    if m: out["debut"], out["fin"] = n(m.group(1)), n(m.group(2))
    m = re.search(r"(\d+[.,]?\d*)\s*/\s*(\d+[.,]?\d*)\s*m?\s*$", t)
    if m: out["foree_m"], out["recup_m"] = n(m.group(1)), n(m.group(2))
    return out


SCHEMA_LABEL = {"type": "object", "additionalProperties": False, "required": ["lignes"],
                "properties": {"lignes": {"type": "array", "items": {"type": "string"},
                                          "description": "Each line of the handwritten paper label, copied exactly as written"}}}


# ---------- SAM 3.1 (Meta) : localise l'étiquette papier, le code la recadre pour une relecture plus nette ----------
# Mesuré sur 10 photos : « label » trouvé 10/10 (confiance 0,77-0,93), « measuring tape » 10/10 (0,90-0,95).
# « core », « drill core », « rock core » ne donnent rien : SAM n'est donc PAS utilisé pour la carotte.
USE_SAM = True         # interrupteur général SAM
SAM_CORE = False       # True = second avis lu sur la carotte isolée par SAM | False = photo entière. Mesuré sur 10 photos : True = 6/10 instables contre 2/10, sans meilleure lithologie -> False par défaut
SAM_PRICE = 2.5 / 1000     # $ par image (page Models)
SAM_URL = "https://api.meta.ai/v1/responses"


def sam_boxes(uri, concept, step):
    """Boîtes (x1,y1,x2,y2,c) d'un concept, dans le repère de l'image envoyée. None si échec."""
    import requests
    body = {"model": "sam-3.1", "stream": True, "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": concept},
                                                                      {"type": "input_image", "image_url": uri}]}]}
    t0 = time.time()
    try:
        r = requests.post(SAM_URL, json=body, stream=True, timeout=120,
                          headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json", "Accept": "text/event-stream"})
        if r.status_code != 200: raise RuntimeError(f"HTTP {r.status_code}")
        txt = ""
        for raw in r.iter_lines():
            if not raw.startswith(b"data: "): continue
            d = raw[6:].decode("utf-8", "ignore")
            if d == "[DONE]": break
            try: ev = json.loads(d)
            except Exception: continue
            if ev.get("type") == "response.output_text.delta": txt += ev.get("delta") or ""
    except Exception as e:
        TRACE.append({"etape": step, "modele": "sam-3.1", "resultat": "échec : " + str(e)[:80]}); return None
    TRACE.append({"etape": step, "modele": "sam-3.1", "s": round(time.time() - t0, 1), "cout_usd": SAM_PRICE})
    out = []
    for m in re.finditer(r"<\|box;([^|]*)\|>", txt):
        kv = dict(p.split("=", 1) for p in m.group(1).split(";") if "=" in p)
        try: out.append({"x1": float(kv["x1"]), "y1": float(kv["y1"]), "x2": float(kv["x2"]), "y2": float(kv["y2"]), "c": float(kv.get("c", 0))})
        except Exception: pass
    return out


def label_crop_uri(uri, step):
    """Recadre l'étiquette papier (la boîte « label » la plus verticale) et l'agrandit. None si SAM ne la trouve pas."""
    if not USE_SAM: return None
    bs = sam_boxes(uri, "label", step)
    cand = [b for b in (bs or []) if b["c"] >= 0.5 and (b["y2"] - b["y1"]) >= 0.9 * (b["x2"] - b["x1"])]   # le papier est vertical ; cartes grise et couleurs sont horizontales
    if not cand: return None
    b = max(cand, key=lambda b: (b["y2"] - b["y1"]) / max(1, b["x2"] - b["x1"]))
    im = cv2.imdecode(np.frombuffer(base64.b64decode(uri.split(",", 1)[1]), np.uint8), cv2.IMREAD_COLOR)
    H, W = im.shape[:2]; mx, my = 0.15 * (b["x2"] - b["x1"]), 0.15 * (b["y2"] - b["y1"])
    x1, x2, y1, y2 = int(max(0, b["x1"] - mx)), int(min(W, b["x2"] + mx)), int(max(0, b["y1"] - my)), int(min(H, b["y2"] + my))
    crop = im[y1:y2, x1:x2]
    if crop.size == 0: return None
    return data_uri(cv2.resize(crop, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC), side=1600)


def core_crop_uri(uri, step):
    """Isole la carotte de son environnement : SAM trouve le ruban (10/10 photos), le code garde tout ce qui est AU-DESSUS.
    Résultat : une vue sans étiquette, cartes de couleur, ni planche du bas ; la carotte occupe plus de pixels. None si échec."""
    if not (USE_SAM and SAM_CORE): return None
    bs = [b for b in (sam_boxes(uri, "measuring tape", step) or []) if b["c"] >= 0.5]
    if not bs: return None
    tape = max(bs, key=lambda b: (b["x2"] - b["x1"]) * b["c"])
    im = cv2.imdecode(np.frombuffer(base64.b64decode(uri.split(",", 1)[1]), np.uint8), cv2.IMREAD_COLOR)
    H, W = im.shape[:2]
    y2 = int(tape["y1"] - 0.01 * H)
    if y2 < 0.15 * H or (tape["x2"] - tape["x1"]) < 0.5 * W: return None   # ruban douteux : on garde la photo entière
    return data_uri(im[:y2, :], side=1600)


def read_label(uri, step):
    """Outil de relecture ciblée : appelé seulement quand la transcription de l'étiquette a échoué."""
    msgs = [{"role": "system", "content": "You read handwritten labels on drill core photos. Copy the text exactly, line by line. Never describe the photo."},
            {"role": "user", "content": [{"type": "text", "text": "Copy the handwritten paper label (borehole ID, date, RUN n, depth range, drilled / recovered)."},
                                         {"type": "image_url", "image_url": {"url": uri}}]}]
    r = call(PRIMARY, step, msgs, "etiquette", SCHEMA_LABEL)
    return " | ".join(r["lignes"]) if r and r.get("lignes") else None


def hole_id(etiquette):
    etiquette = clean_label(etiquette)
    # on ne garde que le 1er segment (avant « / », « | » ou saut de ligne) : le forage, pas toute l'étiquette
    first = re.split(r"[/|\n;\[]", (etiquette or "").strip())[0]
    first = re.sub(r"(?i)^\s*ligne[_ ]?\d*[:_ ]*(id[_ ]sondage[_ ]*)?", "", first)
    h = re.sub(r"[^A-Z0-9]", "", first.upper())      # « PH.016.B » et « PH-016 B » deviennent « PH016B »
    return h if re.fullmatch(r"[A-Z]{1,3}\d{1,4}[A-Z]{0,2}", h) else ""   # sinon : pas un identifiant (description, texte recopié)


def _process(name, data, note, etiquette=True, meta=None, qc=True):
    """etiquette=False : photo sans étiquette (carotte seule, ruban facultatif) -> lithologie, fractures, qualité ; aucune alerte d'étiquette.
    meta={"run":..,"debut":..,"fin":..} : profondeurs saisies par l'utilisateur quand l'étiquette est absente. qc=False : saute le contrôle de netteté (images déjà recadrées)."""
    row = {"photo": name, "decision": "", "drapeaux": [], "infos": [], "mode": "complet" if etiquette else "carotte"}
    im = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        row["drapeaux"].append("FICHIER illisible"); row["decision"] = "NOUVELLE PHOTO"; return row
    q = quality(im); qf = quality_flags(q); row["qualite"] = q
    TRACE.append({"etape": "contrôle qualité (OpenCV)", "modele": "code", "photo": name, "resultat": qf or "OK"})
    if qf and qc:
        row["drapeaux"].append("QUALITÉ : " + ", ".join(qf)); row["decision"] = "NOUVELLE PHOTO"; return row
    uri = data_uri(im)
    with ThreadPoolExecutor(2) as ex:
        fa = ex.submit(contextvars.copy_context().run, extract, PRIMARY, f"extraction principale ({name})", uri)
        fn = ex.submit(check_note, uri, note) if (note and NOTE_CHECK == "modele") else None
        a, n = fa.result(), (fn.result() if fn else None)
    if a is None:
        row["drapeaux"].append("ÉCHEC modèle principal"); row["decision"] = "À VALIDER"; return row
    if not a["intervalles"]:   # réponse sans aucune lithologie -> une nouvelle extraction
        a = extract(PRIMARY, f"nouvelle extraction ({name})", uri) or a
    row.update({"run": a["run"], "debut": a["profondeur_debut_m"], "fin": a["profondeur_fin_m"],
                "foree_m": a["longueur_foree_m"], "recup_m": a["recuperation_m"], "visible_m": a["longueur_visible_estimee_m"],
                "tape_cm": f"{a['tape_gauche_cm']:g}-{a['tape_droite_cm']:g}",
                "fractures": a["fractures_estimees"], "etiquette": a["etiquette_lue"],
                "trou": hole_id(a["etiquette_lue"]),
                "lithologies": sorted({i["lithologie"] for i in a["intervalles"]}),
                "conf_min": min([i["confiance"] for i in a["intervalles"]] or [0]),
                "incertitudes": a["incertitudes"]})
    douteux = bool(re.search(r"(?i)incertain|illisible|possible|\?|\[", row["etiquette"] or ""))   # un crochet = annotation du modèle : une vraie étiquette n'en contient pas   # le modèle a lui-même signalé un doute (p44 : « PH-0160 [caractère final incertain] »)
    if etiquette and (not row["trou"] or (douteux and USE_SAM)):   # transcription ratée ou douteuse -> relecture ciblée de l'étiquette (outil), 1 seule fois
        cu = label_crop_uri(uri, f"SAM étiquette ({name})")
        t2 = read_label(cu or uri, f"relecture étiquette ({name})" + (" sur recadrage SAM" if cu else ""))
        if cu and not (t2 and hole_id(t2)): t2 = read_label(uri, f"relecture étiquette plein cadre ({name})")
        if t2 and hole_id(t2) and not re.search(r"(?i)incertain|illisible|possible|\?|\[", t2):
            row["etiquette"], row["trou"] = t2, hole_id(t2)
            row["infos"].append("étiquette transcrite par relecture ciblée")
        elif douteux and row["trou"]:
            row["drapeaux"].append(f"ÉTIQUETTE : identifiant « {row['trou']} » signalé incertain par le modèle, relecture non concluante")
    for k, v in parse_label(row["etiquette"]).items():   # le texte transcrit fait foi, le code corrige le champ
        if row.get(k) is not None and abs(row[k] - v) > 0.001 and row[k] != -1:
            row["infos"].append(f"{k} corrigé par le code : {row[k]:g} → {v:g} (lu dans l'étiquette transcrite)")
            row[k] = v
        elif row.get(k) == -1:
            row[k] = v
    MAP = {"run": "run", "debut": "profondeur_debut_m", "fin": "profondeur_fin_m", "foree_m": "longueur_foree_m", "recup_m": "recuperation_m"}
    for k, ka in MAP.items(): a[ka] = row[k]
    if meta:   # profondeurs saisies par l'utilisateur (photo sans étiquette lisible)
        for k in ("run", "debut", "fin"):
            if meta.get(k) is not None: row[k] = meta[k]; a[MAP[k]] = meta[k]
        row["infos"].append("profondeurs saisies par l'utilisateur, non lues sur la photo")
    elif not etiquette:
        for k in ("run", "debut", "fin", "foree_m", "recup_m"): row[k] = -1
        row["infos"].append("photo sans étiquette : profondeurs non lues")
    tg, td = a["tape_gauche_cm"], a["tape_droite_cm"]   # longueur visible : calculée par le code depuis le ruban
    if tg >= 0 and td > tg:
        calc = round((td - tg) / 100, 2)
        if abs(row["visible_m"] - calc) > 0.05: row["infos"].append(f"longueur visible recalculée par le code : {row['visible_m']:g} → {calc:g} m (ruban {tg:g}-{td:g} cm)")
        row["visible_m"] = calc
    elif row["visible_m"] != -1:   # ruban incomplet : l'estimation du modèle recopie souvent la longueur de l'étiquette
        row["infos"].append(f"ruban incomplet ({tg:g}-{td:g} cm) : longueur visible non mesurée (estimation du modèle {row['visible_m']:g} m écartée)")
        row["visible_m"] = -1
    if row["fractures"] > 15: row["drapeaux"].append(f"MESURE instable : {row['fractures']} fractures annoncées sur ~1 m, à recompter")
    label_ko = row["run"] == -1 or row["debut"] < 0 or row["fin"] < 0
    if label_ko and etiquette: row["drapeaux"].append("ÉTIQUETTE illisible (principal)")
    if not row["trou"] and etiquette: row["drapeaux"].append("ÉTIQUETTE : texte non transcrit (identifiant de forage introuvable), chiffres à vérifier")
    if etiquette and not label_ko and a["longueur_foree_m"] >= 0:
        L = row["fin"] - row["debut"]
        if abs(a["longueur_foree_m"] - L) > 0.05:
            corrige = False
            cu = label_crop_uri(uri, f"SAM étiquette ({name})")
            t3 = read_label(cu, f"relecture étiquette sur recadrage SAM ({name})") if cu else None
            p3 = parse_label(t3) if t3 else {}
            if {"debut", "fin", "foree_m"} <= p3.keys() and abs(p3["foree_m"] - (p3["fin"] - p3["debut"])) <= 0.05:
                for k, v in p3.items(): row[k] = v; a[MAP[k]] = v
                row["etiquette"] = t3; corrige = True
                row["infos"].append(f"étiquette relue sur recadrage SAM : chiffres cohérents ({p3['debut']:g}-{p3['fin']:g} m, foré {p3['foree_m']:g} m)")
                L = row["fin"] - row["debut"]
            elif t3:
                row["infos"].append(f"relecture sur recadrage SAM : toujours incohérente ({t3[:70]})")
            if not corrige:
                row["drapeaux"].append(f"ÉTIQUETTE : longueur forée {a['longueur_foree_m']} m ≠ fin − début ({L:.2f} m), lecture à vérifier")
        if a["recuperation_m"] > a["longueur_foree_m"] + 0.05:
            row["drapeaux"].append("ÉTIQUETTE : récupération > longueur forée")
        if a["recuperation_m"] >= 0:
            row["taux_recup"] = round(100 * a["recuperation_m"] / a["longueur_foree_m"])
    if row["conf_min"] < CONF_FLAG: row["drapeaux"].append(f"CONFIANCE basse ({row['conf_min']:.2f})")
    if "unknown" in row["lithologies"]: row["drapeaux"].append("lithologie inconnue")

    # second avis : seulement si nécessaire (il coûte ~5x le modèle principal)
    besoin = SECOND_AVIS == "always" or (SECOND_AVIS == "auto" and (
        row["conf_min"] < CONF_SECOND or len(row["lithologies"]) > 1 or bool(row["drapeaux"])))
    cu_core = core_crop_uri(uri, f"SAM carotte isolée ({name})") if besoin else None
    if cu_core:
        b = extract(SECOND, f"second avis sur carotte isolée par SAM ({name})", cu_core,
                    " This image is a crop showing ONLY the core (the label, tape and colour scales were removed): read lithology and fractures; use -1 for label and tape fields.")
        row["infos"].append("second avis lu sur la carotte isolée par SAM (sans étiquette, ruban ni cartes de couleur)")
    else:
        b = extract(SECOND, f"second avis indépendant ({name})", uri) if besoin else None
    row["second_avis"] = "oui" if besoin else "non"
    row["vue_second_avis"] = "carotte isolée (SAM)" if cu_core else "photo entière"
    if b is not None:
        if not cu_core:
            for k, v in parse_label(b["etiquette_lue"]).items(): b[MAP[k]] = v   # même correction par le code sur la 2e lecture
    if besoin and b is None:
        row["drapeaux"].append("second avis indisponible")
    elif b is not None:
        f = (lambda l: l) if SELF else (lambda l: COARSE[l])
        ca = {f(i["lithologie"]) for i in a["intervalles"]} - {"unknown", "inconnu"}
        cb = {f(i["lithologie"]) for i in b["intervalles"]} - {"unknown", "inconnu"}
        row["second_lithos"] = sorted({i["lithologie"] for i in b["intervalles"]})
        reads = [a, b]; cc = None
        if not cb:
            row["infos"].append("second avis : abstention (lithologie inconnue), non comptée comme désaccord")
            if row["conf_min"] < CONF_SECOND:
                row["drapeaux"].append("lithologie non confirmée : second avis en abstention et confiance basse")
        elif ca != cb:
            cc = extract(PRIMARY, f"lecture de départage ({name})", uri) if TIEBREAK else None   # 3e lecture, plein cadre
            if cc is not None: reads.append(cc)
            sets = [x for x in ({f(i["lithologie"]) for i in r_["intervalles"]} - {"unknown", "inconnu"} for r_ in reads) if x]
            cnt = Counter(l for x in sets for l in x)
            final = sorted(l for l, c in cnt.items() if c > len(sets) / 2)
            lecture = " / ".join(_noms(sorted(x)) for x in sets)
            nom = "INSTABILITÉ : lectures du même modèle" if SELF else "DÉSACCORD lithologie"
            if len(sets) >= 3 and final:
                row["drapeaux"].append(f"{nom} {lecture} → vote {_noms(final)} ({max(cnt[l] for l in final)}/{len(sets)}), à confirmer")
                if SELF: row["lithologies"] = final   # la lithologie proposée à l'humain est le vote, pas la 1re lecture
            else:
                row["drapeaux"].append(f"{nom} : {lecture}, aucune majorité")
        if row["visible_m"] == -1:   # ruban incomplet à la 1re lecture : une autre lecture l'a peut-être lu en entier
            for r_ in reads[1:]:
                tg2, td2 = r_["tape_gauche_cm"], r_["tape_droite_cm"]
                if tg2 >= 0 and td2 > tg2 and (td2 - tg2) <= 150:
                    row["visible_m"] = round((td2 - tg2) / 100, 2); row["tape_cm"] = f"{tg2:g}-{td2:g}"
                    row["infos"].append(f"ruban lu en entier sur une autre lecture ({tg2:g}-{td2:g} cm) : longueur visible {row['visible_m']:g} m")
                    break
        if len(reads) > 1:   # fractures : médiane des lectures, plage signalée
            fr = sorted(r_["fractures_estimees"] for r_ in reads if r_["fractures_estimees"] >= 0)
            if fr:
                m = len(fr) // 2
                row["fractures"] = fr[m] if len(fr) % 2 else round((fr[m - 1] + fr[m]) / 2)
                row["fractures_lues"] = fr
                proches = sum(1 for v in fr if abs(v - row["fractures"]) <= 2)
                if fr[-1] - fr[0] > 4 and proches < 2: row["drapeaux"].append(f"MESURE instable : fractures lues {fr}, médiane {row['fractures']} retenue")
        if cb and set(row["lithologies"]) != set(row["second_lithos"]):
            row["infos"].append(f"nuance lithologique : principal {_noms(row['lithologies'])} / second avis {_noms(row['second_lithos'])}")
        same_lab = (a["run"] == b["run"] and abs(a["profondeur_debut_m"] - b["profondeur_debut_m"]) < 0.05
                    and abs(a["profondeur_fin_m"] - b["profondeur_fin_m"]) < 0.05
                    and abs(a["recuperation_m"] - b["recuperation_m"]) < 0.05)
        if not same_lab and not cu_core and etiquette:
            row["drapeaux"].append(f"DÉSACCORD étiquette : run {a['run']} {a['profondeur_debut_m']}-{a['profondeur_fin_m']} / run {b['run']} {b['profondeur_debut_m']}-{b['profondeur_fin_m']}")
    if note and NOTE_CHECK == "code":
        lp = {i["lithologie"] for r_ in ([a, b, cc] if b is not None else [a]) if r_ for i in r_["intervalles"]}
        d, inf = check_note_code(note, lp, row["fractures"])
        row["drapeaux"] += d; row["infos"] += inf
    if n is not None and not n["coherent"]:
        row["drapeaux"].append(f"NOTE incohérente avec la photo : {n['raison']}")
    if note: row["note"] = note
    row["decision"] = "À VALIDER" if row["drapeaux"] else "AUTO (relecture légère)"
    return row


def process(name, data, note, etiquette=True, meta=None, qc=True, domaine=None):
    tok = _CUR.set(_ctx(domaine or DOMAIN_ID)["id"])
    try:
        row = _process(name, data, note, etiquette, meta, qc); row["domaine"] = _CUR.get(); return row
    finally: _CUR.reset(tok)


def safe_process(name, data, note, etiquette=True, meta=None, qc=True, domaine=None):
    try:
        return process(name, data, note, etiquette, meta, qc, domaine)
    except Exception as e:
        return {"photo": name, "decision": "À VALIDER", "drapeaux": [f"ERREUR : {str(e)[:150]}"], "infos": []}


# ---------- 4) Contrôles inter-photos, par du code ----------
# Plusieurs photos peuvent montrer le même run, et les runs n'ont pas tous 3 m : on contrôle
# la cohérence des étiquettes, l'ordre, les trous entre runs consécutifs, pas l'arithmétique.
def cross_checks(rows):
    out, info = [], []
    par = defaultdict(list)   # messages rattachés à une photo : ils la font passer en « À VALIDER »
    ok = [r for r in rows if r.get("run", -1) != -1 and r.get("debut", -1) >= 0]
    ids = [r["trou"] for r in ok if r.get("trou")]
    if ids:
        ref = Counter(ids).most_common(1)[0][0]
        for r in ok:
            if r.get("trou") and r["trou"] != ref:
                out.append(f"{r['photo']} : identifiant de forage lu « {r['trou']} » ≠ « {ref} » (majorité), probable erreur de lecture")
                par[r["photo"]].append(f"identifiant de forage lu « {r['trou']} » ≠ « {ref} » (majorité)")
    runs = defaultdict(list)
    for r in ok: runs[r["run"]].append(r)
    spans = []
    for n, rs in sorted(runs.items()):
        if len({(r["debut"], r["fin"], r["recup_m"]) for r in rs}) > 1:
            out.append(f"Run {n} : étiquettes différentes selon les photos : " +
                       "; ".join(f"{r['photo']} {r['debut']}-{r['fin']} ({r['recup_m']})" for r in rs))
            for r in rs: par[r["photo"]].append(f"étiquette du run {n} différente de celle des autres photos du même run")
        d, f, rc = Counter((r["debut"], r["fin"], r["recup_m"]) for r in rs).most_common(1)[0][0]
        if rc > (f - d) + 0.05: out.append(f"Run {n} : récupération {rc} m > longueur de la run {f - d:.2f} m")
        vis = sum(max(r.get("visible_m", 0), 0) for r in rs)
        info.append(f"Run {n} ({d}-{f} m, récup {rc} m) : {len(rs)} photo(s), ~{vis:.1f} m visibles au total")
        spans.append((n, d, f))
    spans.sort(key=lambda s: s[1])
    for p, q in zip(spans, spans[1:]):
        if q[0] <= p[0]:
            out.append(f"ORDRE : run {q[0]} ({q[1]} m) situé après run {p[0]} ({p[2]} m) : numéros incohérents")
        gap = q[1] - p[2]
        if gap > 0.05:
            if q[0] - p[0] == 1: out.append(f"TROU anormal entre runs consécutifs {p[0]} et {q[0]} : {gap:.2f} m non couverts")
            else: info.append(f"{q[0] - p[0] - 1} run(s) intermédiaire(s) non fourni(s) entre run {p[0]} et run {q[0]} ({gap:.2f} m)")
        elif -gap > 0.05:
            out.append(f"CHEVAUCHEMENT entre run {p[0]} et run {q[0]} : {-gap:.2f} m")
    return out, info, par



# ---------- 4b) Notes dictées : Muse Voice Transcribe (audio -> texte) ----------
# Muse Voice n'a pas d'endpoint « fichier » : on l'appelle en temps réel par WebSocket (protocole lu dans le code du connecteur Pipecat).
# Chaque audio se nomme avec le code de sa photo : p21.m4a, p05.ogg, p41 grès.mp3 ... (dis directement ta note, sans dire le nom de la photo)
import asyncio, tempfile
AUDIO_EXT = (".m4a", ".mp3", ".wav", ".ogg", ".opus", ".aac", ".amr", ".3gp", ".flac", ".weba", ".webm", ".mp4")
VOICE_URL = "wss://api.meta.ai/v1/asr/realtime"
VOICE_RATE = 16000     # Hz, mono, PCM16 (formats acceptés par Muse Voice : 16000 ou 24000)
VOICE_SPEED = 1        # 1 = temps réel (le service attend du son en continu) ; ne pas augmenter sans test
VOICE_LANG = "fr"
VOICE_PRICE_S = 0.18 / 3600   # 0,18 $ par heure d'audio


def to_pcm16(data, name):
    with tempfile.NamedTemporaryFile(suffix=os.path.splitext(name)[1] or ".bin") as f:
        f.write(data); f.flush()
        p = subprocess.run(["ffmpeg", "-v", "error", "-i", f.name, "-ac", "1", "-ar", str(VOICE_RATE), "-f", "s16le", "-"],
                           capture_output=True)
    if p.returncode or not p.stdout:
        raise RuntimeError("conversion audio impossible : " + p.stderr.decode(errors="ignore")[:150])
    return p.stdout


async def _asr(pcm):
    import websockets, uuid
    url = f"{VOICE_URL}?sessionId=stream-{uuid.uuid4()}"
    async with websockets.connect(url, max_size=None) as ws:   # pas d'en-tête HTTP : la clé part dans le 1er message
        await ws.send(json.dumps({"authorization": {"accessToken": f"Bearer {KEY}"}, "audioEncoding": "PCM_16KHZ",
                                  "model": "muse-voice-transcribe-1.0", "mode": "ENDPOINTING", "partialMode": "CUMULATIVE",
                                  "emitAudioProgress": False, "languageBias": [VOICE_LANG]}))
        rep = json.loads(await asyncio.wait_for(ws.recv(), 15))
        if "sessionId" not in rep: raise RuntimeError("handshake refusé : " + str(rep)[:200])
        finals, last = [], ""
        async def lire(timeout):
            nonlocal last
            m = await asyncio.wait_for(ws.recv(), timeout)
            if isinstance(m, bytes): return
            e = json.loads(m)
            if e.get("type") == "error": raise RuntimeError(str(e)[:200])
            if e.get("type") == "speechComplete" and e.get("transcript"): finals.append(e["transcript"].strip())
            elif e.get("type") == "transcript" and e.get("transcript"): last = e["transcript"].strip()
        async def envoyer():
            pas = VOICE_RATE // 5 * 2            # 0,2 s de PCM16 par trame
            for i in range(0, len(pcm), pas):
                await ws.send(pcm[i:i + pas]); await asyncio.sleep(0.2 / VOICE_SPEED)
            await ws.send(b"\x00" * VOICE_RATE * 2 * 2)   # 2 s de silence : le serveur détecte la fin de parole
            await asyncio.sleep(2 / VOICE_SPEED)
            await ws.send(json.dumps({"type": "endStream"}))
        tache = asyncio.create_task(envoyer())
        try:
            while True:
                try: await lire(20 if not (finals or last) else 6)
                except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed): break
                if tache.done() and tache.exception(): raise tache.exception()
        finally:
            if not tache.done(): tache.cancel()
        texte = " ".join(finals) if finals else last
        if not texte: raise RuntimeError("aucune transcription reçue")
        return texte, None


def transcribe_audio(data, name):
    t0 = time.time(); pcm = to_pcm16(data, name)
    with ThreadPoolExecutor(1) as ex:   # boucle asyncio dans un fil à part (Colab en a déjà une)
        texte, secs = ex.submit(asyncio.run, _asr(pcm)).result()
    secs = secs or len(pcm) / (VOICE_RATE * 2)
    TRACE.append({"etape": f"transcription ({name})", "modele": "muse-voice-transcribe-1.0", "s": round(time.time() - t0, 1),
                  "cout_usd": round(secs * VOICE_PRICE_S, 5)})
    return texte



def transcribe_audio(data, name):
    t0 = time.time(); pcm = to_pcm16(data, name)
    with ThreadPoolExecutor(1) as ex:   # boucle asyncio dans un fil à part (Colab en a déjà une)
        texte, secs = ex.submit(asyncio.run, _asr(pcm)).result()
    secs = secs or len(pcm) / (VOICE_RATE * 2)
    TRACE.append({"etape": f"transcription ({name})", "modele": "muse-voice-transcribe-1.0", "s": round(time.time() - t0, 1),
                  "cout_usd": round(secs * VOICE_PRICE_S, 5)})
    return texte


