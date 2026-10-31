import sys, io, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, cv2, agent_core as A
vus = {}
def fake(model, step, uri, hint=""):
    c = A._ctx(A._CUR.get()); vus["litho"] = c["litho"]; vus["sys"] = c["system"]; vus["enum"] = c["schema"]["properties"]["intervalles"]["items"]["properties"]["lithologie"]["enum"]
    return {"etiquette_lue":"","run":-1,"profondeur_debut_m":-1,"profondeur_fin_m":-1,"longueur_foree_m":-1,"recuperation_m":-1,"longueur_visible_estimee_m":-1,"tape_gauche_cm":-1,"tape_droite_cm":-1,"intervalles":[{"zone":"milieu","lithologie":c["litho"][0],"description":"x","confiance":0.9}],"fractures_estimees":0,"incertitudes":""}
A.extract = fake; A.USE_SAM = False
im = (np.random.rand(300,300,3)*255).astype("uint8"); data = cv2.imencode(".jpg", im)[1].tobytes()
r = A.safe_process("a.jpg", data, None, etiquette=False, qc=False, domaine="crystalline")
assert "granite" in vus["enum"] and "sandstone" not in vus["enum"] and "other" in vus["enum"] and "unknown" in vus["enum"], vus["enum"]
assert "CRYSTALLINE" in vus["sys"].upper() or "gneiss" in vus["sys"], "règles cristallines absentes"
assert r["domaine"] == "crystalline"
r = A.safe_process("a.jpg", data, None, etiquette=False, qc=False, domaine="sedimentary")
assert "sandstone" in vus["enum"] and "granite" not in vus["enum"], vus["enum"]
r = A.safe_process("a.jpg", data, None, etiquette=False, qc=False)
assert len(vus["enum"]) == 16
# note hors domaine
r = A.safe_process("a.jpg", data, "du granite avec deux fractures", etiquette=False, qc=False, domaine="sedimentary")
assert any("hors du domaine" in d for d in r["drapeaux"]), r["drapeaux"]
r = A.safe_process("a.jpg", data, "du micaschiste", etiquette=False, qc=False, domaine="crystalline")
assert not any("hors du domaine" in d for d in r["drapeaux"]), r["drapeaux"]
r = A.safe_process("a.jpg", data, "un schiste fissile", etiquette=False, qc=False, domaine="crystalline")
assert not any("hors du domaine" in d for d in r["drapeaux"]), r["drapeaux"]
print("DOMAINE PAR PHOTO OK")
