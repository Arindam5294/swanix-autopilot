#!/usr/bin/env python3
"""Send the finished Short + copy-paste metadata to Telegram, and mark the clip used."""
import json, os, pathlib, subprocess
HERE = pathlib.Path(__file__).resolve().parent
m = json.load(open(HERE / "out" / "metadata.json"))
tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
text = (f"🎬 New Botbeat Short ready ({m['seconds']} s)\n\nTITLE:\n{m['title']}\n\nDESCRIPTION:\n{m['description']}\n\n"
        f"TAGS:\n{', '.join(m['tags'])}\n\nSource: {m['source_url']}\n"
        "Upload: YouTube app → + → Short → pick this video. Not made for kids. Altered content: No (real footage, AI voice).")
if tok and chat:
    subprocess.run(["curl", "-s", "-F", f"chat_id={chat}", "-F", f"video=@{HERE/'out'/'short.mp4'}", "-F", "supports_streaming=true",
                    f"https://api.telegram.org/bot{tok}/sendVideo"], check=True, capture_output=True)
    subprocess.run(["curl", "-s", "--data-urlencode", f"text={text}", "-d", f"chat_id={chat}",
                    f"https://api.telegram.org/bot{tok}/sendMessage"], check=True, capture_output=True)
else:
    (HERE / "out" / "upload_me.txt").write_text(text)
used_f = HERE / "used.json"
used = json.loads(used_f.read_text()) if used_f.exists() else []
used.append(m["source_url"]); used_f.write_text(json.dumps(used))
q = HERE / "queue.txt"
if q.exists():
    q.write_text("\n".join(l for l in q.read_text().splitlines() if l.strip() and l.strip() != m["source_url"]) + "\n")
print(text)
