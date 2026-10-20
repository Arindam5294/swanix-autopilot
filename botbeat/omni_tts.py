"""Botbeat signature voice, fully local and free (OmniVoice, the engine behind VoiceStudio).
usage: python omni_tts.py <text_file> <out.mp3> <words.json>
Every run designs the same channel voice from a fixed seed + fixed line, then clones it for the
script, so every Short sounds like the same narrator (no files to keep between runs). Word timings come from faster-whisper.
Any failure exits non-zero and make_short.py falls back to edge-tts.   -- forged by Swanix 🐸
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).parent
VOICE_DIR = HERE / "voice"
REF_WAV, REF_TXT = VOICE_DIR / "botbeat_voice.flac", VOICE_DIR / "botbeat_voice.txt"
if not REF_WAV.exists():
    REF_WAV, REF_TXT = Path("/tmp/botbeat_voice/ref.wav"), Path("/tmp/botbeat_voice/ref.txt")
CFG = json.load(open(HERE / "config.json"))
INSTRUCT = CFG.get("omni_instruct", "male, middle-aged, low pitch, american accent")
REF_LINE = ("Welcome back to Botbeat. Today we look at a robot that just did "
            "something nobody expected, and why it matters.")
SR = 24000

def log(*a): print("[omni]", *a, file=sys.stderr, flush=True)

def main():
    text_file, out_mp3, words_json = sys.argv[1:4]
    text = Path(text_file).read_text().strip()
    import torch, soundfile as sf
    from omnivoice import OmniVoice
    torch.set_num_threads(os.cpu_count() or 4)
    t0 = time.time()
    model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cpu", dtype=torch.float32)
    log(f"model loaded in {time.time()-t0:.0f}s")
    steps = int(CFG.get("omni_steps", 16))

    if not REF_WAV.exists():   # a committed botbeat/voice/botbeat_voice.flac (yours) always wins
        REF_WAV.parent.mkdir(parents=True, exist_ok=True)
        torch.manual_seed(int(CFG.get("omni_seed", 1294)))
        ref = model.generate(text=REF_LINE, instruct=INSTRUCT, num_step=32)[0]
        sf.write(REF_WAV, ref, SR); REF_TXT.write_text(REF_LINE)
        log("designed Botbeat voice from seed")

    t1 = time.time()
    audio = model.generate(text=text, ref_audio=str(REF_WAV), ref_text=REF_TXT.read_text(),
                           num_step=steps, speed=float(CFG.get("omni_speed", 1.04)))[0]
    wav = Path(out_mp3).with_suffix(".omni.wav")
    sf.write(wav, audio, SR)
    log(f"voice generated in {time.time()-t1:.0f}s, {len(audio)/SR:.1f}s of audio")
    if len(audio) / SR < 5: raise SystemExit("audio too short")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-ar", "24000", "-b:a", "160k", out_mp3], check=True)

    words = []
    try:
        from faster_whisper import WhisperModel
        wm = WhisperModel("base.en", device="cpu", compute_type="int8")
        import librosa, numpy as np
        a16 = librosa.resample(np.asarray(audio, dtype=np.float32), orig_sr=SR, target_sr=16000)
        segs, _ = wm.transcribe(a16, word_timestamps=True, language="en")
        for s in segs:
            for w in s.words or []:
                words.append({"t": round(w.start, 3), "d": round(max(0.05, w.end - w.start), 3), "w": w.word.strip()})
    except Exception as e:
        log("whisper timings failed:", type(e).__name__, e)
    sw = text.split()
    if words and len(words) == len(sw):  # same count: keep the script's spelling, whisper's timing
        for w, t in zip(words, sw): w["w"] = t
    if not words:  # spread the script words by length as a last resort
        ws = text.split(); total = len(audio) / SR; lens = [len(w) + 2 for w in ws]; acc = 0
        for w, l in zip(ws, lens):
            d = total * l / sum(lens); words.append({"t": acc, "d": d, "w": w}); acc += d
    json.dump(words, open(words_json, "w"))
    log(f"done, {len(words)} timed words")

if __name__ == "__main__":
    main()
