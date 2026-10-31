# CoreLog

Photo d'un plateau de carottes (+ note dictée facultative) → brouillon de journal lithologique structuré (run, profondeurs, foré/récupéré, lithologie, fractures), avec contrôles par le code, vote, abstention et validation humaine. Export CSV/JSON.

Projet du Meta Global AI Developer Hackathon (piste Indie).

## Principe
Le modèle propose, le code contrôle, l'humain décide. « Auto » signifie « relecture légère », jamais « validé ».

| Étape | Qui | Rôle |
|---|---|---|
| Qualité (netteté, lumière) | OpenCV, code | refuse les photos inexploitables avant tout appel payant |
| Lecture carotte + étiquette | Muse Spark Open (Baseten) | JSON strict |
| Seconde lecture | Muse Spark Open (ou Glimmer si `GLIMMER_URL` est défini) | auto-cohérence ; départage à 3 lectures si désaccord (vote majoritaire) |
| Étiquette douteuse | SAM 3.1 + Spark Open | recadrage et relecture ciblée |
| Note dictée | Muse Voice Transcribe | transcription (français) |
| Contrôles | code Python | arithmétique foré/récupéré, ordre, trous, chevauchements, note contre photo, identifiant de forage |
| Décision | humain | validation, correction, export |

## Lancer
```
pip install -r requirements.txt   # + ffmpeg installé sur la machine
export META_API_KEY=...  BASETEN_API_KEY=...  CORELOG_TOKEN=un-code-d-acces
uvicorn server:app --port 7860
```
Render (offre gratuite, Docker) : voir `render.yaml` ; les clés se saisissent dans le tableau de bord Render (variables d'environnement `META_API_KEY`, `BASETEN_API_KEY`, `CORELOG_TOKEN`, optionnel `GLIMMER_URL`, `GLIMMER_KEY`). Aucune clé dans le dépôt ni dans le navigateur. Le Space Hugging Face Docker n'est plus gratuit (abonnement PRO requis). Sans `CORELOG_TOKEN`, quiconque connaît l'adresse dépense tes crédits : toujours en définir un en ligne.
Tests sans réseau : `PYTHONPATH=stub:. python test_server.py` (modèles remplacés par des réponses fictives) ; interface : `PYTHONPATH=stub:. python dev_fake.py`, puis http://127.0.0.1:8765 (le dossier `stub` ne sert qu'à ces tests ; ne pas l'utiliser en production, où le vrai paquet `openai` est installé).

## Accès : administrateur et testeurs
`CORELOG_TOKEN` = code administrateur (voit tout, dont l'écran Architecture et l'état des modèles). `CORELOG_DEMO_TOKENS` = codes de démonstration séparés par des virgules (ex. `demo-ana,demo-koffi`) : chaque code a son espace **privé** (les photos d'un testeur ne sont jamais visibles par un autre), pas d'écran Architecture, et un quota de `DEMO_MAX_PHOTOS` photos (40 par défaut) pour protéger tes crédits. Pour révoquer un testeur, retire son code de la variable.

## Transférabilité (type de roche)
Le type de roche est une donnée, pas du code : `domains/<id>.json` définit les lithologies (id, noms FR/EN, couleur, famille), les règles de description données au modèle et le lexique des notes dictées. `CORELOG_DOMAIN=auto` (défaut : fusion des deux ensembles ci-dessous, l'agent choisit lui-même ; la partie sédimentaire est testée, la partie socle ne l'est pas), `sedimentary` (testé) ou `crystalline` (granitoïdes, roches mafiques, gneiss, micaschistes, amphibolites, quartzites, saprolite, roches de faille : **non testé, aucune photo réelle évaluée**). Le contrôle qualité, la lecture de l'étiquette et du ruban, le vote et les contrôles par le code ne dépendent pas du type de roche. Pour ajouter un domaine : copier un fichier de `domains/`, garder les identifiants `other` et `unknown`, puis **mesurer sur des photos réelles avant de s'y fier**.

## Langues
Français et anglais : bouton FR/EN en haut à droite (langue du téléphone par défaut). L'interface, les messages de contrôle de l'agent et les exports CSV/JSON suivent la langue choisie ; les règles de traduction sont dans `static/i18n.json` (une seule source pour le navigateur et le serveur). Un message d'agent sans règle reste en français : à compléter au fil des essais. Les notes dictées sont transcrites en français (`VOICE_LANG` dans `agent_core.py`).

## Test réel (avec les vrais modèles)
Une fois le serveur déployé avec ses clés : `python live_test.py https://ton-space.hf.space CODE dossier_photos` (audio du même nom = note dictée). Ce test n'a pas encore été exécuté.

## Chiffres mesurés (10 photos réelles, dernière version de l'agent)
- Coût ≈ 0,46 $ pour 10 photos, ≈ 4 min (3 en parallèle). Dictée ≈ 0,0004 $ par note. SAM ≈ 0,0025 $ par appel.
- Lithologie : 9/10 conforme à ma relecture des photos. **Ce n'est pas une vérité terrain** (aucune description de géologue de site).
- Reproductibilité de la décision entre deux exécutions identiques : 7/10 (p31, p44, p54 changent).
- SAM localise l'étiquette et le ruban 10/10. Détection de la carotte par SAM : non fiable ; isolation de la carotte testée (6/10 instable contre 2/10, aucun gain de lithologie) → désactivée (`SAM_CORE=False`).
- Argilite/schiste : ambiguïté réelle (p05 bascule d'une exécution à l'autre) ; toujours « à confirmer ».

## Limites connues
- Fractures : estimation grossière (p06 lu entre 5 et 14) ; signalée quand les lectures divergent, jamais présentée comme un comptage.
- Ruban souvent incomplet sur la photo : longueur visible parfois inconnue.
- Étiquette parfois lue de façon ambiguë (p35 : fin lue 21,35 ou 21,95 pour 20,95 attendu) : l'incohérence est signalée, pas corrigée.
- Pas de mode hors ligne : l'analyse se fait sur le serveur (seule la coque de l'application est en cache).
- Une analyse à la fois (file d'attente) ; stockage SQLite : sur un Space gratuit le disque est éphémère → exporter après chaque sondage.
- Glimmer (second avis indépendant) : pas encore hébergé ; sans lui, le second avis est le même modèle relu (auto-cohérence).
- Interface testée dans un navigateur de bureau, caméra simulée ; **pas testée sur un vrai téléphone**, ni les appels réels aux modèles depuis le serveur.
- Photo max 25 Mo ; pas de comptes (un seul code d'accès partagé).
