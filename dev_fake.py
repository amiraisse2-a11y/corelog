"""Serveur de DÉVELOPPEMENT avec modèles factices (aucun réseau) : sert à tester l'interface. Ne jamais déployer."""
import os, tempfile, random, itertools, time
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())
import agent_core as A
A.USE_SAM = False
_n = itertools.count(1)
def fake_extract(model, step, uri, hint=""):
    time.sleep(1.2)
    A.TRACE.append({"etape": step, "modele": model, "s": 1.2, "cout_usd": 0.01})
    i = next(_n) // 2 + 1 if "second" not in step else None
    return {"etiquette_lue": "PH-016B", "run": 3, "profondeur_debut_m": 3.6, "profondeur_fin_m": 6.6, "longueur_foree_m": 3.0, "recuperation_m": 3.0,
            "longueur_visible_estimee_m": 0.7, "tape_gauche_cm": 4, "tape_droite_cm": 73,
            "intervalles": [{"zone": "milieu", "lithologie": "claystone", "description": "x", "confiance": 0.7}], "fractures_estimees": random.choice([2, 3, 9]), "incertitudes": ""}
A.extract = fake_extract
A.transcribe_audio = lambda d, n: "argilite grise, trois fractures"
import server, uvicorn
uvicorn.run(server.app, host="127.0.0.1", port=8765, log_level="warning")
