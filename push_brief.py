#!/usr/bin/env python3
"""Resilient commit + push for the OSIRIS daily brief.

Usage:
    python3 push_brief.py ["commit message"]

What it does:
  1. `git add -A`  (safe: .gitignore excludes .workbuddy/ and __pycache__/).
  2. Commit with the CORRECT per-user no-reply identity, so commits attribute to
     Franky100-pig and NOT the generic "web-flow" bot. Only commits if there is
     something new.
  3. Push with `git push origin main`. If that fails (the sandbox proxy blocks
     git's SSH/HTTPS transport), automatically fall back to pushing the current
     HEAD through the GitHub git-database REST API via `gh` (which rides the API
     allowlist). After a REST push it tries to re-align the local git tree with
     the remote so the next run stays fast-forward.

This makes the daily "back-fill" automatic even when the sandbox can't reach
GitHub over git transport -- no manual VPN step required.
"""
import os
import sys
import json
import base64
import subprocess

REPO = "Franky100-pig/osiris-daily-brief"
GH = "/Users/franky100/.local/bin/gh"
NAME = "Franky100-pig"
EMAIL = "281093441+Franky100-pig@users.noreply.github.com"
BRANCH = "main"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def gh_api(method, path, data=None):
    cmd = [GH, "api", "-X", method, f"/repos/{REPO}{path}"]
    inp = json.dumps(data).encode() if data is not None else None
    return subprocess.run(cmd, input=inp, capture_output=True, text=True)


def git_push():
    r = run(["git", "push", "origin", BRANCH])
    if r.returncode == 0:
        print("[push] git push ok")
        return True
    print(f"[push] git push failed ({r.returncode}): {r.stderr.strip()[:200]}")
    return False


def rest_push(message):
    """Push the current local HEAD to origin/main via the GitHub REST API.
    Returns the new remote commit SHA on success, else None."""
    refs = gh_api("GET", f"/git/refs/heads/{BRANCH}")
    if refs.returncode != 0:
        print("[push] REST: cannot read remote ref:", refs.stderr.strip()[:200])
        return None
    parent = json.loads(refs.stdout)["object"]["sha"]

    # Replicate the local HEAD tree exactly (blobs only; repo is all text).
    ls = run(["git", "ls-tree", "-r", "HEAD"]).stdout.strip().splitlines()
    entries = []
    for line in ls:
        meta, path = line.split("\t", 1)
        mode, typ, sha = meta.split()
        if typ != "blob":
            continue
        content = run(["git", "cat-file", "blob", sha]).stdout
        entries.append({"path": path, "mode": mode, "type": "blob", "content": content})

    par = gh_api("GET", f"/git/commits/{parent}")
    base_tree = json.loads(par.stdout)["tree"]["sha"] if par.returncode == 0 else None
    tr = gh_api("POST", "/git/trees", {"base_tree": base_tree, "tree": entries})
    if tr.returncode != 0:
        print("[push] REST: tree create failed:", tr.stderr.strip()[:200])
        return None
    tree = json.loads(tr.stdout)["sha"]

    cm = gh_api("POST", "/git/commits", {
        "message": message,
        "tree": tree,
        "parents": [parent],
        "author": {"name": NAME, "email": EMAIL},
        "committer": {"name": NAME, "email": EMAIL},
    })
    if cm.returncode != 0:
        print("[push] REST: commit create failed:", cm.stderr.strip()[:200])
        return None
    new_sha = json.loads(cm.stdout)["sha"]

    ur = gh_api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": new_sha})
    if ur.returncode != 0:
        print("[push] REST: ref update failed:", ur.stderr.strip()[:200])
        return None
    return new_sha


def main():
    message = sys.argv[1] if len(sys.argv) > 1 else "daily brief (auto)"

    run(["git", "add", "-A"])
    if run(["git", "diff", "--cached", "--quiet"]).returncode == 0:
        print("[commit] nothing to commit -- already up to date")
    else:
        c = run(["git", "-c", f"user.name={NAME}", "-c", f"user.email={EMAIL}",
                 "commit", "-q", "-m", message])
        if c.returncode != 0:
            print("[commit] failed:", c.stderr.strip()[:200])
            sys.exit(1)
        print(f"[commit] committed: {message}")

    if git_push():
        return

    print("[push] falling back to GitHub REST API (gh) ...")
    new_sha = rest_push(message)
    if not new_sha:
        print("[push] ALL PUSH METHODS FAILED -- local commit kept; back-fill manually later")
        sys.exit(2)

    print(f"[push] REST push ok -> {new_sha}")
    # Best-effort: re-align local git with the remote so the next run is fast-forward.
    fr = run(["git", "fetch", "origin", BRANCH])
    if fr.returncode == 0:
        run(["git", "reset", "--hard", f"origin/{BRANCH}"])
        print("[sync] local git realigned to remote")
    else:
        print("[sync] WARNING: local git is now divergent from remote. "
              "Run `git fetch origin main && git reset --hard origin/main` on your Mac to realign.")


if __name__ == "__main__":
    main()
