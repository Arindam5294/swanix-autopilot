"""Long exam-prep jobs that span several runs: /book and /mock.
Each call to step(job, ping) does ONE unit of work (one AI call or the final render) and updates job["data"]
in place; run.py saves the job file after every step, so a run that ends (time budget, crash, 6-hour limit)
resumes exactly where it stopped on the next scheduled run."""
import datetime, html, json, pathlib, re, time
import llm, mcq, render

HERE = pathlib.Path(__file__).resolve().parent
CSS = (HERE / "print.css").read_text()
esc = mcq.esc


def today():
    return datetime.date.today().strftime("%d %b %Y")


def slug(s):
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:40] or "studio"


def theme_css(th):
    th = th or {}
    v = []
    for k, var in (("accent", "--accent"), ("accent2", "--accent2")):
        if re.fullmatch(r"#[0-9a-fA-F]{6}", str(th.get(k, ""))):
            v.append(f"{var}: {th[k]};")
    out = f":root {{ {' '.join(v)} }}" if v else ""
    if th.get("dark"):
        out += " @page { background: #12161d; } html { background: #12161d; }"
    return out


def page_html(title, body, theme):
    cls = ' class="dark"' if (theme or {}).get("dark") else ""
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>{esc(title)}</title>'
            f"<style>{CSS}\n{theme_css(theme)}</style></head><body{cls}>{body}</body></html>")


def two_pass(title, build, out):
    """build(pages) -> html. pages=None on pass 1 (placeholders). Pass 1 finds which PDF page each marker /
    index term lands on; pass 2 prints the real page numbers in the contents and index."""
    p1 = render.pdf(page_html(title, build(None), build.theme), out, safety=False)
    texts = [re.sub(r"\s+", " ", t) for t in render.page_texts(p1)]
    pages = {"markers": {}, "texts": texts}
    for i, t in enumerate(texts):
        for m in re.findall(r"@@(\w+)@@", t.replace(" ", "")):
            pages["markers"].setdefault(m, i + 1)
    return render.pdf(page_html(title, build(pages), build.theme), out, safety=False)


def find_pages(term, texts, lo, hi, limit=3):
    t = re.sub(r"\s+", " ", term.strip().lower())
    if len(t) < 3:
        return []
    found = []
    for i in range(max(0, lo - 1), min(len(texts), hi)):
        if t in texts[i].lower():
            found.append(i + 1)
            if len(found) >= limit:
                break
    return found


def send_pdf(path):
    parts = render.split(path)
    return [("document", p, f"Part {i+1}/{len(parts)}" if len(parts) > 1 else "") for i, p in enumerate(parts)]


# =============================== BOOK ===============================
NOTES_SYSTEM = """You are an expert exam coach and textbook author. You write ONE chapter of a study book as an HTML
fragment (no <html>, <head>, <body>, <style> or <script>). Use only these building blocks:
<h2>, <h3>, <h4>, <p>, <ul>/<ol>/<li>, <strong>, <em>, <table><thead><tr><th>…</th></tr></thead><tbody>…</tbody></table>,
<div class="callout tip"><span class="t">Tip</span>…</div>, <div class="callout warn"><span class="t">Common mistake</span>…</div>,
<div class="callout exam"><span class="t">Exam focus</span>…</div>,
<div class="formula"><div class="t">Key formula</div><div class="f">…</div><div>what each symbol means</div></div>,
<div class="example"><div class="t">Worked example</div>…step-by-step solution…</div>,
<ul class="keypoints">…</ul> for a quick-revision list, and simple inline <svg> diagrams (width/height set, viewBox equal).
Write dense, accurate, exam-oriented notes: definitions, concepts, numbers/standards, comparison tables, key formulas,
2-4 worked examples where the subject is numerical, mnemonics, previous-year-style focus areas. 1,800-3,000 words.
Do NOT include practice questions (they are added separately). Do not repeat the chapter title as <h1>.
End with a "Quick revision" <h2> and a <ul class="keypoints">.
After the fragment write one line: <!--KEYTERMS: term1; term2; ... (12-20 important terms that appear in the text)-->
then the line <!--END-->"""


def _outline_check(t):
    d = llm.json_obj(t)
    ch = d.get("chapters") or []
    if not d.get("title") or not ch or not all(c.get("title") for c in ch):
        raise llm.AIError("outline incomplete")
    return d


def book_new(topic):
    m = re.search(r"\b(\d{1,2})\s*chapters?\b", topic, re.I)
    return {"kind": "book", "topic": topic, "stage": "outline", "want": int(m.group(1)) if m else None,
            "chapters": [], "theme": {}, "ops": [], "edits": []}


def book_step(d, ping):
    if d["stage"] == "outline":
        want = d.get("want")
        p = (f"Plan a complete syllabus-based study book for: <<<{d['topic']}>>>.\n"
             + (f"Exactly {want} chapters.\n" if want else "8-14 chapters that cover the official syllabus in a sensible teaching order.\n")
             + 'Reply JSON only: {"title": "...", "subtitle": "...", "audience": "...", "chapters": [{"title": "...", '
               '"summary": "one sentence", "topics": ["subtopic", "..."]}]}')
        o = llm.ask(p, max_tokens=5000, check=_outline_check, temp=0.4, label="book-outline")
        if want:
            o["chapters"] = o["chapters"][:want]
        o["chapters"] = o["chapters"][:20]
        d["outline"] = {k: o.get(k, "") for k in ("title", "subtitle", "audience")}
        d["chapters"] = [{"title": c["title"], "summary": c.get("summary", ""), "topics": c.get("topics", [])}
                         for c in o["chapters"]]
        d["stage"] = "chapters"
        ping(f"Outline ready: {len(d['chapters'])} chapters. Writing chapter 1…")
        return False
    if d["stage"] == "chapters":
        n = len(d["chapters"])
        for i, c in enumerate(d["chapters"]):
            if not c.get("html"):
                c["html"], c["keyterms"] = write_chapter(d, i)
                return False
            if not c.get("mcqs"):
                c["mcqs"] = chapter_mcqs(d, i, 12)
                ping(f"Chapter {i+1}/{n} done")
                return False
        d["stage"] = "ops" if d.get("ops") else "front"
        return False
    if d["stage"] == "ops":
        return ops_step(d, ping)
    if d["stage"] == "front":
        ttl = d["outline"]["title"]
        p = (f"Write the back-cover style blurb for a study book titled \"{ttl}\" ({d['outline'].get('subtitle','')}). "
             f"Chapters: {'; '.join(c['title'] for c in d['chapters'])}. Reply JSON only: "
             '{"kicker": "2-4 word label like \'Complete study guide\'", "blurb": "60-90 words"}')
        try:
            d["front"] = llm.ask(p, max_tokens=1500, check=llm.json_obj, temp=0.6, label="book-front")
        except Exception:
            d["front"] = {"kicker": "Study guide", "blurb": d["outline"].get("subtitle", "")}
        d["stage"] = "render"
        return False
    if d["stage"] == "render":
        out = render.WORK / "out" / f"{slug(d['outline']['title'])}.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        book_render(d, out)
        d["files"] = [[k, str(p), c] for k, p, c in send_pdf(out)]
        d["stage"] = "done"
        return True
    return True


def write_chapter(d, i, instruction=None):
    c = d["chapters"][i]
    ctx = (f"Book: {d['outline']['title']} ({d['outline'].get('subtitle','')}), for {d['outline'].get('audience','exam aspirants')}.\n"
           f"Full chapter list: {'; '.join(f'{k+1}. ' + x['title'] for k, x in enumerate(d['chapters']))}\n\n"
           f"Write Chapter {i+1}: {c['title']}\nCovers: {', '.join(c.get('topics') or []) or c.get('summary','')}")
    if instruction and c.get("html"):
        ctx += (f"\n\nThis is a REVISION. Instruction: <<<{instruction}>>>\nCurrent chapter HTML:\n{c['html']}\n"
                "Return the full revised chapter fragment.")

    def chk(t):
        frag = llm.html_fragment(t)
        m = re.search(r"<!--\s*KEYTERMS:(.*?)-->", t, re.S)
        terms = [x.strip() for x in (m.group(1).split(";") if m else []) if 2 < len(x.strip()) < 60][:20]
        frag = re.sub(r"<!--\s*KEYTERMS:.*?-->", "", frag, flags=re.S)
        m1 = re.match(r"\s*<h[12][^>]*>(.*?)</h[12]>", frag, re.S)   # drop a repeated chapter title at the top
        if m1:
            head = re.sub(r"<[^>]+>", "", m1.group(1)).lower()
            if "chapter" in head or c["title"].lower()[:25] in head:
                frag = frag[m1.end():]
        return frag, terms
    return llm.ask(ctx, system=NOTES_SYSTEM, max_tokens=14000, check=chk, temp=0.5, label=f"chapter{i+1}")


def chapter_mcqs(d, i, count, extra="", avoid=()):
    c = d["chapters"][i]
    ctx = (f"Practice questions for a study book: {d['outline']['title']}.\nChapter {i+1}: {c['title']} "
           f"(topics: {', '.join(c.get('topics') or [])}). Level: real exam level.")
    return mcq.generate(ctx, c["title"], count, 4, avoid=avoid, extra=extra, chunk=15, label=f"ch{i+1}-mcq")


def book_render(d, out):
    o, chs, th = d["outline"], d["chapters"], d.get("theme") or {}

    def build(pages):
        mk = (pages or {}).get("markers", {})
        cover = (f'<section class="cover"><div class="inner"><div class="kicker">{esc((d.get("front") or {}).get("kicker","Study guide"))}</div>'
                 f'<h1>{esc(o["title"])}</h1><div class="sub">{esc(o.get("subtitle",""))}</div>'
                 f'<div class="blurb">{esc((d.get("front") or {}).get("blurb",""))}</div></div>'
                 f'<div class="meta"><span>{len(chs)} chapters · {sum(len(c.get("mcqs") or []) for c in chs)} practice MCQs</span>'
                 f'<span>{today()}</span></div></section>')
        toc = '<section class="front toc"><h2>Contents</h2><ol>' + "".join(
            f'<li><span class="n">{k+1}</span><span class="ttl">{esc(c["title"])}</span><span class="pg">{mk.get(f"CH{k+1}", "000") if pages else "000"}</span></li>'
            for k, c in enumerate(chs)) + f'<li><span class="n"></span><span class="ttl">Index</span><span class="pg">{mk.get("INDEX","000") if pages else "000"}</span></li></ol></section>'
        body = []
        for k, c in enumerate(chs):
            qs = c.get("mcqs") or []
            body.append(f'<section class="chapter"><div class="chap-open"><span class="marker">@@CH{k+1}@@</span>'
                        f'<div class="num">Chapter {k+1}</div><h1 class="chap-head">{esc(c["title"])}</h1>'
                        f'<div class="obj">{esc(c.get("summary",""))}</div></div>{c.get("html","")}'
                        + (f'<div class="qsec"><h2>Practice questions</h2>{mcq.render_questions(qs)}'
                           f'<h3>Answers and explanations</h3>{mcq.render_solutions(qs)}</div>' if qs else "")
                        + "</section>")
        # index
        terms = {}
        for k, c in enumerate(chs):
            for t in c.get("keyterms") or []:
                terms.setdefault(t.strip(), k)
        ents = []
        if pages:
            texts = pages["texts"]
            starts = [mk.get(f"CH{k+1}", 1) for k in range(len(chs))]
            end = mk.get("INDEX", len(texts) + 1) - 1
            for t, k in terms.items():
                pg = find_pages(t, texts, starts[0], end) or [starts[k]]
                ents.append((t, pg))
        else:
            ents = [(t, [0, 0]) for t in terms]
        ents.sort(key=lambda x: x[0].lower())
        idx, letter = ['<section class="index"><span class="marker">@@INDEX@@</span><h2>Index</h2>'], ""
        for t, pg in ents:
            L = t[:1].upper()
            if L != letter:
                letter = L; idx.append(f'<div class="letter">{esc(L)}</div>')
            idx.append(f'<div class="ent"><span>{esc(t)}</span><span class="pg">{", ".join(str(p) if pages else "000" for p in pg)}</span></div>')
        idx.append("</section>")
        return cover + toc + "".join(body) + "".join(idx)
    build.theme = th
    return two_pass(o["title"], build, out)


# =============================== MOCK ===============================
PATTERN_ASK = """Give the CURRENT official exam pattern for: <<<{exam}>>>.
If several stages exist (prelims/mains, tier 1/2, CBT 1/2), use the first objective (MCQ) stage unless the request names one.
Reply JSON only:
{{"exam_name": "...", "post": "...", "stage": "...", "duration_minutes": 60, "total_questions": 100, "total_marks": 200,
 "options_per_question": 4, "marks_per_question": 2, "negative_marking": "0.5 mark deducted per wrong answer",
 "sections": [{{"name": "...", "questions": 25, "marks": 50, "topics": ["...", "..."]}}],
 "notes": ["one-line facts a candidate should know (qualifying marks, language, mode)"]}}"""


def _pattern_check(t):
    d = llm.json_obj(t)
    secs = [s for s in d.get("sections") or [] if s.get("name") and int(s.get("questions") or 0) > 0]
    if not secs:
        raise llm.AIError("pattern has no sections")
    d["sections"] = secs[:10]
    return d


def mock_new(text):
    m = re.match(r"\s*(\d{1,2})\s+(.*)", text, re.S)
    n, exam = (int(m.group(1)), m.group(2).strip()) if m else (1, text.strip())
    q = re.search(r"\b(\d{1,3})\s*(?:questions|qs)\b", exam, re.I)
    return {"kind": "mock", "exam": exam, "n": max(1, min(n, 5)), "stage": "pattern", "mocks": [], "theme": {},
            "ops": [], "edits": [], "qcap": int(q.group(1)) if q else None}


def mock_targets(d):
    """Questions per section; scales down proportionally if the request capped total questions."""
    secs = d["pattern"]["sections"]
    tot = sum(int(s["questions"]) for s in secs)
    cap = d.get("qcap") or min(tot, 200)
    f = min(1.0, cap / max(1, tot))
    return [max(1, round(int(s["questions"]) * f)) for s in secs]


def mock_step(d, ping):
    if d["stage"] == "pattern":
        text, src = llm.grounded(PATTERN_ASK.format(exam=d["exam"]) +
                                 "\nUse Google Search to find the latest official notification/pattern.")
        pat = None
        if text:
            try:
                pat = _pattern_check(text)
                d["grounded"], d["sources"] = True, src
            except Exception:
                pat = None
        if not pat:
            pat = llm.ask(PATTERN_ASK.format(exam=d["exam"]), max_tokens=4000, check=_pattern_check, temp=0.2, label="pattern")
            d["grounded"], d["sources"] = False, []
        pat["options_per_question"] = 5 if int(pat.get("options_per_question") or 4) == 5 else 4
        d["pattern"] = pat
        d["mocks"] = [{"sections": [{"name": s["name"], "questions": []} for s in pat["sections"]]} for _ in range(d["n"])]
        d["stage"] = "questions"
        tg = mock_targets(d)
        ping(f"Pattern: {pat.get('exam_name','')} {pat.get('stage','')}, {len(tg)} sections, {sum(tg)} questions"
             f"{'' if d['grounded'] else ' (from AI knowledge, verify with the official notice)'}. Writing questions…")
        return False
    if d["stage"] == "questions":
        tg = mock_targets(d)
        pat = d["pattern"]
        for mi, mk in enumerate(d["mocks"]):
            for si, sec in enumerate(mk["sections"]):
                need = tg[si] - len(sec["questions"])
                if need > 0:
                    ps = pat["sections"][si]
                    ctx = (f"Exam: {pat.get('exam_name', d['exam'])} - {pat.get('post','')} {pat.get('stage','')}. "
                           f"Mock test {mi+1} of {d['n']}. Section topics: {', '.join(ps.get('topics') or [])}. "
                           "Real exam difficulty and question style.")
                    avoid = [q for m2 in d["mocks"] for s2 in m2["sections"] if s2["name"] == sec["name"] for q in s2["questions"]]
                    new = mcq.generate(ctx, sec["name"], min(need, 25), pat["options_per_question"], avoid=avoid,
                                       chunk=25, label=f"mock{mi+1}")
                    sec["questions"] += new
                    if len(sec["questions"]) >= tg[si]:
                        done = sum(len(s["questions"]) for s in mk["sections"])
                        ping(f"Mock {mi+1}/{d['n']}: {sec['name']} done ({done}/{sum(tg)} questions)")
                    return False
        d["stage"] = "ops" if d.get("ops") else "render"
        return False
    if d["stage"] == "ops":
        return ops_step(d, ping)
    if d["stage"] == "render":
        out = render.WORK / "out" / f"mock-{slug(d['pattern'].get('exam_name') or d['exam'])}.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        mock_render(d, out)
        d["files"] = [[k, str(p), c] for k, p, c in send_pdf(out)]
        d["stage"] = "done"
        return True
    return True


def mock_render(d, out):
    pat, th = d["pattern"], d.get("theme") or {}
    nq = [sum(len(s["questions"]) for s in m["sections"]) for m in d["mocks"]]
    mpq = pat.get("marks_per_question")
    note = ("Pattern checked with Google Search: " + ", ".join(d.get("sources") or [])) if d.get("grounded") \
        else "Pattern from AI knowledge, verify with the official notice."
    title = f"{pat.get('exam_name') or d['exam']}"
    cover = (f'<section class="cover"><div class="inner"><div class="kicker">Full-length mock test{"s" if d["n"] > 1 else ""}</div>'
             f'<h1>{esc(title)}</h1><div class="sub">{esc(" · ".join(x for x in (pat.get("post"), pat.get("stage")) if x))}</div>'
             f'<div class="blurb">{d["n"]} mock{"s" if d["n"] > 1 else ""} · {nq[0]} questions each · '
             f'{esc(pat.get("duration_minutes",""))} minutes · {esc(pat.get("negative_marking") or "no negative marking stated")}<br>'
             + (f'Shortened sample: {nq[0]} of {sum(int(s["questions"]) for s in pat["sections"])} questions, as requested.<br>' if d.get("qcap") else "")
             + f'Answer key and short explanations after each mock.</div><div class="warn">{esc(note)}</div></div>'
             f'<div class="meta"><span>{len(pat["sections"])} sections · {pat.get("options_per_question",4)} options per question</span><span>{today()}</span></div></section>')
    rows = "".join(f'<tr><td>{esc(s["name"])}</td><td>{len(d["mocks"][0]["sections"][k]["questions"])}</td>'
                   f'<td>{esc(s.get("marks",""))}</td><td>{esc(", ".join((s.get("topics") or [])[:6]))}</td></tr>'
                   for k, s in enumerate(pat["sections"]))
    notes = "".join(f"<li>{esc(n)}</li>" for n in (pat.get("notes") or [])[:6])
    front = (f'<section class="front"><h2>Exam pattern</h2><table class="pattern"><thead><tr><th>Section</th><th>Questions</th>'
             f'<th>Marks</th><th>Main topics</th></tr></thead><tbody>{rows}</tbody></table>'
             f'<div class="callout exam"><span class="t">Marking</span>{esc(mpq or "")} {"marks per question; " if mpq else ""}'
             f'{esc(pat.get("negative_marking") or "")}. Time: {esc(pat.get("duration_minutes",""))} minutes.</div>'
             f'<h2>Instructions</h2><ol class="instr"><li>Set a timer for the full duration and attempt in one sitting.</li>'
             f'<li>Mark answers on a separate sheet; do not look at the key until time is up.</li>'
             f'<li>Score: correct x marks, minus the negative marking for wrong answers; unattempted = 0.</li>'
             f'<li>Read the explanations for every wrong or guessed answer.</li>{notes}</ol>'
             f'<div class="callout tip"><span class="t">About the answer key</span>Arithmetic answers marked '
             f'"answer re-computed" were checked by calculation.</div></section>')
    parts = []
    for mi, mk in enumerate(d["mocks"]):
        allq, h, start = [], [], 1
        for sec in mk["sections"]:
            if not sec["questions"]:
                continue
            h.append(f'<div class="qsec"><h2>{esc(sec["name"])}</h2>{mcq.render_questions(sec["questions"], start)}</div>')
            start += len(sec["questions"]); allq += sec["questions"]
        parts.append(f'<section class="part"><div class="mock-head"><h1>Mock Test {mi+1}</h1><div class="m">{len(allq)} questions · '
                     f'{esc(pat.get("duration_minutes",""))} min<br>{esc(pat.get("negative_marking") or "")}</div></div>{"".join(h)}</section>'
                     f'<section class="part"><h2>Mock {mi+1}: Answer key</h2>{mcq.render_key(allq)}'
                     f'<h2>Mock {mi+1}: Explanations</h2>{mcq.render_solutions(allq)}</section>')
    html_ = page_html(title, cover + front + "".join(parts), th)
    return render.pdf(html_, out, safety=False)


# =============================== EDITS ===============================
EDIT_PLAN = """You plan edits to an existing {kind}. Current structure:
{structure}
User's edit request: <<<{instr}>>>
Reply JSON only: {{"ops": [ ... ]}} using only these operations:
{ops}
Use as few operations as needed. Numbers are 1-based."""

BOOK_OPS = """{"op":"rewrite_chapter","chapter":N,"instruction":"what to change"}
{"op":"add_mcqs","chapter":N,"count":K,"instruction":"focus"}
{"op":"add_chapter","after":N,"title":"...","instruction":"what it covers"}
{"op":"remove_chapter","chapter":N}
{"op":"theme","accent":"#RRGGBB","accent2":"#RRGGBB","dark":true|false}
{"op":"retitle","title":"...","subtitle":"..."}"""

MOCK_OPS = """{"op":"add_questions","mock":M (0 = every mock),"section":"section name","count":K,"instruction":"focus"}
{"op":"rewrite_section","mock":M (0 = every mock),"section":"section name","instruction":"what to change"}
{"op":"remove_questions","mock":M,"numbers":[question numbers as printed]}
{"op":"add_mock","count":K}
{"op":"theme","accent":"#RRGGBB","accent2":"#RRGGBB","dark":true|false}"""


def plan_edit(d, instr):
    if d["kind"] == "book":
        st = f"Book \"{d['outline']['title']}\"; chapters: " + "; ".join(
            f"{k+1}. {c['title']} ({len(c.get('mcqs') or [])} MCQs)" for k, c in enumerate(d["chapters"]))
        ops = BOOK_OPS
    else:
        st = f"{d['n']} mock(s) of {d['pattern'].get('exam_name')}; sections: " + "; ".join(
            f"{s['name']} ({len(d['mocks'][0]['sections'][k]['questions'])} q)" for k, s in enumerate(d["pattern"]["sections"]))
        ops = MOCK_OPS

    def chk(t):
        o = llm.json_obj(t)
        o = o.get("ops") if isinstance(o, dict) else o
        if not o or not all(isinstance(x, dict) and x.get("op") for x in o):
            raise llm.AIError("no edit ops")
        return o[:12]
    ops_list = llm.ask(EDIT_PLAN.format(kind=d["kind"], structure=st, instr=instr, ops=ops), max_tokens=2500,
                       check=chk, temp=0.1, label="edit-plan")
    d["ops"] = ops_list
    d["ops_total"] = len(ops_list)
    d["edits"] = (d.get("edits") or []) + [instr]
    d["stage"] = "ops"
    d.pop("files", None)
    return ops_list


def _sec_index(d, name):
    name = (name or "").lower().strip()
    secs = d["pattern"]["sections"]
    for k, s in enumerate(secs):
        if name and (name in s["name"].lower() or s["name"].lower() in name):
            return k
    words = set(re.findall(r"[a-z]{4,}", name))
    for k, s in enumerate(secs):
        if words & set(re.findall(r"[a-z]{4,}", s["name"].lower())):
            return k
    secs.append({"name": (name or "Extra questions").title(), "questions": 0, "topics": []})
    for m in d["mocks"]:
        m["sections"].append({"name": secs[-1]["name"], "questions": []})
    return len(secs) - 1


def ops_step(d, ping):
    if not d.get("ops"):
        d["stage"] = "front" if d["kind"] == "book" else "render"
        return False
    op = d["ops"][0]
    o, kind = op.get("op"), d["kind"]
    try:
        if o == "theme":
            th = d.setdefault("theme", {})
            for k in ("accent", "accent2"):
                if op.get(k):
                    th[k] = op[k]
            if "dark" in op:
                th["dark"] = bool(op["dark"])
        elif kind == "book":
            chs = d["chapters"]
            i = int(op.get("chapter") or 1) - 1
            if o == "rewrite_chapter" and 0 <= i < len(chs):
                chs[i]["html"], chs[i]["keyterms"] = write_chapter(d, i, op.get("instruction") or "improve it")
            elif o == "add_mcqs" and 0 <= i < len(chs):
                k = max(1, min(int(op.get("count") or 10), 60))
                chs[i]["mcqs"] = (chs[i].get("mcqs") or []) + chapter_mcqs(d, i, k, op.get("instruction", ""), chs[i].get("mcqs") or [])
            elif o == "add_chapter":
                a = max(0, min(int(op.get("after") or len(chs)), len(chs)))
                chs.insert(a, {"title": op.get("title") or "New chapter", "summary": op.get("instruction", ""),
                               "topics": [op.get("instruction", "")]})
                d["ops"][0] = {"op": "_fill"}   # next steps write it via the normal chapter loop
            elif o == "remove_chapter" and 0 <= i < len(chs):
                chs.pop(i)
            elif o == "retitle":
                d["outline"]["title"] = op.get("title") or d["outline"]["title"]
                d["outline"]["subtitle"] = op.get("subtitle") or d["outline"].get("subtitle", "")
            elif o == "_fill":
                for k, c in enumerate(chs):
                    if not c.get("html"):
                        c["html"], c["keyterms"] = write_chapter(d, k); return False
                    if not c.get("mcqs"):
                        c["mcqs"] = chapter_mcqs(d, k, 12); return False
        else:
            pat = d["pattern"]
            mocks = d["mocks"]
            m = int(op.get("mock") or 0)
            targets = range(len(mocks)) if m <= 0 or m > len(mocks) else [m - 1]
            if o in ("add_questions", "rewrite_section"):
                si = _sec_index(d, op.get("section"))
                ps = pat["sections"][si]
                prog = op.setdefault("_done", [])
                for mi in targets:
                    if mi in prog:
                        continue
                    sec = mocks[mi]["sections"][si]
                    ctx = (f"Exam: {pat.get('exam_name', d['exam'])} - {pat.get('post','')} {pat.get('stage','')}. "
                           f"Section topics: {', '.join(ps.get('topics') or [])}. Real exam difficulty.")
                    if o == "add_questions":
                        k = max(1, min(int(op.get("count") or 10), 100))
                        sec["questions"] += mcq.generate(ctx, sec["name"], k, pat["options_per_question"], sec["questions"],
                                                         op.get("instruction", ""), chunk=25, label="edit")
                    else:
                        k = max(1, len(sec["questions"]))
                        sec["questions"] = mcq.generate(ctx, sec["name"], k, pat["options_per_question"], (),
                                                        "Revision instruction: " + op.get("instruction", ""), chunk=25, label="edit")
                    prog.append(mi)
                    if len(prog) < len(list(targets)):
                        return False
            elif o == "remove_questions":
                for mi in targets:
                    nums = set(int(x) for x in op.get("numbers") or [])
                    c = 0
                    for sec in mocks[mi]["sections"]:
                        keep = []
                        for q in sec["questions"]:
                            c += 1
                            if c not in nums:
                                keep.append(q)
                        sec["questions"] = keep
            elif o == "add_mock":
                k = max(1, min(int(op.get("count") or 1), 5))
                for _ in range(k):
                    mocks.append({"sections": [{"name": s["name"], "questions": []} for s in pat["sections"]]})
                d["n"] = len(mocks)
                d["ops"].pop(0)
                d["stage"] = "questions"     # the normal question loop fills the new mock(s), then continues ops
                return False
    except llm.AIError:
        raise
    d["ops"].pop(0)
    done = d.get("ops_total", 1) - len(d["ops"])
    ping(f"Edit {done}/{d.get('ops_total', 1)} done")
    return False


def step(d, ping):
    return book_step(d, ping) if d["kind"] == "book" else mock_step(d, ping)


def describe(d):
    if d["kind"] == "book":
        return d.get("outline", {}).get("title") or d["topic"]
    return (d.get("pattern") or {}).get("exam_name") or d["exam"]
