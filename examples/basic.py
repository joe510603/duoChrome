"""basic.py — programmatic usage example.

Opens 2 independent Chromium windows (different cookies / cache), each on its
own profile. Demonstrates the core duoChrome promise: same site, different
accounts, simultaneously.

Note: Playwright's sync API uses a per-process asyncio loop, so calling
`dc.launch()` twice in one process blows up on the 2nd call. `launch_group()`
solves this by spawning one detached subprocess per profile.
"""
from __future__ import annotations

from duochrome import DuoChrome


def main() -> None:
    dc = DuoChrome()
    dc.init()

    names = ["demo-account-a", "demo-account-b"]
    for name in names:
        if not dc.store.exists(name):
            dc.create(name, group="demo", notes="basic.py demo")

    # Launch all in group — each profile gets its own Chromium + isolated user_data_dir
    pids = dc.launch_group("demo", headless=False)
    print(f"\nLaunched {len(pids)} Chrome window(s).")
    print(f"  Stop with: duochrome stop --all")
    print(f"  Or kill wrapper PIDs: {pids}")
    print("(this parent process exits immediately — the wrappers run detached)")


if __name__ == "__main__":
    main()