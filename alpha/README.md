# Swanix Alpha Bot
Posts the earliest crypto news, brand-new projects and trending coins to the Swanix Crypto Telegram channel. 100% free: runs on GitHub Actions every 15 minutes, no server needed.

Sources: CoinDesk, Cointelegraph, The Block, Decrypt, Google News (Ordinals/BRC-20/Runes, airdrops/testnets, perp DEX), DefiLlama new project listings (with their X handles), CoinGecko trending.

## Setup (10 minutes)
1. In Telegram, open @BotFather, send /newbot, pick a name, copy the token.
2. Add the bot to your channel as an admin with "Post messages".
3. Create a new public GitHub repo and upload these files (keep the .github folder).
4. Repo Settings > Secrets and variables > Actions > New secret:
   - TELEGRAM_BOT_TOKEN = the token from BotFather
   - TELEGRAM_CHAT_ID = @swanixcrypto (or your own user ID to review first)
5. Actions tab > enable workflows > "Swanix Alpha Bot" > Run workflow.
The first run only memorises existing news so the channel isn't flooded; new items post from the next run on.

Edit KEYWORDS in bot.py to change the topics. MAX_POSTS limits posts per run (default 8).

## ord.net Ordinals market digest (new)
Twice a day (9:00 AM and 9:00 PM IST) the bot posts an Ordinals floor report from ord.net:
- 👑 floors of the top collections (BTC + USD, 24h change)
- 📈 top movers in 24h (pumps and dumps)
- 🐋 biggest sales of the day + most traded collection
- 💸 collections on discount (floor well below its 7d/30d level, or sitting just above the top bid)
- an auto-picked catchy headline based on the day's data

No key needed. Turn it off by adding a repo variable/env `ORDNET=0`. Change times in `ordnet.due_slot(hours=(9, 21))`.
