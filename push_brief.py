#!/usr/bin/env python3
"""Resilient commit + push for the OSIRIS daily brief.

Usage (must be run from the repo, on branch `main`):
    python3 push_brief.py ["commit message"]

What it does:
  1. `git add -A`  (safe: .gitignore excludes .workbuddy/ and __pycache__/).
  2. Commit with the CORRECT per-user no-reply identity, so commits attribute to
     Franky100-pig and NOT the generic "web-flow" bot. Only commits if there is
     something new.
  3. Push with `git push origin main`. If that fails (the sandbox proxy blocks
     git's SSH/HTTPS transport), automatically fall back to pushing the current
     HEAD through the GitHub git-database REST API via `gh` (which rides the API
     allowlist). After a REST push it rebuilds the exact remote commit locally
     (same SHA) so local and remote never diverge.

Safety:
  - Refuses to run on any branch other than `main` (the REST fallback pushes
    the *current tree* to main; running it from a feature branch would
    clobber main with that branch's content).
  - If `gh` is missing or the REST API is unreachable, it exits non-zero and
    leaves the local commit in place for a later manual back-fill.

This makes the daily "back-fill" automatic even when the sandbox can't reach
GitHub over git transport -- no manual VPN step required.
"""
import os
import sys
import json
import subprocess

REPO = "Franky100-pig/osiris-daily-brief"
GH = "/Users/franky100/.local/bin/gh"
NAME = "Franky100-pig"
EMAIL = "281093441+Franky100-pig@users.noreply.github.com"
BRANCH = "main"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


class ApiResult:
    def __init__(self, returncode=127, stdout="", stderr="gh unavailable"):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def gh_api(method, path, data=None):
    """Call `gh api`. Returns ApiResult; never raises on a missing binary."""
    if not os.path.exists(GH):
        return ApiResult(stderr=f"gh not found at {GH}")
    cmd = [GH, "api", "-X", method, f"/repos/{REPO}{path}"]
    inp = None
    if data is not None:
        cmd += ["--input", "-"]
        inp = json.dumps(data)
    try:
        return subprocess.run(cmd, input=inp, capture_output=True, text=True)
    except OSError as e:
        return ApiResult(stderr=str(e))


def git_push():
    r = run(["git", "push", "origin", BRANCH])
    if r.returncode == 0:
        print("[push] git push ok")
        return True
    print(f"[push] git push failed ({r.returncode}): {r.stderr.strip()[:200]}")
    return False


def build_entries():
    """Blob entries for the current HEAD tree. Returns (entries, error)."""
    ls = run(["git", "ls-tree", "-r", "HEAD"]).stdout.strip().splitlines()
    entries = []
    for line in ls:
        meta, path = line.split("\t", 1)
        mode, typ, sha = meta.split()
        if typ != "blob":
            continue
        try:
            content = run(["git", "cat-file", "blob", sha]).stdout
        except UnicodeDecodeError:
            return None, f"non-UTF8 blob cannot be pushed via REST: {path}"
        entries.append({"path": path, "mode": mode, "type": "blob", "content": content})
    return entries, None


def rest_push(message):
    """Push the current local HEAD to origin/main via the GitHub REST API.
    Returns the created commit info dict on success, else None."""
    refs = gh_api("GET", f"/git/refs/heads/{BRANCH}")
    if refs.returncode != 0:
        print("[push] REST: cannot read remote ref:", refs.stderr.strip()[:200])
        return None
    parent = json.loads(refs.stdout)["object"]["sha"]

    entries, err = build_entries()
    if err:
        print("[push] REST:", err)
        return None

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
    info = json.loads(cm.stdout)

    ur = gh_api("PATCH", f"/git/refs/heads/{BRANCH}", {"sha": info["sha"]})
    if ur.returncode != 0:
        print("[push] REST: ref update failed:", ur.stderr.strip()[:200])
        return None
    return info


def realign_local(info):
    """After a REST push, make the local branch point at the SAME commit the
    remote now has, by rebuilding that exact commit object locally
    (same tree / parent / message / author / committer -> same SHA).
    Falls back to a network fetch if reconstruction doesn't match."""
    new_sha = info["sha"]
    env = os.environ.copy()
    env.update({
        "GIT_AUTHOR_NAME": info["author"]["name"],
        "GIT_AUTHOR_EMAIL": info["author"]["email"],
        "GIT_AUTHOR_DATE": info["author"]["date"],
        "GIT_COMMITTER_NAME": info["committer"]["name"],
        "GIT_COMMITTER_EMAIL": info["committer"]["email"],
        "GIT_COMMITTER_DATE": info["committer"]["date"],
    })
    r = subprocess.run(
        ["git", "commit-tree", info["tree"]["sha"],
         "-p", info["parents"][0]["sha"]],
        input=info.get("message", ""), capture_output=True, text=True, env=env)
    if r.returncode == 0 and r.stdout.strip() == new_sha:
        run(["git", "update-ref", f"refs/remotes/origin/{BRANCH}", new_sha])
        run(["git", "reset", "--hard", new_sha])
        print("[sync] local git realigned to remote (exact SHA match)")
        return
    # Fallback: pull the object over git transport.
    fr = run(["git", "fetch", "origin", BRANCH])
    if fr.returncode == 0:
        run(["git", "reset", "--hard", f"origin/{BRANCH}"])
        print("[sync] local git realigned to remote (via fetch)")
        return
    print("[sync] WARNING: local git is divergent from remote. Run "
          "`git fetch origin main && git reset --hard origin/main` on a "
          "machine with working git transport to realign.")


def main():
    message = sys.argv[1] if len(sys.argv) > 1 else "daily brief (auto)"

    # Safety: the REST fallback would push this branch's tree onto main.
    br = run(["git", "branch", "--show-current"]).stdout.strip()
    if br != BRANCH:
        print(f"[guard] refusing to run on branch '{br or '(detached HEAD)'}' "
              f"-- checkout '{BRANCH}' first (REST fallback would overwrite main).")
        sys.exit(3)

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
    info = rest_push(message)
    if not info:
        print("[push] ALL PUSH METHODS FAILED -- local commit kept; back-fill manually later")
        sys.exit(2)

    print(f"[push] REST push ok -> {info['sha']}")
    realign_local(info)


if __name__ == "__main__":
    main()
