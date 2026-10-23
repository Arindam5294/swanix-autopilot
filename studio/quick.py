"""Single-shot makers: PDF, infographic, (video lives in video.py). Each returns
{"files": [(kind, path, caption)], "source": {...}} where source is what an edit needs later."""
import re, pathlib, time
import llm, render, prompts

OUT = render.WORK / "out"


def slug(s, n=40):
    return (re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:n] or "studio")


def classify(text):
    t = text.lower()
    if re.search(r"\b(video|reel|short|clip|animation|narrat)", t):
        guess = "video"
    elif len(t) > 160 or re.search(r"\b(guide|notes|report|ebook|handbook|syllabus|plan|chapter|study|manual|detailed|pdf|document|essay)\b", t):
        guess = "pdf"
    else:
        guess = "info"
    try:
        a = llm.ask(prompts.CLASSIFY.format(p=text[:1500]), max_tokens=400, temp=0, rounds=1, label="classify",
                    check=lambda x: next(w for w in ("info", "pdf", "video") if w in x.lower()))
        return a
    except Exception:
        return guess


def size_for(text):
    return (1080, 1920) if re.search(r"\b(story|stories|9:16|vertical|status|reel)\b", text.lower()) else (1080, 1350)


def make_info(req, html=None, size=None, instruction=None, original=None):
    w, h = size or size_for(req)
    system = prompts.INFOGRAPHIC_SYSTEM.format(W=w, H=h)
    if instruction:
        prompt = prompts.edit_html_prompt("infographic", original or req, html, instruction)
    else:
        facts = ""
        try:   # research first so the content is specific, not generic filler
            facts, _src = llm.grounded(f"List 8-12 accurate, specific facts, figures (with units and year) and steps for an infographic about: {req}. Short bullets only.")
        except Exception as e:
            print("   research skipped:", e)
        prompt = prompts.info_prompt(req, w, h, facts or "")
    doc = llm.ask(prompt, system=system, max_tokens=16000, check=llm.html_doc, temp=0.6, label="infographic")
    OUT.mkdir(parents=True, exist_ok=True)
    out = render.png(doc, OUT / f"info-{slug(original or req)}-{int(time.time())}.png", w, h)
    best = (0, doc, out)
    for rnd in range(2):   # v5: look at the rendered image, fix what a picky art director would flag
        try:
            rv = llm.json_obj(llm.see(out, prompts.INFO_REVIEW.format(req=original or req)) or "")
        except Exception:
            rv = None
        if not rv:
            break
        score = float(rv.get("score") or 0)
        print(f"   [infographic] review round {rnd + 1}: {score}/10", rv.get("fixes"))
        if score > best[0]:
            best = (score, doc, out)
        if score >= 8.5:
            break
        try:
            doc2 = llm.ask(prompts.edit_html_prompt("infographic", original or req, doc, "Fix these problems a senior art director found: "
                           + "; ".join(map(str, rv.get("fixes") or []))), system=system, max_tokens=16000, check=llm.html_doc, temp=0.4, label="infographic-fix")
            out2 = render.png(doc2, OUT / f"info-{slug(original or req)}-{int(time.time())}-r{rnd + 1}.png", w, h)
            doc, out = doc2, out2
        except Exception as e:
            print("   fix round failed:", e); break
    else:
        try:
            rv = llm.json_obj(llm.see(out, prompts.INFO_REVIEW.format(req=original or req)) or "")
            if rv and float(rv.get("score") or 0) < best[0]:
                doc, out = best[1], best[2]
        except Exception:
            pass
    photo = out
    if out.stat().st_size > 9_500_000:          # Telegram photos max 10 MB: send a JPEG preview, PNG as file
        from PIL import Image
        photo = out.with_suffix(".jpg")
        Image.open(out).convert("RGB").save(photo, quality=92)
    return {"files": [("photo", photo, ""), ("document", out, "Full-quality file")],
            "source": {"html": doc, "size": [w, h]}}


def make_pdf(req, html=None, instruction=None, original=None):
    system = prompts.PDF_SYSTEM.format()
    if instruction:
        prompt = prompts.edit_html_prompt("PDF document", original or req, html, instruction)
    else:
        prompt = prompts.pdf_prompt(req)
    doc = llm.ask(prompt, system=system, max_tokens=30000, check=llm.html_doc, temp=0.5, label="pdf")
    OUT.mkdir(parents=True, exist_ok=True)
    out = render.pdf(doc, OUT / f"{slug(original or req)}.pdf")
    parts = render.split(out)
    files = [("document", p, f"Part {i+1}/{len(parts)}" if len(parts) > 1 else "") for i, p in enumerate(parts)]
    return {"files": files, "source": {"html": doc}}
