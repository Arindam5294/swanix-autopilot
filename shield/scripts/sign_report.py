#!/usr/bin/env python3
"""Shield Audit, step 2b: sign the report (Ed25519) and timestamp it on Bitcoin (OpenTimestamps).

Run after deep_audit.py. Reads audit_deep.json, adds a "proof" object, writes it back.
- The signed message is the report's SHA-256 hex string (UTF-8), i.e. reportSha256.
- reports/report_<height>.json holds the exact bytes whose SHA-256 is reportSha256.
- reports/report_<height>.json.ots is its OpenTimestamps proof (pending until a Bitcoin block confirms it).
The private key lives in .keys/ed25519.key and never leaves this folder.
"""
import json, os, sys, glob, base64, hashlib, subprocess, pathlib
HERE = pathlib.Path(__file__).resolve().parent.parent / "data"
pass
import nacl.signing

KEY = HERE / ".keys" / "ed25519.key"
REP = HERE / "reports"; REP.mkdir(exist_ok=True)
DEEP = HERE / "audit_deep.json"
OTS = ["ots"]
ENV = dict(os.environ)

if not KEY.exists():
    KEY.parent.mkdir(exist_ok=True)
    KEY.write_bytes(nacl.signing.SigningKey.generate().encode()); os.chmod(KEY, 0o600)
sk = nacl.signing.SigningKey(KEY.read_bytes())
pub = sk.verify_key.encode().hex()

d = json.load(open(DEEP))
core = d["reportCore"]; digest = d["reportSha256"]
raw = json.dumps(core, sort_keys=True, default=str).encode()
assert hashlib.sha256(raw).hexdigest() == digest, "digest mismatch"
sig = sk.sign(digest.encode()).signature.hex()

h = core["lastHeight"]
f = REP / f"report_{h}.json"
if not f.exists():
    f.write_bytes(raw)
if not (REP / f"report_{h}.json.ots").exists():
    subprocess.run(OTS + ["stamp", str(f)], env=ENV, capture_output=True, timeout=60)

# try to upgrade pending proofs (newest 30 only), find newest Bitcoin-confirmed one
confirmed = None
proofs = sorted(glob.glob(str(REP / "report_*.json.ots")), key=lambda p: int(p.split("_")[-1].split(".")[0]), reverse=True)
for p in proofs[:72]:  # ~12h window; Bitcoin confirmation takes a few hours
    info = subprocess.run(OTS + ["info", p], env=ENV, capture_output=True, text=True, timeout=30).stdout
    if "BitcoinBlockHeaderAttestation" not in info:
        subprocess.run(OTS + ["upgrade", p], env=ENV, capture_output=True, timeout=60)
        info = subprocess.run(OTS + ["info", p], env=ENV, capture_output=True, text=True, timeout=30).stdout
    if confirmed is None and "BitcoinBlockHeaderAttestation" in info:
        btc = int(info.split("BitcoinBlockHeaderAttestation(")[1].split(")")[0])
        rh = int(p.split("_")[-1].split(".")[0])
        rawp = pathlib.Path(p[:-4]).read_bytes()
        confirmed = {"reportHeight": rh, "bitcoinBlock": btc,
                     "reportSha256": hashlib.sha256(rawp).hexdigest(),
                     "reportJson": rawp.decode(),
                     "otsBase64": base64.b64encode(pathlib.Path(p).read_bytes()).decode()}
CONF = REP / "confirmed.json"
if confirmed is not None:
    CONF.write_text(json.dumps(confirmed))
elif CONF.exists():
    confirmed = json.loads(CONF.read_text())
# prune proofs older than the newest 300
for p in proofs[300:]:
    for q in (p, p[:-4]):
        try: os.remove(q)
        except FileNotFoundError: pass

latest_ots = REP / f"report_{h}.json.ots"
d["proof"] = {
    "algorithm": "Ed25519", "publicKey": pub, "signedMessage": "reportSha256 (UTF-8 hex string)",
    "signature": sig, "reportHeight": h,
    "otsPending": latest_ots.exists() and (confirmed is None or confirmed["reportHeight"] != h),
    "otsLatestBase64": base64.b64encode(latest_ots.read_bytes()).decode() if latest_ots.exists() else None,
    "confirmed": confirmed,
}
json.dump(d, open(DEEP, "w"), indent=1)
print("signed", h, "pub", pub[:16], "confirmed", confirmed and confirmed["bitcoinBlock"])
