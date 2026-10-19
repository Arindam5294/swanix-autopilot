"""Minimal Telegram Bot API client for Studio. Never prints the token.
Set STUDIO_FAKE_TG=1 to record calls in tg.SENT instead of calling Telegram (used by the tests)."""
import json, os, subprocess, time, urllib.parse, urllib.request

SENT = []          # fake mode log
_FAKE_ID = [9000]


def fake():
    return os.environ.get("STUDIO_FAKE_TG") == "1"


def _tok():
    t = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not t:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing")
    return t


def _scrub(s):
    t = os.environ.get("TELEGRAM_BOT_TOKEN") or "@@none@@"
    return str(s).replace(t, "***")


def call(method, data=None, files=None, timeout=120):
    """POST to the Bot API. files = {"document": "/path"}. Returns the 'result' object."""
    data = {k: v for k, v in (data or {}).items() if v is not None}
    if fake():
        _FAKE_ID[0] += 1
        SENT.append({"method": method, "data": data, "files": files})
        return {"message_id": _FAKE_ID[0]}
    url = f"https://api.telegram.org/bot{_tok()}/{method}"
    for attempt in range(3):
        try:
            if files:
                cmd = ["curl", "-s", "-S", "--max-time", str(timeout)]
                for k, v in data.items():
                    cmd += ["--form-string", f"{k}={v if isinstance(v, str) else json.dumps(v)}"]
                for k, p in files.items():
                    cmd += ["-F", f"{k}=@{p}"]
                r = subprocess.run(cmd + [url], capture_output=True, text=True, timeout=timeout + 30)
                out = json.loads(r.stdout or "{}")
            else:
                body = urllib.parse.urlencode({k: (v if isinstance(v, str) else json.dumps(v)) for k, v in data.items()}).encode()
                with urllib.request.urlopen(url, body, timeout=timeout) as r:
                    out = json.loads(r.read())
            if out.get("ok"):
                return out["result"]
            if out.get("error_code") == 429:
                time.sleep(int((out.get("parameters") or {}).get("retry_after", 5)) + 1)
                continue
            if "reply" in str(out.get("description", "")).lower() and "reply_parameters" in data:
                data.pop("reply_parameters")      # original message deleted: send without reply
                continue
            raise RuntimeError(f"Telegram {method}: {out.get('description')}")
        except RuntimeError:
            raise
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(_scrub(f"Telegram {method} failed: {e}"))
            time.sleep(3)


def _reply(reply_to):
    return {"message_id": int(reply_to), "allow_sending_without_reply": True} if reply_to else None


def send(chat, text, reply_to=None):
    res = None
    for i in range(0, max(1, len(text)), 3900):
        res = call("sendMessage", {"chat_id": str(chat), "text": text[i:i + 3900], "disable_web_page_preview": "true",
                                   "reply_parameters": _reply(reply_to)})
    return res


def send_file(chat, kind, path, caption="", reply_to=None):
    """kind: document | photo | video. Returns message_id."""
    method = {"document": "sendDocument", "photo": "sendPhoto", "video": "sendVideo"}[kind]
    data = {"chat_id": str(chat), "caption": caption[:1000] or None, "reply_parameters": _reply(reply_to)}
    if kind == "video":
        data["supports_streaming"] = "true"
    return call(method, data, {kind: str(path)}, timeout=600)["message_id"]


def get_updates(offset):
    inbox = os.environ.get("STUDIO_INBOX_URL")
    if inbox and not fake():   # the Cloudflare bot relay holds messages (Telegram webhook mode)
        req = urllib.request.Request(f"{inbox}/inbox?offset={offset}", headers={"x-studio-key": os.environ.get("STUDIO_INBOX_KEY", ""), "user-agent": "studio"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read()).get("result", [])
        except Exception as e:
            raise RuntimeError(f"inbox read failed: {e}")
    if fake():
        return []
    url = f"https://api.telegram.org/bot{_tok()}/getUpdates?offset={offset}&timeout=0&allowed_updates=%5B%22message%22%5D"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.loads(r.read())
    except Exception as e:
        raise RuntimeError(_scrub(f"getUpdates failed: {e}"))
    return d.get("result", [])
