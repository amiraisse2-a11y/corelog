"""Rôles : administrateur et codes de démonstration (espaces privés, quota, pas d'Architecture). Modèles factices, aucun réseau."""
import os, tempfile, time
os.environ.update(DATA_DIR=tempfile.mkdtemp(), CORELOG_TOKEN="adm", CORELOG_DEMO_TOKENS="demoA, demoB", DEMO_MAX_PHOTOS="2")
import numpy as np, cv2, agent_core as A
A.USE_SAM = False
A.extract = lambda *a, **k: (A.TRACE.append({"etape": "x", "modele": "m", "cout_usd": 0}) or {"etiquette_lue": "PH-1", "run": 1, "profondeur_debut_m": 0.0, "profondeur_fin_m": 3.0, "longueur_foree_m": 3.0, "recuperation_m": 3.0,
  "longueur_visible_estimee_m": 0.7, "tape_gauche_cm": 10, "tape_droite_cm": 80, "intervalles": [{"zone": "milieu", "lithologie": A.LITHO[3], "description": "x", "confiance": 0.8}], "fractures_estimees": 1, "incertitudes": ""})
import server
from starlette.testclient import TestClient
c = TestClient(server.app)
H = lambda t: {"x-token": t}
jpg = cv2.imencode(".jpg", (np.random.rand(600, 900, 3) * 255).astype(np.uint8))[1].tobytes()
assert c.get("/api/sondages").status_code == 401 and c.get("/api/sondages", headers=H("nope")).status_code == 401
sa = c.post("/api/sondages", json={"nom": "ADMIN"}, headers=H("adm")).json()["id"]
s1 = c.post("/api/sondages", json={"nom": "A"}, headers=H("demoA")).json()["id"]
assert [x["nom"] for x in c.get("/api/sondages", headers=H("demoA")).json()] == ["A"]
assert [x["nom"] for x in c.get("/api/sondages", headers=H("demoB")).json()] == []
assert {x["nom"] for x in c.get("/api/sondages", headers=H("adm")).json()} == {"ADMIN", "A"}
assert c.get(f"/api/sondages/{sa}", headers=H("demoA")).status_code == 404       # pas le sondage de l'admin
assert c.get(f"/api/sondages/{s1}", headers=H("demoB")).status_code == 404       # pas celui d'un autre testeur
assert c.get(f"/api/sondages/{s1}/export.csv", headers=H("demoB")).status_code == 404
cfg = c.get("/api/config", headers=H("demoA")).json(); assert cfg["role"] == "demo" and "modeles" not in cfg
assert "modeles" in c.get("/api/config", headers=H("adm")).json()
pid = None
for k in range(3):
    r = c.post(f"/api/sondages/{s1}/photos", headers=H("demoA"), files={"photo": ("a.jpg", jpg, "image/jpeg")})
    if k < 2: assert r.status_code == 200, r.text; pid = r.json()["id"]
    else: assert r.status_code == 429, (r.status_code, r.text)                    # quota atteint
assert c.get(f"/api/photos/{pid}", headers=H("demoB")).status_code == 404
assert c.get(f"/api/photos/{pid}/image?token=demoB").status_code == 404
assert c.post(f"/api/photos/{pid}/validate", headers=H("demoB"), json={"valide": True, "lithologies": [A.LITHO[3]]}).status_code == 404
assert c.delete(f"/api/photos/{pid}", headers=H("demoB")).status_code == 404
assert c.get(f"/api/photos/{pid}/image?token=demoA").status_code == 200
assert c.get(f"/api/photos/{pid}", headers=H("adm")).status_code == 200
print("RÔLES OK")
