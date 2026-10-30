"""Mode « carotte seule » : aucune alerte d'étiquette, profondeurs optionnelles saisies. Modèles factices, aucun réseau."""
import os, tempfile, time
os.environ.update(DATA_DIR=tempfile.mkdtemp())
import numpy as np, cv2, agent_core as A
A.USE_SAM = False
A.extract = lambda *a, **k: (A.TRACE.append({"etape": "x", "modele": "m", "cout_usd": 0}) or {"etiquette_lue": "", "run": -1, "profondeur_debut_m": -1, "profondeur_fin_m": -1, "longueur_foree_m": -1, "recuperation_m": -1,
  "longueur_visible_estimee_m": -1, "tape_gauche_cm": -1, "tape_droite_cm": -1, "intervalles": [{"zone": "milieu", "lithologie": A.LITHO[3], "description": "x", "confiance": 0.8}], "fractures_estimees": 2, "incertitudes": ""})
import server
from starlette.testclient import TestClient
c = TestClient(server.app)
sid = c.post("/api/sondages", json={"nom": "T"}).json()["id"]
jpg = cv2.imencode(".jpg", (np.random.rand(600, 900, 3) * 255).astype(np.uint8))[1].tobytes()
def go(**data):
    r = c.post(f"/api/sondages/{sid}/photos", files={"photo": ("a.jpg", jpg, "image/jpeg")}, data=data); assert r.status_code == 200, r.text
    for _ in range(100):
        p = c.get(f"/api/photos/{r.json()['id']}").json()
        if p["statut"] in ("terminé", "erreur"): return p
        time.sleep(0.1)
p = go(etiquette="0"); r = p["row"]
print("sans étiquette :", r["decision"], r["drapeaux"], r["infos"][:2], r["mode"], r["run"], r["lithologies"])
assert r["mode"] == "carotte" and not any("ÉTIQUETTE" in d for d in r["drapeaux"]) and r["run"] == -1 and r["decision"].startswith("AUTO")
p = go(etiquette="0", debut="3,5", fin="6.5", run="4"); r = p["row"]
print("avec profondeurs saisies :", r["run"], r["debut"], r["fin"], r["drapeaux"])
assert (r["run"], r["debut"], r["fin"]) == (4, 3.5, 6.5)
assert c.post(f"/api/sondages/{sid}/photos", files={"photo": ("a.jpg", jpg, "image/jpeg")}, data={"debut": "5", "fin": "2"}).status_code == 400
v = c.get(f"/api/sondages/{sid}").json(); print("journal :", [(i["row"]["run"], i["decision"]) for i in v["photos"]])
print("MODE CAROTTE OK")
