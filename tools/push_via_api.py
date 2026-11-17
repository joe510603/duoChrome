#!/usr/bin/env python3
"""Push to GitHub through the REST API when github.com:443 is unreachable.

Why this exists
---------------
On some networks `github.com` is blocked while `api.github.com` still
works. Then `git push` / `git fetch` hang and time out, even though the
repository is perfectly reachable through the API.

This script pushes the current branch with the Git Data API:

    blobs  -> trees -> commits -> refs

and keeps the local history byte-identical to the remote one, so no fetch
is ever needed. A commit's identity is

    tree + parents + message + author + committer

so if we hand GitHub the same values, it reproduces the same sha. The one
gotcha: the API appends its own trailing newline to the message, so the
message must be passed *without* its final newline.

Usage
-----
    python3 tools/push_via_api.py                 # push current branch
    python3 tools/push_via_api.py --tag v1.2.3 -m "release notes"
    python3 tools/push_via_api.py --align         # re-sync local refs only

Credentials come from git's own credential helper
(`git credential fill`), i.e. the same token `git push` would use.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

REPO = os.environ.get("DUOCHROME_GH_REPO", "joe510603/duoChrome")
API = f"https://api.github.com/repos/{REPO}"
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- plumbing ----------

def token() -> str:
    out = subprocess.check_output(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        text=True,
    )
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("no credential from `git credential fill`")


TOK = token()


def api(method: str, path: str, payload=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {TOK}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "duoChrome-push",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"GitHub API {method} {path} failed: {e.code} {e.read().decode()}\n")
        raise
    return json.loads(body) if body else {}


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", REPO_DIR, *args], text=True).strip()


def obj_sha(kind: str, body: bytes) -> str:
    """git object id = sha1("<type> <len>\\0" + body)"""
    return hashlib.sha1(f"{kind} {len(body)}\0".encode() + body).hexdigest()


def epoch_of(iso: str) -> int:
    return int(
        datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


# ---------- align local <-> remote ----------

def align_local(remote_sha: str) -> None:
    """Rebuild the remote commit object locally so both refs agree.

    GitHub's API does not expose the raw timezone offset of a commit, so we
    brute-force the plausible values until the object hash matches.
    """
    c = api("GET", f"/git/commits/{remote_sha}")
    tree = c["tree"]["sha"]
    parents = [p["sha"] for p in c["parents"]]
    message = c["message"]
    a_name, a_email = c["author"]["name"], c["author"]["email"]
    c_name, c_email = c["committer"]["name"], c["committer"]["email"]
    a_epoch, c_epoch = epoch_of(c["author"]["date"]), epoch_of(c["committer"]["date"])

    head = f"tree {tree}\n" + "".join(f"parent {p}\n" for p in parents)
    tzs = [
        f"{'+' if h >= 0 else '-'}{abs(h):02d}{m:02d}"
        for h in range(-12, 15)
        for m in (0, 15, 30, 45)
    ]
    # The API's message field is already trimmed; the stored object may carry
    # one or two trailing newlines depending on how it was created.
    for msg in (
        message,
        message + "\n",
        message + "\n\n",
        message.rstrip("\n") + "\n",
        message.rstrip("\n"),
    ):
        for ae in (a_epoch - 1, a_epoch, a_epoch + 1):
            for atz in tzs:
                for ce in (c_epoch - 1, c_epoch, c_epoch + 1):
                    for ctz in tzs:
                        body = (
                            head
                            + f"author {a_name} <{a_email}> {ae} {atz}\n"
                            + f"committer {c_name} <{c_email}> {ce} {ctz}\n\n"
                            + msg
                        ).encode("utf-8")
                        if obj_sha("commit", body) == remote_sha:
                            p = subprocess.run(
                                ["git", "-C", REPO_DIR, "hash-object", "-w", "-t", "commit", "--stdin"],
                                input=body, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            )
                            if p.returncode != 0:
                                print("git refused the object:", p.stderr.decode())
                                return
                            subprocess.run(
                                ["git", "-C", REPO_DIR, "update-ref", "refs/heads/main", remote_sha],
                                check=True,
                            )
                            subprocess.run(
                                ["git", "-C", REPO_DIR, "reset", "--mixed", remote_sha],
                                check=True, stdout=subprocess.DEVNULL,
                            )
                            print(f"✓ local main aligned to {remote_sha}")
                            return
    print(f"⚠ could not reproduce {remote_sha} locally — remote still has the commit, local doesn't")


# ---------- push ----------

def push_branch(branch: str) -> int:
    head = git("rev-parse", "HEAD")
    parent = git("rev-parse", "HEAD^")
    tree = git("rev-parse", "HEAD^{tree}")
    # The API appends its own trailing newline -> strip ours.
    message = git("log", "-1", "--format=%B").rstrip("\n")
    author = {
        "name": git("log", "-1", "--format=%an"),
        "email": git("log", "-1", "--format=%ae"),
        "date": git("log", "-1", "--format=%aI"),
    }
    committer = {
        "name": git("log", "-1", "--format=%cn"),
        "email": git("log", "-1", "--format=%ce"),
        "date": git("log", "-1", "--format=%cI"),
    }

    remote = api("GET", f"/git/ref/heads/{branch}")["object"]["sha"]
    print(f"local  {head}")
    print(f"remote {remote}")

    if remote == head:
        print("✓ already up to date")
        return 0
    if remote != parent:
        print(f"✗ remote has commits we don't have ({remote} != {parent}) — refusing to overwrite")
        return 2

    remote_tree = api("GET", f"/git/commits/{remote}")["tree"]["sha"]
    if remote_tree != tree:
        changed = git("diff", "--name-only", parent, head).splitlines()
        entries = []
        for path in changed:
            with open(os.path.join(REPO_DIR, path), "rb") as fp:
                content = fp.read()
            import base64

            blob = api(
                "POST", "/git/blobs",
                {"content": base64.b64encode(content).decode(), "encoding": "base64"},
            )
            entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
            print(f"   blob {path}")
        tree = api("POST", "/git/trees", {"base_tree": remote_tree, "tree": entries})["sha"]

    new_commit = api(
        "POST", "/git/commits",
        {"message": message, "tree": tree, "parents": [parent],
         "author": author, "committer": committer},
    )["sha"]
    api("PATCH", f"/git/refs/heads/{branch}", {"sha": new_commit, "force": False})
    print(f"✓ remote {branch} = {new_commit}")
    align_local(new_commit)
    return 0


def create_tag(tag: str, message: str) -> int:
    commit = git("rev-parse", "HEAD")
    obj = api("POST", "/git/tags",
              {"tag": tag, "message": message, "object": commit, "type": "commit"})
    api("POST", "/git/refs", {"ref": f"refs/tags/{tag}", "sha": obj["sha"]})
    print(f"✓ refs/tags/{tag} → {obj['sha']} (commit {commit})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--tag")
    ap.add_argument("-m", "--message", default="")
    ap.add_argument("--align", action="store_true", help="only re-sync local refs")
    args = ap.parse_args()

    if args.align:
        remote = api("GET", f"/git/ref/heads/{args.branch}")["object"]["sha"]
        align_local(remote)
        return 0
    if args.tag:
        return create_tag(args.tag, args.message or args.tag)
    return push_branch(args.branch)


if __name__ == "__main__":
    sys.exit(main())
