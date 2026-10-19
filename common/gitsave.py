#!/usr/bin/env python3
"""Commit workflow state back to the repo without losing anyone's changes when two workflows race.

python common/gitsave.py "message" --mirror <paths...> --lines <files...>
  --mirror: files/folders this workflow owns; our copy wins (deletions included)
  --lines : shared line lists (e.g. botbeat/queue.txt); lines we added/removed since checkout are re-applied
            on top of the newest remote version, so another workflow's edits are kept too.
Retries up to 5 times (fetch newest, re-apply, push)."""
import os, pathlib, shutil, subprocess, sys, tempfile, time


def git(*a, check=True):
    r = subprocess.run(["git", *a], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(a)}: {r.stderr.strip()[-300:]}")
    return r.stdout


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); return
    msg, mirror, lines, mode = args[0], [], [], None
    for a in args[1:]:
        if a in ("--mirror", "--lines"):
            mode = a; continue
        (mirror if mode == "--mirror" else lines).append(a)
    root = pathlib.Path(git("rev-parse", "--show-toplevel").strip())
    os.chdir(root)
    git("config", "user.name", "swanix-bot")
    git("config", "user.email", "bot@users.noreply.github.com")
    branch = git("rev-parse", "--abbrev-ref", "HEAD").strip() or "main"

    # snapshot what this run produced
    snap = pathlib.Path(tempfile.mkdtemp())
    for p in mirror:
        if os.path.isdir(p):
            shutil.copytree(p, snap / p, dirs_exist_ok=True)
        elif os.path.isfile(p):
            (snap / p).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, snap / p)
    deltas = {}
    for p in lines:
        base = git("show", f"HEAD:{p}", check=False).splitlines()
        cur = pathlib.Path(p).read_text().splitlines() if os.path.exists(p) else []
        b, c = [x.strip() for x in base if x.strip()], [x.strip() for x in cur if x.strip()]
        deltas[p] = ([x for x in c if x not in b], set(x for x in b if x not in c))

    for attempt in range(5):
        git("fetch", "--depth=1", "origin", branch, check=False)
        git("reset", "--hard", "FETCH_HEAD", check=False)
        for p in mirror:
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.exists(p):
                os.remove(p)
            s = snap / p
            if s.is_dir():
                shutil.copytree(s, p)
            elif s.is_file():
                pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, p)
        for p, (added, removed) in deltas.items():
            cur = pathlib.Path(p).read_text().splitlines() if os.path.exists(p) else []
            keep = [x for x in cur if x.strip() and x.strip() not in removed]
            have = set(x.strip() for x in keep)
            keep += [x for x in added if x not in have]
            if keep or os.path.exists(p):
                pathlib.Path(p).parent.mkdir(parents=True, exist_ok=True)
                pathlib.Path(p).write_text("\n".join(keep) + ("\n" if keep else ""))
        for p in mirror + lines:
            git("add", "-A", "--", p, check=False)   # one by one: a missing path must not block the others
        if not git("diff", "--cached", "--name-only").strip():
            print("nothing to commit"); return
        git("commit", "-q", "-m", msg)
        r = subprocess.run(["git", "push", "-q", "origin", f"HEAD:{branch}"], capture_output=True, text=True)
        if r.returncode == 0:
            print("saved"); return
        print("push raced with another run, retrying…")
        time.sleep(3 + attempt * 4)
    print("could not push state after 5 tries"); sys.exit(1)


if __name__ == "__main__":
    main()
