"""Compare an app export (JSON list of rows with 'run' and 'lithologies') with reference/ph016b_reference.json.
Usage: python evaluate.py rows.json"""
import json, sys
ref = {r["run"]: r for r in json.load(open("reference/ph016b_reference.json"))["runs"]}
fam = {l["id"]: l["famille"] for l in json.load(open("domains/sedimentary.json"))["lithologies"]}
rows = json.load(open(sys.argv[1]))
ex = fm = n = 0
for row in rows:
    r = ref.get(row.get("run"))
    if not r: continue
    n += 1; a = set(row["lithologies"]); b = set(r["lithology"])
    e = a == b or bool(a & b); f = {fam.get(x) for x in a} & {fam.get(x) for x in b}
    ex += e; fm += bool(f)
    print(f"Run {row['run']:>2}: app={sorted(a)} ref={sorted(b)} exact={'Y' if e else 'N'} family={'Y' if f else 'N'}")
print(f"exact overlap {ex}/{n}, family overlap {fm}/{n} (reference = geologist reading of photos, not field-verified)")
