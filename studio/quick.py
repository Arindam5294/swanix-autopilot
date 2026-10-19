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
        prompt = prompts.info_prompt(req, w, h)
    doc = llm.ask(prompt, system=system, max_tokens=16000, check=llm.html_doc, temp=0.6, label="infographic")
    OUT.mkdir(parents=True, exist_ok=True)
    out = render.png(doc, OUT / f"info-{slug(original or req)}-{int(time.time())}.png", w, h)
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
