import urllib.request
import urllib.parse
import re
import subprocess
import feedparser

def get_trend():
    feed = feedparser.parse("https://trends.google.com/trends/trendingsearches/daily/rss?geo=US")
    return feed.entries[0].title

def get_yt_link(query):
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query + " shorts")
    html = urllib.request.urlopen(url).read().decode()
    vids = re.findall(r"watch\?v=(\S{11})", html)
    return "https://www.youtube.com/watch?v=" + vids[0] if vids else None

if __name__ == "__main__":
    print("Catching today's top trend...")
    trend = get_trend()
    print(f"Trend: {trend}")
    link = get_yt_link(trend)
    
    if link:
        print(f"Found viral clip: {link}")
        # Inject the viral link into your bot's existing queue system
        with open("botbeat/queue.txt", "a") as f:
            f.write(link + "\n")
            
        # Trigger your existing pipeline
        subprocess.run(["python", "botbeat/find_clip.py"])
        subprocess.run(["python", "botbeat/make_short.py"])
        subprocess.run(["python", "botbeat/upload_youtube.py"])
    else:
        print("No video found.")
