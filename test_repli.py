"""Le second lecteur (autre modèle) est injoignable : repli sur le modèle principal, sans drapeau « second avis indisponible »."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, cv2, agent_core as A
A.MODE = "mix"; A.SECOND = "muse-spark-1.3"; A.PRIMARY = A.BASETEN_MODEL; A.SECOND_AVIS = "always"; A.USE_SAM = False; A.SELF = False
seen = []
def fake(model, step, uri, hint=""):
    seen.append(model)
    if model == "muse-spark-1.3": return None
    return {"etiquette_lue":"","run":-1,"profondeur_debut_m":-1,"profondeur_fin_m":-1,"longueur_foree_m":-1,"recuperation_m":-1,"longueur_visible_estimee_m":-1,"tape_gauche_cm":-1,"tape_droite_cm":-1,"intervalles":[{"zone":"milieu","lithologie":"claystone","description":"x","confiance":0.9}],"fractures_estimees":0,"incertitudes":""}
A.extract = fake
im = (np.random.rand(300,300,3)*255).astype("uint8"); data = cv2.imencode(".jpg", im)[1].tobytes()
r = A.safe_process("a.jpg", data, None, etiquette=False, qc=False, domaine="sedimentary")
assert not any("indisponible" in d for d in r["drapeaux"]), r["drapeaux"]
assert any("indisponible" in i for i in r["infos"]), r["infos"]
assert seen.count("muse-spark-1.3") == 1 and seen.count(A.BASETEN_MODEL) >= 2, seen
print("REPLI OK")
