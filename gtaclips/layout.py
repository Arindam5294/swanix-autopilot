#!/usr/bin/env python3
"""Playbook step 6: facecam on top, gameplay below.
find_facecam(video) looks at ~10 frames for a face that stays in the SAME spot (a streamer's webcam box).
Gameplay faces move around, a webcam doesn't. Returns (x, y, w, h) of the webcam box in source pixels, or None.
split_filter(...) gives the ffmpeg filter for a 1080x1920 split: webcam 1080x640 on top, gameplay 1080x1280 below."""
import subprocess, sys, json, pathlib, statistics

TOP_H, BOT_H = 640, 1280

def _frames(video, n=10):
    import cv2
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    out = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / n))
        ok, f = cap.read()
        if ok: out.append(f)
    cap.release()
    return out

def find_facecam(video, min_hits=0.4):
    try:
        import cv2
    except ImportError:
        print("layout: opencv missing, no split layout", file=sys.stderr); return None
    frames = _frames(video)
    if not frames: return None
    H, W = frames[0].shape[:2]
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    hits = []
    for f in frames:
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        for (x, y, w, h) in casc.detectMultiScale(g, 1.1, 6, minSize=(max(24, W // 40), max(24, W // 40))):
            hits.append((x + w / 2, y + h / 2, w, h))
    if not hits: return None
    # cluster faces that sit in the same place (within 6% of the frame)
    best = []
    for c in hits:
        grp = [h for h in hits if abs(h[0] - c[0]) < W * 0.06 and abs(h[1] - c[1]) < H * 0.06]
        if len(grp) > len(best): best = grp
    if len(best) < max(2, min_hits * len(frames)): return None
    cx, cy = statistics.median(h[0] for h in best), statistics.median(h[1] for h in best)
    fw, fh = statistics.median(h[2] for h in best), statistics.median(h[3] for h in best)
    if fw > W * 0.45: return None            # face fills the screen: a just-chatting cam, not a webcam box
    # 1st choice: the webcam's own rectangle = the still region around the face (gameplay pixels change, the cam
    # background doesn't). 2nd choice: ~3.4 face widths around the face.
    r = _static_box(frames, cx, cy, fw, fh)
    if r:
        x0, y0, rw, rh = r
        if rw / rh > 1080 / TOP_H: bw, bh = rh * 1080 / TOP_H, rh
        else: bw, bh = rw, rw * TOP_H / 1080
        x = min(max(x0, cx - bw / 2), x0 + rw - bw); y = min(max(y0, cy - bh * 0.45), y0 + rh - bh)
        box = tuple(int(round(v)) // 2 * 2 for v in (x, y, bw, bh))
        print(f"layout: webcam box {r} -> crop {box} in {W}x{H}", file=sys.stderr)
        return box
    bw = min(W, fw * 3.4); bh = bw * TOP_H / 1080
    if bh > H: bh = H; bw = bh * 1080 / TOP_H
    x = min(max(0, cx - bw / 2), W - bw); y = min(max(0, cy - bh * 0.45), H - bh)
    box = tuple(int(round(v)) // 2 * 2 for v in (x, y, bw, bh))
    print(f"layout: facecam found at {box} in {W}x{H} ({len(best)}/{len(frames)} frames)", file=sys.stderr)
    return box

def _static_box(frames, cx, cy, fw, fh):
    import cv2, numpy as np
    k = 4
    g = np.stack([cv2.cvtColor(cv2.resize(f, (f.shape[1] // k, f.shape[0] // k)), cv2.COLOR_BGR2GRAY).astype(np.float32) for f in frames])
    still = (g.std(axis=0) < 6).astype(np.uint8)
    still = cv2.morphologyEx(still, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(still)
    H, W = still.shape
    px, py = int(cx / k), int(cy / k)
    # the face itself moves a little, so look at a ring just outside it for the cam background
    ids = {lab[yy, xx] for yy in range(max(0, py - int(fh / k)), min(H, py + int(fh / k)), 2)
           for xx in range(max(0, px - int(fw / k)), min(W, px + int(fw / k)), 2) if lab[yy, xx]}
    best = None
    for i in ids:
        x, y, w, h, a = stats[i]
        x, y, w, h = x * k, y * k, w * k, h * k
        if 1.5 * fw <= w <= 0.6 * W * k and 1.5 * fh <= h <= 0.75 * H * k and x <= cx <= x + w and y <= cy <= y + h:
            if not best or w * h > best[2] * best[3]: best = (x, y, w, h)
    if best and best[2] <= 0.6 * W * k and best[3] <= 0.75 * H * k: return best
    return None

def split_filter(inp, out, box, gameplay_shift=0.0):
    """inp: an ffmpeg video label already trimmed + fps'd. Gameplay crop is centered (shift -0.5..0.5 moves it)."""
    x, y, w, h = box
    return (f"{inp}split[ct{out}][cb{out}];"
            f"[ct{out}]crop={w}:{h}:{x}:{y},scale=1080:{TOP_H}:force_original_aspect_ratio=increase,crop=1080:{TOP_H}[top{out}];"
            f"[cb{out}]scale=-2:{BOT_H}:flags=lanczos,crop=1080:{BOT_H}:(iw-1080)/2*(1+{gameplay_shift*2:.2f}):0[bot{out}];"
            f"[top{out}][bot{out}]vstack=inputs=2,drawbox=x=0:y={TOP_H-3}:w=1080:h=6:color=white@0.9:t=fill,setsar=1[{out}];")

if __name__ == "__main__":
    print(json.dumps(find_facecam(sys.argv[1])))
