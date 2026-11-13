"""Capture real screenshots of the running Streamlit reviewer app with headless Microsoft Edge (DevTools Protocol).

    streamlit run app/review_app.py --server.port 8601 --server.headless true    (in another terminal)
    python docs/final_capstone/build/capture_screenshots.py

Each screenshot loads a saved review session produced by an actual run (docs/milestone_5/scenarios/*.json)
and waits until the page shows the expected text before capturing the full page. Uses the `websockets`
client, which Streamlit already installs.
"""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs" / "final_capstone" / "screenshots"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
APP = "http://localhost:8601"
PORT = 9333

SHOTS = [
    ("scenario_A_clear_policy_finalized.png", "docs/milestone_5/scenarios/OPRA-TEST-201-fa2f8610.json", False, "Finalized by"),
    ("scenario_B_tot_reasoning_and_evidence.png", "docs/milestone_5/scenarios/OPRA-TEST-202-70804a4f.json", True, "Branches considered"),
    ("scenario_C_missing_evidence_blocked.png", "docs/milestone_5/scenarios/OPRA-TEST-203-23d4078c.json", False, "Final authorization"),
]


class CDP:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})


async def shoot(cdp: CDP, url: str, wait_text: str, path: Path, width: int = 1500) -> None:
    await cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=1200, deviceScaleFactor=1, mobile=False)
    await cdp.call("Page.navigate", url=url)
    deadline = time.time() + 60
    while time.time() < deadline:
        r = await cdp.call("Runtime.evaluate", expression="document.body ? document.body.innerText : ''", returnByValue=True)
        if wait_text in r["result"].get("value", ""):
            break
        await asyncio.sleep(1)
    else:
        text = r["result"].get("value", "")
        raise RuntimeError(f"timed out waiting for {wait_text!r} at {url}; page text starts: {text[:600]!r}")
    await asyncio.sleep(2)  # let late elements (dataframes) finish rendering
    # Hide the sidebar (reviewer name and request picker) so the main review column is captured unclipped,
    # and measure the main block's height so the page is not padded by the sidebar.
    await cdp.call("Runtime.evaluate", expression=(
        "for (const s of ['[data-testid=stSidebar]','[data-testid=stSidebarCollapsedControl]','header']) "
        "document.querySelectorAll(s).forEach(e => e.style.display = 'none');"))
    await asyncio.sleep(1)
    h = await cdp.call("Runtime.evaluate", returnByValue=True,
                       expression="document.querySelector('[data-testid=stMainBlockContainer]').scrollHeight")
    height = min(int(h["result"]["value"]) + 20, 12000)
    await cdp.call("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=1, mobile=False)
    await asyncio.sleep(1.5)
    shot = await cdp.call("Page.captureScreenshot", format="png", captureBeyondViewport=True)
    path.write_bytes(base64.b64decode(shot["data"]))
    print(f"saved {path.relative_to(ROOT)} ({height}px tall)")


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    profile = Path.home() / "AppData" / "Local" / "Temp" / "edge-cdp-profile"
    edge = subprocess.Popen([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                             f"--user-data-dir={profile}", f"--remote-debugging-port={PORT}", "about:blank"])
    try:
        for _ in range(30):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                break
            except OSError:
                time.sleep(0.5)
        page = next(p for p in pages if p.get("type") == "page")
        ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=200 * 1024 * 1024)
        cdp = CDP(ws)
        await cdp.call("Page.enable")
        for name, session, expand, wait_text in SHOTS:
            url = f"{APP}/?session={session}" + ("&expand=1" if expand else "")
            await shoot(cdp, url, wait_text, OUT / name)
        await ws.close()
    finally:
        edge.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
