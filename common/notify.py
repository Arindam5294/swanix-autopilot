"""Send a message to Telegram if TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are set; otherwise print it
(the workflow then opens a GitHub issue, which GitHub emails you)."""
import os, json, urllib.request, urllib.parse

def telegram(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return False
    for i in range(0, len(text), 3900):
        data = urllib.parse.urlencode({"chat_id": chat, "text": text[i:i+3900],
                                       "disable_web_page_preview": "true"}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=30).read()
    return True
