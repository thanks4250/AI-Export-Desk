# 대시보드 갱신 — 장부·위키·폴더를 읽어 대시보드.html 의 데이터 칸만 다시 쓴다.
# AI가 작업을 마칠 때 실행한다:  python "장부/대시보드 갱신.py"
# 엑셀 사본 내보내기:              python "장부/대시보드 갱신.py" --xlsx
# Python 실행 불가 시 수동 JSON 규칙: reports.summary/sources 는 배열 [], wiki.title 은 "00 우리 회사",
# 문서 본문 키는 "md", JSON 문자열 안의 </ 는 <\/, <!-- 는 \u003c!-- 로 이스케이프한다.
# 표준 라이브러리만 쓴다. 폴더 밖은 읽거나 쓰지 않는다.
import csv, datetime, io, json, os, re, sys, unicodedata, zipfile
from xml.sax.saxutils import escape as xesc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_TOP = {"AGENTS.md", "CLAUDE.md", "README.md", "대시보드.html", "진행기록.md"}
TEXT_MAX = 100_000


def nfc(s):
    return unicodedata.normalize("NFC", str(s or ""))


def now_local():
    dt = datetime.datetime.now().astimezone()
    if dt.utcoffset() == datetime.timedelta(0) and os.environ.get("TZ", "") in ("", "UTC", "Etc/UTC"):
        return dt.astimezone(datetime.timezone(datetime.timedelta(hours=9)))
    return dt


def date_key(s):
    m = re.match(r"^\s*(\d{2}|\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})", str(s or ""))
    if not m:
        return str(s or "").strip()
    y = int("20" + m.group(1)) if len(m.group(1)) == 2 else int(m.group(1))
    try:
        return datetime.date(y, int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return ""


def p(*parts):
    cur = ROOT
    for part in parts:
        cand = os.path.join(cur, part)
        if os.path.exists(cand) or not os.path.isdir(cur):
            cur = cand
            continue
        want = nfc(part)
        hit = next((e for e in os.listdir(cur) if nfc(e) == want), part)
        cur = os.path.join(cur, hit)
    return cur


def rel(path):
    return os.path.relpath(path, ROOT).replace(os.sep, "/")


def norm_p(s):
    segs = []
    for seg in nfc(s).replace("\\", "/").strip("/").split("/"):
        seg = re.sub(r'[:*?"<>|]', "", seg).rstrip(". ").strip()
        if seg:
            segs.append(seg.lower())
    return "/".join(segs)


def decode_bytes(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            txt, ok = raw.decode("utf-16"), True
        except UnicodeDecodeError:
            txt, ok = raw.decode("utf-16", errors="replace"), False
    else:
        for enc in ("utf-8-sig", "cp949"):
            try:
                txt, ok = raw.decode(enc), True
                break
            except UnicodeDecodeError:
                continue
        else:
            txt, ok = raw.decode("utf-8", errors="replace"), False
    return nfc(txt.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")), ok


def read_text(path, limit=None):
    with open(path, "rb") as f:
        raw = f.read() if limit is None else f.read(limit)
    return decode_bytes(raw)[0]


def read_csv(name):
    path = p("장부", name)
    if not os.path.exists(path):
        return [], []
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except PermissionError:
        sys.exit("열려 있는 엑셀 창(%s)을 닫은 뒤 다시 시도해 주세요." % name)
    text, lossless = decode_bytes(raw)
    first = text.splitlines()[0] if text.splitlines() else ""
    delim = "\t" if ("," not in first and "\t" in first) else ","
    if lossless and not raw.startswith(b"\xef\xbb\xbf") and delim == ",":
        try:
            with open(path, "wb") as wf:
                wf.write(b"\xef\xbb\xbf" + text.encode("utf-8"))
        except OSError:
            pass
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        return [], []
    head = []
    for h_raw in rows[0]:
        base = h_raw.strip() or ("열%d" % (len(head) + 1))
        cur, idx = base, 2
        while cur in head:
            cur = "%s(%d)" % (base, idx)
            idx += 1
        head.append(cur)
    out = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        if len(r) > len(head) and head:
            r = r[:len(head) - 1] + [", ".join(c.strip() for c in r[len(head) - 1:] if c.strip())]
        out.append({head[i]: (r[i].strip() if i < len(r) else "") for i in range(len(head))})
    return head, out


def walk_files(skip_md_paths):
    files = []
    for d, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(x for x in dirs if not nfc(x).startswith((".", "__")))
        for raw_n in sorted(names, key=nfc):
            n = nfc(raw_n)
            if n.startswith(".") or n.startswith("_이 폴더는") or n.endswith((".py", ".pyc")) or n == "현황.json":
                continue
            full = os.path.join(d, raw_n)
            raw_r = rel(full)
            r = nfc(raw_r)
            if "/" not in r and n in SKIP_TOP:
                continue
            parts = r.split("/")
            top2 = tuple(parts[:2])
            depth = 4 if top2 == ("보관", "바이어") else 3 if (parts[0] == "바이어" or top2 == ("보관", "시장")) else 2 if parts[0] in ("시장", "보관", "회사") else 1
            folder = "/".join(parts[:min(depth, len(parts) - 1)]) or "기타"
            item = {"name": n, "folder": folder, "path": r}
            if raw_r != r:
                item["raw"] = raw_r
            if n.lower().endswith((".md", ".txt")) and r not in skip_md_paths:
                try:
                    txt = read_text(full, TEXT_MAX * 4 + 64)
                    if len(txt) > TEXT_MAX:
                        txt = txt[:TEXT_MAX] + "\n\n…(문서가 길어 앞부분만 표시합니다. 원본 파일을 열어 확인하세요)"
                    item["md"] = txt
                except OSError:
                    pass
            files.append(item)
    return files


def parse_report(path, idx):
    text = read_text(path)
    meta = {"종류": "", "나라": "", "날짜": "", "신뢰도": "", "요약": ""}
    base_title = nfc(os.path.splitext(os.path.basename(path))[0])
    title = base_title
    for line in text.splitlines()[:20]:
        m = re.match(r"^#\s+(.+)", line)
        if m and title == base_title:
            title = m.group(1).strip()
        m = re.match(r"^-\s*\*{0,2}(종류|나라|날짜|신뢰도|요약)\*{0,2}\s*:\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    raw_r = rel(path)
    r = nfc(raw_r)
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
    raw_sum = meta["요약"]
    sums = re.split(r"\s+/\s+|\s+/(?=\S)|(?<=\S)/\s+", raw_sum) if raw_sum else []
    item = {
        "id": "r%d" % idx, "country": country, "kind": meta["종류"] or "조사",
        "title": title, "date": date_key(meta["날짜"]) or meta["날짜"], "conf": meta["신뢰도"],
        "summary": [s.strip() for s in sums if s.strip()][:3],
        "sources": sources, "path": r, "md": text, "archived": parts[0] == "보관",
    }
    if raw_r != r:
        item["raw"] = raw_r
    return item


def reports():
    out = []
    for top in (("시장",), ("보관", "시장")):
        base = p(*top)
        if not os.path.isdir(base):
            continue
        for d, dirs, names in os.walk(base):
            dirs[:] = sorted(x for x in dirs if not nfc(x).startswith((".", "__")))
            for raw_n in sorted(names, key=nfc):
                n = nfc(raw_n)
                if n.endswith(".md") and not n.startswith("_"):
                    out.append(parse_report(os.path.join(d, raw_n), len(out) + 1))
    return out


def wiki():
    base = p("회사")
    if not os.path.isdir(base):
        return []
    out = []
    for raw_n in sorted(os.listdir(base), key=nfc):
        n = nfc(raw_n)
        full = os.path.join(base, raw_n)
        if n.endswith(".md") and not n.startswith(("_", ".")) and os.path.isfile(full):
            raw_r = rel(full)
            r = nfc(raw_r)
            item = {"title": os.path.splitext(n)[0], "path": r, "md": read_text(full)}
            if raw_r != r:
                item["raw"] = raw_r
            out.append(item)
    return out


def export_xlsx():
    b_head, b_rows = read_csv("바이어.csv")
    l_head, l_rows = read_csv("연락기록.csv")

    def col_name(idx):
        s, n = "", idx + 1
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    def clean_cell(val):
        v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]", "", str(val or ""))
        v = v.replace("\r\n", "\n").replace("\r", "\n")
        if len(v.encode("utf-16-le")) > 65500:
            while len(v.encode("utf-16-le")) > 65400:
                v = v[:-200]
            v += " …(생략)"
        return v

    def disp_w(s):
        return max((sum(2 if ord(ch) > 127 else 1 for ch in ln) for ln in s.split("\n")), default=0)

    def sheet_xml(head, rows):
        all_rows = [head] + [[r.get(h, "") for h in head] for r in rows] if head else []
        cleaned = [[clean_cell(v) for v in row] for row in all_rows]
        cols_xml = ""
        if head:
            cols_xml = "<cols>" + "".join(
                '<col min="%d" max="%d" width="%d" customWidth="1"/>' % (
                    c + 1, c + 1, min(max([disp_w(cleaned[r][c]) + (3 if r == 0 else 2) for r in range(len(cleaned))] + [10]), 48)
                ) for c in range(len(head))
            ) + "</cols>"
        lines = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">', cols_xml, '<sheetData>']
        for r_idx, row in enumerate(cleaned, 1):
            style = ' s="1"' if r_idx == 1 else ""
            cells = ['<c r="%s%d"%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (col_name(c_idx), r_idx, style, xesc(v)) for c_idx, v in enumerate(row)]
            lines.append('<row r="%d">%s</row>' % (r_idx, "".join(cells)))
        lines.append("</sheetData></worksheet>")
        return "".join(lines)

    stamp = now_local().strftime("%y%m%d")
    out_name = "%s 장부 내보내기 (사본).xlsx" % stamp

    def write_zip(target):
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
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
                       '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                       '</styleSheet>')
            z.writestr("xl/worksheets/sheet1.xml", sheet_xml(b_head, b_rows))
            z.writestr("xl/worksheets/sheet2.xml", sheet_xml(l_head, l_rows))

    for seq in range(1, 20):
        cand_name = out_name if seq == 1 else ("%s 장부 내보내기 (사본 %d).xlsx" % (stamp, seq))
        try:
            write_zip(p("장부", cand_name))
            out_name = cand_name
            break
        except PermissionError:
            continue
    else:
        sys.exit("열려 있는 엑셀 내보내기 창을 닫은 뒤 다시 시도해 주세요.")
    print("엑셀 내보내기 완료: 장부/%s (바이어 %d · 기록 %d)" % (out_name, len(b_rows), len(l_rows)))


def derive_home(status, buyers, rep_list, log):
    try:
        manual_stage = max(0, min(4, int(status.get("stage", 0))))
    except (TypeError, ValueError):
        manual_stage = 0
    live = [b for b in buyers if b.get("단계", "").strip() != "보관"]
    stages = {re.sub(r"\s+", "", b.get("단계", "")) for b in live}
    auto_stage = (
        4 if "계약" in stages else
        3 if stages & {"연락함", "답장옴", "샘플", "협상"} or any(l.get("종류") in ("메일 보냄", "샘플", "견적") for l in log) else
        2 if live else
        1 if any(not r["archived"] for r in rep_list) else 0
    )
    raw_now = status.get("now")
    now_list = [
        {"title": nfc(x.get("title")), "say": nfc(x.get("say")), "due": nfc(x.get("due"))}
        for x in (raw_now if isinstance(raw_now, list) else [])
        if isinstance(x, dict) and (x.get("title") or x.get("say"))
    ][:3]
    # 현황.json 의 now 빈자리에 장부의 마감 임박·진행 할 일을 자동 보완
    if len(now_list) < 3 and live:
        horizon = (now_local().date() + datetime.timedelta(days=14)).isoformat()
        used = " ".join(x["title"] + " " + x["say"] for x in now_list).lower()
        cands = []
        for b in live:
            co, todo = b.get("회사명", "").strip(), b.get("다음 할 일", "").strip()
            if not co or not todo or todo in ("-", "—", "확인필요") or co.lower() in used:
                continue
            iso = date_key(b.get("다음 할 일 날짜", ""))
            valid_iso = iso if re.match(r"^\d{4}-\d{2}-\d{2}$", iso) else ""
            if valid_iso and valid_iso <= horizon:
                cands.append((0, valid_iso, co, todo, valid_iso))
            elif not now_list:
                cands.append((1, valid_iso or "9999-99-99", co, todo, valid_iso))
        cands.sort(key=lambda t: (t[0], t[1]))
        for _, _, co, todo, due_str in cands:
            if len(now_list) >= 3:
                break
            is_mail = any(k in todo for k in ("초안", "답장", "회신")) or bool(re.search(r"(?<!이)메일", todo))
            say_txt = ("%s 팔로업 메일 써 줘" % co) if "팔로업" in todo else ("%s에 메일 써 줘: %s" % (co, todo)) if is_mail else ("%s %s 도와줘" % (co, todo))
            now_list.append({"title": "%s — %s" % (co, todo), "say": say_txt, "due": due_str})
    return max(manual_stage, auto_stage), now_list


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:
        pass
    if "--xlsx" in sys.argv:
        export_xlsx()
        return
    clean = "--clean" in sys.argv
    status = {}
    sp = p("장부", "현황.json")
    if os.path.exists(sp):
        try:
            loaded = json.loads(read_text(sp))
            if isinstance(loaded, dict):
                status = loaded
        except Exception as e:
            sys.stderr.write("경고: 현황.json 형식을 읽지 못해 기본값으로 진행합니다 (%s)\n" % e)
    fields, buyers = read_csv("바이어.csv")
    _, log = read_csv("연락기록.csv")
    rep_list = reports()
    wiki_list = wiki()
    files = walk_files({r["path"] for r in rep_list} | {w["path"] for w in wiki_list})
    for b in buyers:
        co, na = b.get("회사명", "").strip(), b.get("나라", "").strip()
        cands = {norm_p(b["폴더"])} if b.get("폴더") else set()
        if co:
            if na:
                cands.update((norm_p("/".join(["바이어", na, co])), norm_p("/".join(["보관", "바이어", na, co]))))
            cands.add(norm_p("/".join(["보관", "바이어", co])))
        cands -= {"", "바이어", "보관", "보관/바이어"}
        b["files"] = [
            {k: f[k] for k in ("name", "path", "raw") if k in f}
            for f in files
            if any(norm_p(f["path"]).startswith(c + "/") for c in cands)
        ]
    log.sort(key=lambda r: date_key(r.get("날짜", "")), reverse=True)
    stage, now_list = (0, []) if clean else derive_home(status, buyers, rep_list, log)
    raw_checks = status.get("checks")
    checks_list = [nfc(x) for x in raw_checks if str(x or "").strip()] if isinstance(raw_checks, list) else ([nfc(raw_checks).strip()] if isinstance(raw_checks, str) and raw_checks.strip() else [])
    company = "" if clean else nfc(status.get("company") or "")
    product = "" if clean else nfc(status.get("product") or "")
    if (not company or not product) and not clean:
        w0 = next((w for w in wiki_list if w["title"].startswith("00")), None)
        if w0:
            if not company:
                m_co = re.search(r"회사명[^:\n]*:\s*([^\n(]+)", w0["md"])
                if m_co:
                    company = m_co.group(1).strip()
            if not product:
                m_pr = re.search(r"(?:수출\s*)?주력\s*제품[^:\n]*:\s*([^\n(]+)", w0["md"])
                if m_pr:
                    product = m_pr.group(1).strip()
    data = {
        "company": company, "product": product,
        "updated": "" if clean else now_local().strftime("%Y-%m-%d %H:%M"), "stage": stage,
        "mail": False if clean else bool(status.get("mail", False)),
        "now": now_list, "checks": [] if clean else checks_list,
        "fields": fields, "buyers": buyers, "log": log,
        "reports": rep_list, "wiki": wiki_list, "files": files, "root": "" if clean else nfc(ROOT),
    }
    hp = p("대시보드.html")
    html = read_text(hp)
    body = (
        json.dumps(data, ensure_ascii=False, indent=1)
        .replace("</", "<\\/")
        .replace("<!--", "\\u003c!--")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    m_tags = list(re.finditer(r'^[ \t]*<script id="db" type="application/json">', html, re.M))
    if not m_tags:
        sys.exit("대시보드.html 에서 데이터 칸을 찾지 못했습니다")
    start = m_tags[-1].end()
    end = html.find("</script>", start)
    if end < 0:
        sys.exit("대시보드.html 에서 데이터 닫는 태그를 찾지 못했습니다")
    try:
        with open(hp, "w", encoding="utf-8", newline="\n") as f:
            f.write(html[:start] + "\n" + body + "\n" + html[end:])
    except PermissionError:
        sys.exit("대시보드.html 파일이 다른 프로그램에 잠겨 있습니다. 닫은 뒤 다시 시도해 주세요.")
    print("대시보드 갱신: 바이어 %d · 기록 %d · 보고서 %d · 위키 %d · 파일 %d" % (len(buyers), len(log), len(rep_list), len(wiki_list), len(files)))


if __name__ == "__main__":
    main()
