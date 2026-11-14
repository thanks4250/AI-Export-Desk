# 대시보드 갱신 — 장부·위키·폴더를 읽어 대시보드.html 의 데이터 칸만 다시 쓴다.
# AI가 작업을 마칠 때 실행한다:  python "장부/대시보드 갱신.py"
# 엑셀 사본 내보내기:              python "장부/대시보드 갱신.py" --xlsx
# 표준 라이브러리만 쓴다. 폴더 밖은 읽거나 쓰지 않는다.
import csv, datetime, io, json, os, re, sys, zipfile
from xml.sax.saxutils import escape as xesc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_TOP = {"AGENTS.md", "CLAUDE.md", "README.md", "대시보드.html", "진행기록.md"}


def p(*parts):
    return os.path.join(ROOT, *parts)


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def norm_p(s):
    return "/".join(seg.rstrip(". ") for seg in s.replace("\\", "/").strip("/").split("/") if seg)


def read_csv(name):
    path = p("장부", name)
    if not os.path.exists(path):
        return [], []
    raw = open(path, "rb").read()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949", errors="replace")
        has_bom = False
    # 엑셀 한글 깨짐 방지를 위해 UTF-8 BOM 이 빠졌거나 CP949 였으면 BOM 을 붙여 복구
    if not has_bom:
        try:
            with open(path, "wb") as wf:
                wf.write(b"\xef\xbb\xbf" + text.encode("utf-8"))
        except OSError:
            pass
    rows = list(csv.reader(io.StringIO(text)))
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
        dirs[:] = sorted(x for x in dirs if not x.startswith((".", "__")))
        for n in sorted(names):
            if n.startswith(".") or n.startswith("_이 폴더는") or n.endswith((".py", ".pyc")) or n == "현황.json":
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
    text = open(path, encoding="utf-8-sig").read()
    meta = {"종류": "", "나라": "", "날짜": "", "신뢰도": "", "요약": ""}
    title = os.path.splitext(os.path.basename(path))[0]
    for line in text.splitlines()[:20]:
        m = re.match(r"^#\s+(.+)", line)
        if m and title == os.path.splitext(os.path.basename(path))[0]:
            title = m.group(1).strip()
        m = re.match(r"^-\s*\*{0,2}(종류|나라|날짜|신뢰도|요약)\*{0,2}\s*:\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    r = rel(path)
    parts = r.split("/")
    parent = parts[-2] if len(parts) >= 3 else ""
    country = meta["나라"] or (parent if parent and not parent.startswith("_") and parent not in ("시장", "보관") else "여러 나라")
    sources = []
    m = re.search(r"^#{2,3}\s*(?:\d+[.)]\s*)?출처[^\n]*$(.*?)(?=^#{1,3}\s|\Z)", text, re.S | re.M)
    if m:
        sources = [
            re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", s).strip()
            for s in m.group(1).splitlines()
            if re.match(r"^\s*(?:[-*]|\d+[.)])\s+\S", s)
        ]
    # 단어 안의 슬래시(김밥/소금빵, $3.50/팩)는 유지하고 공백이 붙은 구분 슬래시만 분할
    raw_sum = meta["요약"]
    sums = re.split(r"\s+/\s+|\s+/(?=\S)|(?<=\S)/\s+", raw_sum) if raw_sum else []
    return {
        "id": "r%d" % idx, "country": country, "kind": meta["종류"] or "조사",
        "title": title, "date": meta["날짜"] or "", "conf": meta["신뢰도"],
        "summary": [s.strip() for s in sums if s.strip()][:3],
        "sources": sources, "path": r, "md": text, "archived": parts[0] == "보관",
    }


def reports():
    out = []
    for top in ("시장", os.path.join("보관", "시장")):
        base = p(top)
        if not os.path.isdir(base):
            continue
        for d, dirs, names in os.walk(base):
            dirs[:] = sorted(x for x in dirs if not x.startswith((".", "__")))
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
            out.append({"title": os.path.splitext(n)[0], "path": rel(full), "md": open(full, encoding="utf-8-sig").read()})
    return out


def export_xlsx():
    # 외부 패키지(openpyxl 등) 없이 표준 라이브러리만으로 바이어·연락기록 2시트 .xlsx 생성
    b_head, b_rows = read_csv("바이어.csv")
    l_head, l_rows = read_csv("연락기록.csv")

    def col_name(idx):
        s = ""
        n = idx + 1
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    def sheet_xml(head, rows):
        all_rows = [head] + [[r.get(h, "") for h in head] for r in rows] if head else []
        lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
        for r_idx, row in enumerate(all_rows, 1):
            cells = []
            for c_idx, val in enumerate(row):
                v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(val or ""))
                ref = "%s%d" % (col_name(c_idx), r_idx)
                style = ' s="1"' if r_idx == 1 else ""
                cells.append('<c r="%s"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (ref, style, xesc(v)))
            lines.append('<row r="%d">%s</row>' % (r_idx, "".join(cells)))
        lines.append("</sheetData></worksheet>")
        return "".join(lines)

    out_name = "%s 장부 내보내기 (사본).xlsx" % datetime.datetime.now().strftime("%y%m%d")
    out_path = p("장부", out_name)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '</Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="바이어" sheetId="1" r:id="rId1"/><sheet name="연락기록" sheetId="2" r:id="rId2"/></sheets>'
                   '</workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
                   '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   '</Relationships>')
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<fonts count="2"><font><sz val="11"/><name val="Malgun Gothic"/></font><font><b/><sz val="11"/><name val="Malgun Gothic"/></font></fonts>'
                   '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                   '<fill><patternFill patternType="solid"><fgColor rgb="FFEFF2F5"/></patternFill></fill></fills>'
                   '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                   '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                   '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
                   '</styleSheet>')
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml(b_head, b_rows))
        z.writestr("xl/worksheets/sheet2.xml", sheet_xml(l_head, l_rows))
    print("엑셀 내보내기 완료: 장부/%s (바이어 %d · 기록 %d)" % (out_name, len(b_rows), len(l_rows)))


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    if "--xlsx" in sys.argv:
        export_xlsx()
        return
    clean = "--clean" in sys.argv
    status = {}
    sp = p("장부", "현황.json")
    if os.path.exists(sp):
        status = json.load(open(sp, encoding="utf-8-sig"))
    fields, buyers = read_csv("바이어.csv")
    _, log = read_csv("연락기록.csv")
    files = walk_files()
    for b in buyers:
        co = b.get("회사명", "").strip()
        na = b.get("나라", "").strip()
        cands = set()
        if b.get("폴더"):
            cands.add(norm_p(b["폴더"]))
        if co:
            if na:
                cands.add(norm_p("/".join(["바이어", na, co])))
                cands.add(norm_p("/".join(["보관", "바이어", na, co])))
            cands.add(norm_p("/".join(["보관", "바이어", co])))
        cands -= {"", "바이어", "보관", "보관/바이어"}
        b["files"] = [
            {"name": f["name"], "path": f["path"]}
            for f in files
            if any(norm_p(f["path"]).startswith(c + "/") for c in cands)
        ]
    log.sort(key=lambda r: r.get("날짜", ""), reverse=True)
    data = {
        "company": status.get("company", ""), "product": status.get("product", ""),
        "updated": "" if clean else datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "stage": status.get("stage", 0),
        "mail": bool(status.get("mail", False)),
        "now": status.get("now", [])[:3], "checks": status.get("checks", []),
        "fields": fields, "buyers": buyers, "log": log[:100],
        "reports": reports(), "wiki": wiki(), "files": files, "root": "" if clean else ROOT,
    }
    hp = p("대시보드.html")
    html = open(hp, encoding="utf-8-sig").read()
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
