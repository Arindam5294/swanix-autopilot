"""Multiple-choice questions: AI generation in chunks, validation, Python re-computation of arithmetic answers,
and HTML rendering (question paper, answer key, explanations)."""
import ast, html, json, math, operator, re
import llm

LETTERS = "ABCDE"

MCQ_SYSTEM = """You write exam-quality multiple-choice questions. Reply with JSON only:
{"questions": [{"q": "question text", "options": ["...", "..."], "answer": 0, "explanation": "1-3 sentences",
  "calc": null}]}
Rules:
- "answer" is the 0-based index of the single correct option. Options are distinct and plausible; no "all of the above" spam.
- Match the real exam's difficulty and style. Accurate facts only. Vary topics within the section; no repeats.
- For any question whose answer is a number computed by arithmetic, set "calc" to ONE Python arithmetic expression
  that evaluates to the correct answer, using only numbers, + - * / // % **, parentheses and the functions
  sqrt, abs, round, min, max, gcd, lcm, factorial, comb, perm, log10, log2, ln, pi, e (example: "(1200*8*3)/100").
  The option text for that answer must contain that number (units allowed, e.g. "Rs 288", "12.5%", "3/4").
  For non-numeric questions set "calc": null.
- Data-interpretation or table questions must include the data in the question text.
- Keep each question self-contained. Use plain text; write math like 3/4, x^2, sqrt(2)."""

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FN = {"sqrt": math.sqrt, "abs": abs, "round": round, "min": min, "max": max, "gcd": math.gcd, "lcm": math.lcm,
       "factorial": math.factorial, "comb": math.comb, "perm": math.perm, "log10": math.log10, "log2": math.log2,
       "ln": math.log, "log": math.log, "floor": math.floor, "ceil": math.ceil}
_CONST = {"pi": math.pi, "e": math.e}


def safe_eval(expr):
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(b) > 100:
                raise ValueError("power too big")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FN and not n.keywords:
            args = [ev(a) for a in n.args]
            if n.func.id in ("factorial", "comb", "perm", "gcd", "lcm"):
                args = [int(a) for a in args]
                if max(args or [0]) > 500:
                    raise ValueError("too big")
            return _FN[n.func.id](*args)
        if isinstance(n, ast.Name) and n.id in _CONST:
            return _CONST[n.id]
        raise ValueError("unsupported expression")
    expr = str(expr).replace("^", "**").replace("×", "*").replace("÷", "/")
    return float(ev(ast.parse(expr, mode="eval")))


_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?(?:\s*/\s*\d+)?|-?\.\d+")


def option_values(text):
    vals = []
    for m in _NUM.finditer(str(text)):
        s = m.group(0).replace(",", "").replace(" ", "")
        try:
            if "/" in s:
                a, b = s.split("/")
                vals.append(float(a) / float(b))
            else:
                vals.append(float(s))
        except Exception:
            pass
    return vals


def fmt_num(v):
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v)):,}" if abs(v) >= 10000 else str(int(round(v)))
    return f"{v:.2f}".rstrip("0").rstrip(".")


def verify(q):
    """Recompute q['calc'] and make the answer key agree with it. Returns 'ok' | 'fixed' | 'replaced' | 'skip'."""
    c = q.get("calc")
    if not c:
        return "skip"
    try:
        val = safe_eval(c)
    except Exception:
        q["calc"] = None
        return "skip"
    if math.isnan(val) or math.isinf(val):
        return "skip"
    vals = [option_values(o) for o in q["options"]]
    if any(len(v) != 1 for v in vals):   # ratios, ranges, "x and y" answers: too ambiguous to auto-fix
        return "skip"
    best, bestd = None, None
    for i, vs in enumerate(vals):
        for v in vs:
            d = abs(v - val) / max(1.0, abs(val))
            if bestd is None or d < bestd:
                best, bestd = i, d
    tol = 0.006
    if best is not None and bestd <= tol:
        if best != q["answer"]:
            q["answer"] = best
            q["verified"] = "fixed"
            return "fixed"
        q["verified"] = "ok"
        return "ok"
    # no option holds the true value: put it into the keyed option, keeping that option's units/wording
    i = q["answer"]
    o = str(q["options"][i])
    m = _NUM.search(o)
    q["options"][i] = (o[:m.start()] + fmt_num(val) + o[m.end():]) if m else fmt_num(val)
    q["verified"] = "replaced"
    return "replaced"


def _check(n_min):
    def chk(t):
        d = llm.json_obj(t)
        qs = d.get("questions") if isinstance(d, dict) else d
        good = []
        for q in qs or []:
            try:
                opts = [str(o).strip() for o in q["options"]]
                a = int(q["answer"])
                if str(q["q"]).strip() and 3 <= len(opts) <= 5 and 0 <= a < len(opts) and len(set(opts)) == len(opts):
                    good.append({"q": str(q["q"]).strip(), "options": opts, "answer": a,
                                 "explanation": str(q.get("explanation") or "").strip(), "calc": q.get("calc")})
            except Exception:
                continue
        if len(good) < n_min:
            raise llm.AIError(f"only {len(good)} valid questions")
        return good
    return chk


def generate(context, section, count, n_options=4, avoid=(), extra="", chunk=20, label="mcq"):
    """Generate `count` questions for one section in chunks of <= chunk. Arithmetic answers are re-verified."""
    out = []
    tries = 0
    while len(out) < count and tries < 6:
        tries += 1
        n = min(chunk, count - len(out))
        seen = [q["q"][:70] for q in list(avoid)[-40:] + out[-40:]]
        p = (f"{context}\nSection: {section}\nWrite exactly {n} new questions, each with exactly {n_options} options.\n{extra}\n"
             + ("Do NOT repeat these existing questions:\n- " + "\n- ".join(seen) if seen else ""))
        qs = llm.ask(p, system=MCQ_SYSTEM, max_tokens=12000, check=_check(max(1, int(n * 0.6))), temp=0.7,
                     label=f"{label}:{section[:20]}")
        for q in qs:
            q["options"] = q["options"][:n_options]
            if q["answer"] >= len(q["options"]):
                continue
            verify(q)
            out.append(q)
    return out[:count]


def esc(s):
    return html.escape(str(s)).replace("\n", "<br>")


def qtext(s):
    """Question text; consecutive 'a | b | c' lines become a small table (data-interpretation questions)."""
    out, rows = [], []
    def flush():
        if rows:
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows if not re.fullmatch(r"[\s|:\-]+", r)]
            if cells:
                out.append('<table class="dt"><thead><tr>' + "".join(f"<th>{esc(c)}</th>" for c in cells[0]) + "</tr></thead><tbody>"
                           + "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in cells[1:]) + "</tbody></table>")
            rows.clear()
    for line in str(s).split("\n"):
        if line.count("|") >= 1 and len(line.split("|")) >= 2 and len(line) < 200:
            rows.append(line)
        else:
            flush()
            out.append(esc(line))
    flush()
    return "<br>".join(x for x in out if x != "").replace("<br><table", "<table").replace("</table><br>", "</table>")


def render_questions(qs, start=1):
    h = []
    for k, q in enumerate(qs):
        opts = q["options"]
        cls = "opts two" if max(len(o) for o in opts) <= 38 else "opts"
        h.append(f'<div class="q"><div class="qn">{start+k}.</div><div class="qb"><div class="qt">{qtext(q["q"])}</div>'
                 f'<ol class="{cls}">' + "".join(f'<li><span class="ol">({LETTERS[i]})</span> {esc(o)}</li>'
                                               for i, o in enumerate(opts)) + "</ol></div></div>")
    return "\n".join(h)


def render_key(qs, start=1, cols=10):
    cells = [f'<td><b>{start+k}</b> {LETTERS[q["answer"]]}</td>' for k, q in enumerate(qs)]
    rows = ["<tr>" + "".join(cells[i:i + cols]) + "</tr>" for i in range(0, len(cells), cols)]
    return '<table class="key">' + "".join(rows) + "</table>"


def render_solutions(qs, start=1):
    h = []
    for k, q in enumerate(qs):
        tag = ' <span class="chk">answer re-computed</span>' if q.get("verified") in ("fixed", "replaced", "ok") else ""
        h.append(f'<div class="sol"><b>{start+k}. ({LETTERS[q["answer"]]})</b> {esc(q["options"][q["answer"]])}{tag}'
                 f'<div>{esc(q.get("explanation",""))}</div></div>')
    return "\n".join(h)
