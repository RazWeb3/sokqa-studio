# PM初級パック 3点の機械検証（Sokqa Import JSON Profile v1 / Vol.07 §44）
import json, sys, collections

base = sys.argv[1]
errors = []

doc = json.load(open(f"{base}/doc_pm_zeroichi_v1.json", encoding="utf-8"))
quiz = json.load(open(f"{base}/quiz_pm_zeroichi_v1.json", encoding="utf-8"))
mani = json.load(open(f"{base}/pack_pm_zeroichi_manifest_01.json", encoding="utf-8"))

# --- doc ---
if doc.get("type") != "document": errors.append("doc.type")
if doc.get("schemaVersion") != 1: errors.append("doc.schemaVersion")
secs = doc["documents"]
if len(secs) != 44: errors.append(f"doc section count={len(secs)}")
ids = [s["id"] for s in secs]
if len(set(ids)) != len(ids): errors.append("doc id 重複")
for s in secs:
    t = s["text"]
    if not (60 <= len(t) <= 200): errors.append(f"doc text長 {len(t)}: {s['id']}")
    if "\n" in t: errors.append(f"doc 改行内包: {s['id']}")

# --- quiz ---
if quiz.get("type") != "quiz": errors.append("quiz.type")
if quiz.get("schemaVersion") != 1: errors.append("quiz.schemaVersion")
qs = quiz["questions"]
if len(qs) != 30: errors.append(f"quiz count={len(qs)}")
qids = [q["id"] for q in qs]
if len(set(qids)) != len(qids): errors.append("quiz id 重複")
dist = collections.Counter()
runs = 0; prev = None; runlen = 0
for q in qs:
    if len(q["choices"]) != 4: errors.append(f"choices!=4: {q['id']}")
    ai = q.get("answerIndex")
    if not isinstance(ai, int) or not (0 <= ai <= 3): errors.append(f"answerIndex 不正: {q['id']}")
    else:
        dist[ai] += 1
        runlen = runlen + 1 if ai == prev else 1
        prev = ai
        if runlen >= 3: errors.append(f"answerIndex 3連続: {q['id']}")
    if len(set(q["choices"])) != 4: errors.append(f"choices 重複: {q['id']}")
    if not q.get("explanation"): errors.append(f"explanation 欠落: {q['id']}")

# --- manifest ---
if mani.get("type") != "pack_manifest": errors.append("mani.type")
if mani.get("schemaVersion") != 1: errors.append("mani.schemaVersion")
if not (1 <= len(mani["items"]) <= 200): errors.append("mani items数")
for it in mani["items"]:
    if it["kind"] not in ("quiz", "document"): errors.append(f"kind 不正: {it}")
    if not it["url"].startswith("https://"): errors.append(f"url 絶対HTTPsでない: {it['url']}")

print(f"doc sections: {len(secs)}  quiz: {len(qs)}  answerIndex dist: {dict(sorted(dist.items()))}")
print(f"doc text長 min/max: {min(len(s['text']) for s in secs)}/{max(len(s['text']) for s in secs)}")
if errors:
    print("NG:"); [print(" -", e) for e in errors]; sys.exit(1)
print("OK: 全チェック通過")
