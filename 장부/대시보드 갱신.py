# 대시보드 갱신 — 장부·위키·폴더를 읽어 대시보드.html 의 데이터 칸만 다시 쓴다.
# AI가 작업을 마칠 때 실행한다:  python "장부/대시보드 갱신.py"
# 표준 라이브러리만 쓴다. 폴더 밖은 읽거나 쓰지 않는다.
import csv, json, os, re, sys, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_TOP = {"AGENTS.md", "CLAUDE.md", "README.md", "대시보드.html", "진행기록.md"}


def p(*parts):
    return os.path.join(ROOT, *parts)


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def read_csv(name):
    path = p("장부", name)
    if not os.path.exists(path):
        return [], []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return [], []
    head = [h.strip() for h in rows[0]]
    out = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        out.append({head[i]: (r[i].strip() if i < len(r) else "") for i in range(len(head))})
    return head, out


def walk_files():
    files = []
    for d, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for n in sorted(names):
            if n.startswith(".") or n.startswith("_이 폴더는") or n.endswith(".py") or n == "현황.json":
                continue
            full = os.path.join(d, n)
            r = rel(full)
            if "/" not in r and n in SKIP_TOP:
                continue
            parts = r.split("/")
            # 자료 탭의 묶음: 바이어/나라/회사 · 시장/나라 · 보관/종류 · 그 밖은 맨 위 폴더
            depth = 3 if parts[0] == "바이어" else 2 if parts[0] in ("시장", "보관") else 1
            folder = "/".join(parts[:min(depth, len(parts) - 1)]) or "기타"
            files.append({"name": n, "folder": folder, "path": r})
    return files


def parse_report(path, idx):
    text = open(path, encoding="utf-8").read()
    meta = {"종류": "", "나라": "", "날짜": "", "신뢰도": "", "요약": ""}
    title = os.path.splitext(os.path.basename(path))[0]
    for line in text.splitlines()[:15]:
        m = re.match(r"^#\s+(.+)", line)
        if m and title == os.path.splitext(os.path.basename(path))[0]:
            title = m.group(1).strip()
        m = re.match(r"^-\s*(종류|나라|날짜|신뢰도|요약)\s*:\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    r = rel(path)
    parts = r.split("/")
    country = meta["나라"] or (parts[-2] if len(parts) >= 3 and not parts[-2].startswith("_") else "여러 나라")
    sources = []
    m = re.search(r"^##\s*출처\s*$(.*?)(?=^##\s|\Z)", text, re.S | re.M)
    if m:
        sources = [re.sub(r"^\s*[-*]\s*", "", s).strip() for s in m.group(1).splitlines() if s.strip().startswith(("-", "*"))]
    return {
        "id": "r%d" % idx, "country": country, "kind": meta["종류"] or "조사",
        "title": title, "date": meta["날짜"] or "", "conf": meta["신뢰도"],
        "summary": [s.strip() for s in meta["요약"].split("/") if s.strip()][:3],
        "sources": sources, "path": r, "md": text, "archived": parts[0] == "보관",
    }


def reports():
    out = []
    for top in ("시장", os.path.join("보관", "시장")):
        base = p(top)
        if not os.path.isdir(base):
            continue
        for d, dirs, names in os.walk(base):
            dirs.sort()
            for n in sorted(names):
                if n.endswith(".md") and not n.startswith("_"):
                    out.append(parse_report(os.path.join(d, n), len(out) + 1))
    return out


def wiki():
    # 우리 회사 위키 — 회사/ 바로 아래 .md 페이지
    base = p("회사")
    if not os.path.isdir(base):
        return []
    out = []
    for n in sorted(os.listdir(base)):
        full = os.path.join(base, n)
        if n.endswith(".md") and not n.startswith(("_", ".")) and os.path.isfile(full):
            out.append({"title": os.path.splitext(n)[0], "path": rel(full), "md": open(full, encoding="utf-8").read()})
    return out


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    status = {}
    sp = p("장부", "현황.json")
    if os.path.exists(sp):
        status = json.load(open(sp, encoding="utf-8-sig"))
    fields, buyers = read_csv("바이어.csv")
    _, log = read_csv("연락기록.csv")
    files = walk_files()
    for b in buyers:
        folder = b.get("폴더") or "/".join(["바이어", b.get("나라", ""), b.get("회사명", "")])
        b["files"] = [{"name": f["name"], "path": f["path"]} for f in files if f["path"].startswith(folder.rstrip("/") + "/")]
    log.sort(key=lambda r: r.get("날짜", ""), reverse=True)
    data = {
        "company": status.get("company", ""), "product": status.get("product", ""),
        "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "stage": status.get("stage", 0),
        "mail": bool(status.get("mail", False)),
        "now": status.get("now", [])[:3], "checks": status.get("checks", []),
        "fields": fields, "buyers": buyers, "log": log[:100],
        "reports": reports(), "wiki": wiki(), "files": files, "root": ROOT,
    }
    hp = p("대시보드.html")
    html = open(hp, encoding="utf-8").read()
    body = json.dumps(data, ensure_ascii=False, indent=1).replace("</", "<\\/")
    start = html.rfind('<script id="db" type="application/json">')
    end = html.find("</script>", start)
    if start < 0 or end < 0:
        sys.exit("대시보드.html 에서 데이터 칸을 찾지 못했습니다")
    start += len('<script id="db" type="application/json">')
    with open(hp, "w", encoding="utf-8") as f:
        f.write(html[:start] + "\n" + body + "\n" + html[end:])
    print("대시보드 갱신: 바이어 %d · 기록 %d · 보고서 %d · 위키 %d · 파일 %d" % (len(buyers), len(log), len(data["reports"]), len(data["wiki"]), len(files)))


if __name__ == "__main__":
    main()
