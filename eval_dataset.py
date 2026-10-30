"""Évalue la lithologie de l'agent sur un dossier d'images classées par sous-dossier (ex. sandstone/, limestone/, shale/).
Usage (Colab) : définir META_API_KEY et BASETEN_API_KEY dans os.environ, puis
    python eval_dataset.py /content/lithology_data 10        # 10 images par classe, tirées au hasard (graine fixe)
Mesure la lecture de la LITHOLOGIE seule (mode « carotte seule », sans étiquette ni contrôle de netteté) : ce n'est PAS le test de la chaîne complète."""
import os, sys, random, csv, time, pathlib, collections
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_core as A
A.USE_SAM = False
root, n = pathlib.Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 10
IMG = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
def classe(d):
    t = d.lower()
    for k in A.LITHO:
        if k in t or A.DOMAIN_ID and any(l["fr"].lower() in t for l in A.DOMAIN["lithologies"] if l["id"] == k): return k
    return None
par = collections.defaultdict(list)
for p in root.rglob("*"):
    if p.suffix.lower() in IMG and classe(p.parent.name): par[classe(p.parent.name)].append(p)
if not par: sys.exit("Aucun sous-dossier reconnu (noms attendus : " + ", ".join(A.LITHO) + "). Contenu : " + ", ".join(sorted({p.parent.name for p in root.rglob('*') if p.is_file()})[:20]))
random.seed(7)
lot = [(k, p) for k, ps in sorted(par.items()) for p in random.sample(ps, min(n, len(ps)))]
print({k: len(v) for k, v in par.items()}, "-> évaluées :", len(lot))
t0 = time.time()
def un(kp):
    k, p = kp
    r = A.safe_process(p.name, p.read_bytes(), None, etiquette=False, qc=False)
    return k, p.name, r
with ThreadPoolExecutor(3) as ex: res = list(ex.map(un, lot))
conf = collections.defaultdict(collections.Counter); ok = ok_fam = ok_in = 0
rows = []
for k, nom, r in res:
    pred = r.get("lithologies") or ["(échec)"]
    conf[k][",".join(pred)] += 1
    exact = pred == [k]; inside = k in pred
    fam = {A.COARSE.get(x) for x in pred} == {A.COARSE[k]}
    ok += exact; ok_in += inside; ok_fam += fam
    rows.append({"fichier": nom, "verite": k, "agent": ";".join(pred), "exact": exact, "famille_ok": fam, "decision": r.get("decision"), "drapeaux": " | ".join(r.get("drapeaux", []))})
N = len(res)
print(f"\nExact (une seule lithologie = la vérité) : {ok}/{N} = {100*ok/N:.0f} %")
print(f"Vérité incluse dans la réponse           : {ok_in}/{N} = {100*ok_in/N:.0f} %")
print(f"Bonne famille (ex. argilite/schiste = fin): {ok_fam}/{N} = {100*ok_fam/N:.0f} %")
print("\nMatrice (vérité -> lecture de l'agent) :")
for k, c in sorted(conf.items()): print(f"  {k:12s}", dict(c))
cout = sum(t.get("cout_usd", 0) for t in A.TRACE)
print(f"\nCoût {cout:.2f} $ | durée {time.time()-t0:.0f} s | à valider : {sum(r['decision']=='À VALIDER' for _,_,r in res)}/{N}")
with open("eval_resultats.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print("Détail : eval_resultats.csv")
