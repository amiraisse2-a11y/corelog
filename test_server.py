"""Test local du serveur SANS appel réseau : les lectures de modèles sont remplacées par des réponses fictives.
Ce test vérifie la plomberie (API, file, progression, validation, contrôles inter-photos, export), pas la justesse des modèles."""
import os, sys, time, tempfile, json
os.environ["DATA_DIR"] = tempfile.mkdtemp(); os.environ["CORELOG_TOKEN"] = "secret"
import numpy as np, cv2
import agent_core as A
A.USE_SAM = False

def fake(run, d, f, litho, fr=2):
    return {"etiquette_lue": f"PH-016B\nRun {run}\n{d}-{f}\n{f-d}/{f-d}", "run": run, "profondeur_debut_m": d, "profondeur_fin_m": f,
            "longueur_foree_m": f - d, "recuperation_m": f - d, "longueur_visible_estimee_m": 0.7, "tape_gauche_cm": 10, "tape_droite_cm": 80,
            "intervalles": [{"zone": "milieu", "lithologie": litho, "description": "x", "confiance": 0.8}], "fractures_estimees": fr, "incertitudes": ""}
REPONSES = {}
def fake_extract(model, step, uri, hint=""):
    A.TRACE.append({"etape": step, "modele": model, "s": 0.1, "cout_usd": 0.01})
    return REPONSES.get("cur")
A.extract = fake_extract
A.transcribe_audio = lambda data, name: (A.TRACE.append({"etape": "transcription", "modele": "muse-voice-transcribe-1.0", "cout_usd": 0.0004}) or "argilite sans fracture")
import server
from starlette.testclient import TestClient
c = TestClient(server.app); H = {"x-token": "secret"}

assert c.get("/api/sondages").status_code == 401
assert c.get("/api/ping").json()["protege"] is True
sid = c.post("/api/sondages", json={"nom": "PH 016B"}, headers=H).json()["id"]
img = (np.random.rand(900, 1400, 3) * 255).astype(np.uint8); ok, jpg = cv2.imencode(".jpg", img); jpg = jpg.tobytes()

ids = []
for run, d, f, litho in [(1, 0.0, 3.0, A.LITHO[3]), (2, 3.0, 6.0, A.LITHO[3]), (4, 9.0, 12.0, A.LITHO[2])]:
    REPONSES["cur"] = fake(run, d, f, litho)
    r = c.post(f"/api/sondages/{sid}/photos", headers=H, files={"photo": (f"r{run}.jpg", jpg, "image/jpeg"), "audio": ("n.webm", b"x", "audio/webm")} if run == 1 else {"photo": (f"r{run}.jpg", jpg, "image/jpeg")}, data={"note": "RAS"} if run != 1 else {})
    assert r.status_code == 200, r.text; ids.append(r.json()["id"])
    for _ in range(100):
        p = c.get(f"/api/photos/{ids[-1]}", headers=H).json()
        if p["statut"] in ("terminé", "erreur"): break
        time.sleep(0.1)
    assert p["statut"] == "terminé", p
print("photo 1 :", p["row"]["decision"], p["row"].get("drapeaux"), "| étapes:", [e["etape"] for e in c.get(f"/api/photos/{ids[0]}", headers=H).json()["etapes"]][:6])
v = c.get(f"/api/sondages/{sid}", headers=H).json()
print("contrôles :", v["controles"]); print("infos :", v["infos"]); print("coût total :", v["cout_total"])
print("décisions :", [(i["row"]["run"], i["decision"]) for i in v["photos"]])
assert any("run" in x.lower() or "TROU" in x for x in v["controles"] + v["infos"]), "trou entre run 2 et 4 non signalé"
print("note dictée enregistrée :", v["photos"][0]["note"])
r = c.post(f"/api/photos/{ids[2]}/validate", headers=H, json={"valide": True, "lithologies": [A.LITHO[3]], "fractures": 3, "commentaire": "ok"}); assert r.status_code == 200, r.text
assert c.post(f"/api/photos/{ids[2]}/validate", headers=H, json={"valide": True, "lithologies": ["granit"]}).status_code == 400
csvt = c.get(f"/api/sondages/{sid}/export.csv", headers=H).text; print(csvt)
js = c.get(f"/api/sondages/{sid}/export.json", headers=H).json(); assert len(js["log"]) == 3
en = c.get(f"/api/sondages/{sid}/export.csv?lang=en", headers=H).text; print(en.splitlines()[0]); assert "hole_id" in en and "TO VALIDATE" in en or "AUTO (light review)" in en or "VALIDATED" in en
print(c.get(f"/api/sondages/{sid}/export.json?lang=en", headers=H).json()["info"])
assert c.get(f"/api/photos/{ids[0]}/image?token=secret").status_code == 200
q = c.post("/api/qc", headers=H, files={"photo": ("a.jpg", jpg, "image/jpeg")}).json(); print("qc :", q)
assert c.post(f"/api/sondages/{sid}/photos", headers=H, files={"photo": ("a.jpg", b"pas une image", "image/jpeg")}).status_code == 400
print("config :", c.get("/api/config", headers=H).json())
print("TOUT OK")
