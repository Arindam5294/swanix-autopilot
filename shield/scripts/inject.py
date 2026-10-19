#!/usr/bin/env python3
"""Put the fresh JSON inline into both pages (same blocks the Hark pages used)."""
import json, re, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
D, S = ROOT / "data", ROOT / "site"

def put(html_path, block_id, json_path):
    html = html_path.read_text(encoding="utf-8")
    data = json.dumps(json.load(open(json_path)), separators=(",", ":")).replace("</", "<\\/")
    pat = re.compile(r'(<script id="%s" type="application/json">)(.*?)(</script>)' % re.escape(block_id), re.S)
    if len(pat.findall(html)) != 1:
        raise SystemExit(f"{html_path}: expected exactly one {block_id} block")
    html = pat.sub(lambda m: m.group(1) + data + m.group(3), html)
    html_path.write_text(html, encoding="utf-8")

put(S / "index.html", "audit-data", D / "audit_latest.json")
put(S / "index.html", "deep-data", D / "audit_deep.json")
put(S / "cosmos" / "index.html", "data", D / "cosmos_data.json")
print("injected block", json.load(open(D / "audit_latest.json"))["block"])
