"""Test RÉEL de bout en bout : envoie des photos (et audios) à un serveur CoreLog déployé, attend l'analyse, affiche le résultat et le coût.
Usage : python live_test.py https://ton-space.hf.space CODE_ACCES dossier_photos
Les photos sont envoyées dans l'ordre alphabétique ; un audio du même nom (p21.m4a pour p21.jpg) est joint comme note dictée."""
import sys, time, pathlib, requests
base, token, folder = sys.argv[1].rstrip("/"), sys.argv[2], pathlib.Path(sys.argv[3])
H = {"x-token": token}
sid = requests.post(f"{base}/api/sondages", json={"nom": "TEST LIVE " + time.strftime("%H:%M")}, headers=H).json()["id"]
IMG, AUD = {".jpg", ".jpeg", ".png", ".webp"}, {".m4a", ".mp3", ".wav", ".ogg", ".webm", ".opus"}
ids = []
for f in sorted(folder.iterdir()):
    if f.suffix.lower() not in IMG: continue
    files = {"photo": (f.name, f.read_bytes())}
    for a in folder.glob(f.stem + ".*"):
        if a.suffix.lower() in AUD: files["audio"] = (a.name, a.read_bytes()); break
    r = requests.post(f"{base}/api/sondages/{sid}/photos", headers=H, files=files); r.raise_for_status(); ids.append((f.name, r.json()["id"])); print("envoyée", f.name)
t0 = time.time()
for nom, pid in ids:
    while True:
        p = requests.get(f"{base}/api/photos/{pid}", headers=H).json()
        if p["statut"] in ("terminé", "erreur"): break
        time.sleep(3)
    r = p["row"] or {}
    print(f"{nom:28s} run {r.get('run')} {r.get('debut')}-{r.get('fin')} m | {r.get('lithologies')} | frac {r.get('fractures')} | {p.get('decision') or r.get('decision')} | {p['cout']} $ | {p['duree']} s")
    for d in r.get("drapeaux_total", r.get("drapeaux", [])): print("      •", d)
v = requests.get(f"{base}/api/sondages/{sid}", headers=H).json()
print("\nContrôles entre runs :" if v["controles"] else "\nAucune anomalie entre runs.", *["  • " + c for c in v["controles"]], sep="\n")
print(f"\nCoût total {v['cout_total']} $ | durée {time.time()-t0:.0f} s | export : {base}/api/sondages/{sid}/export.csv?token={token}")
