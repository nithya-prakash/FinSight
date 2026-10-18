"""Record the dashboard walk-through used in the README (docs/demo.mp4 and docs/demo.gif).

Requirements (not project dependencies): `pip install playwright`, Google Chrome installed (Playwright drives it via channel="chrome", so no browser
download), and ffmpeg. Frames are captured as screenshots and stitched by ffmpeg, so Playwright's own video encoder is not needed. The stack must be running with data (`make demo`). The script only reads the dashboard and opens one investigation draft;
it never records an analyst decision.

    python scripts/record_demo.py --base http://localhost:3001 --out docs
"""
import argparse
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OVERLAY = """
(() => {
  if (window.__demo) return; window.__demo = true;
  const bar = document.createElement('div');
  bar.id = '__cap';
  bar.style.cssText = 'position:fixed;left:0;right:0;bottom:0;z-index:99999;padding:14px 22px;font:600 19px system-ui,sans-serif;color:#fff;background:rgba(15,23,32,.92);line-height:1.35;pointer-events:none;';
  const dot = document.createElement('div');
  dot.style.cssText = 'position:fixed;width:18px;height:18px;border-radius:50%;background:rgba(235,64,52,.75);border:2px solid #fff;z-index:100000;pointer-events:none;left:-40px;top:-40px;transform:translate(-50%,-50%);';
  document.addEventListener('DOMContentLoaded', () => { document.body.append(bar, dot); });
  if (document.body) document.body.append(bar, dot);
  document.addEventListener('mousemove', e => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; }, true);
})();
"""


FRAMES: list[tuple[float, Path]] = []
FRAME_DIR = Path(tempfile.mkdtemp())


def hold(page: Page, ms: int) -> None:
    """Wait while capturing frames (real-time video is rebuilt from the capture timestamps)."""
    end = time.monotonic() + ms / 1000
    while True:
        f = FRAME_DIR / f"f{len(FRAMES):05d}.png"
        page.screenshot(path=str(f))
        FRAMES.append((time.monotonic(), f))
        if time.monotonic() >= end:
            break


def caption(page: Page, text: str) -> None:
    page.evaluate("t => { const b = document.getElementById('__cap'); if (b) b.textContent = t; }", text)


def scroll(page: Page, total: int, steps: int = 6, pause: int = 450) -> None:
    for _ in range(steps):
        page.mouse.wheel(0, total // steps)
        hold(page, pause)


def goto_nav(page: Page, name: str) -> None:
    link = page.get_by_role("link", name=name, exact=True)
    link.hover()
    hold(page, 300)
    link.click()
    page.wait_for_load_state("networkidle")


def run(base: str, out: Path) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 720}, color_scheme="light")
        ctx.add_init_script(OVERLAY)
        page = ctx.new_page()

        page.goto(base + "/")
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("text=Recent alerts")
        caption(page, "FinSight overview: KPIs come live from the API. All data is synthetic; 'fraud rate' uses synthetic labels and is not the flagged rate.")
        page.mouse.move(640, 300)
        hold(page, 4500)
        scroll(page, 360, 3)
        caption(page, "Pipeline and model health: events processed, dead letters, scoring time, scoring mode, last drift check.")
        hold(page, 3000)

        goto_nav(page, "Transactions")
        page.wait_for_selector("tbody tr")
        caption(page, "Live transactions streamed through Redpanda and scored on arrival. Identifiers are pseudonymised.")
        hold(page, 2500)
        page.get_by_label("Min risk (0–1)").fill("0.6")
        page.get_by_role("button", name="Apply").click()
        caption(page, "Server-side filters: risk >= 0.6, with the rules that fired and the model version.")
        hold(page, 3500)

        goto_nav(page, "Alerts")
        page.wait_for_selector("tbody tr")
        caption(page, "Fraud alerts: a signal for review, not a confirmed fraud.")
        hold(page, 2000)
        page.locator("tbody tr").first.click()
        page.wait_for_selector("text=Triggered signals")
        caption(page, "Evidence behind the alert: triggered signals, model score and the transaction facts.")
        hold(page, 3500)
        page.get_by_role("button", name="Open investigation").click()
        page.wait_for_selector("text=Educational SAR-style draft", timeout=60000)
        caption(page, "Investigation draft: database facts, model outputs and relationships are kept apart. No LLM key here, so no agent narrative is invented.")
        hold(page, 3500)
        scroll(page, 1500, 5, 700)
        caption(page, "Evidence timeline, exposure computed in SQL, missing information and limitations.")
        hold(page, 2500)
        decision = page.locator("form[aria-label='Analyst decision'] select")
        decision.evaluate("e => e.scrollIntoView({block: 'center'})")  # keep it clear of the caption bar
        hold(page, 800)
        decision.select_option("escalate")
        hold(page, 1200)
        page.get_by_role("button", name="Record decision").click()
        caption(page, "Human approval: a decision needs a second confirming click, and is recorded under the server-side key identity. (Not confirmed in this demo.)")
        hold(page, 4500)

        goto_nav(page, "AI analyst")
        page.wait_for_selector("text=Reference queries")
        caption(page, "AI analyst: without an API key the page says live LLM access is unavailable instead of inventing answers.")
        hold(page, 3500)
        page.get_by_role("button", name="How many transactions and how much value per channel?").click()
        page.wait_for_selector("text=SQL (validated, read-only, row-limited)")
        caption(page, "Labelled reference query: SQL passes the policy, runs on a read-only role, and returns a chart plus the table.")
        hold(page, 3000)
        scroll(page, 700, 4, 600)
        hold(page, 1500)

        goto_nav(page, "Analytics")
        page.wait_for_selector("text=Daily trend")
        caption(page, "Analytics: trends, fraud exposure by merchant category, channels and false positives per rule.")
        hold(page, 3500)
        scroll(page, 700, 4, 600)
        hold(page, 1500)

        goto_nav(page, "Model ops")
        page.wait_for_selector("text=Model operations")
        caption(page, "Model operations: candidates are never promoted automatically; drift and promotion history are tracked.")
        hold(page, 4000)

        goto_nav(page, "Operations")
        page.wait_for_selector("text=Pipeline & observability")
        caption(page, "Pipeline and observability: service health, broker lag, throughput, scoring latency, AI cost estimate (labelled as an estimate).")
        hold(page, 4000)
        scroll(page, 700, 4, 600)
        hold(page, 1500)

        ctx.close()
        browser.close()

    out.mkdir(parents=True, exist_ok=True)
    mp4, gif = out / "demo.mp4", out / "demo.gif"
    listing = FRAME_DIR / "frames.txt"
    with listing.open("w") as fh:
        for i, (t, f) in enumerate(FRAMES):
            dur = (FRAMES[i + 1][0] - t) if i + 1 < len(FRAMES) else 1.5
            fh.write(f"file '{f}'\nduration {max(dur, 0.04):.3f}\n")
        fh.write(f"file '{FRAMES[-1][1]}'\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-vf", "fps=25,format=yuv420p",
                    "-c:v", "libx264", "-crf", "27", "-preset", "slow", "-movflags", "+faststart", str(mp4)], check=True)
    vf = "fps=8,scale=960:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=96:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4), "-vf", vf, "-loop", "0", str(gif)], check=True)
    shutil.rmtree(FRAME_DIR, ignore_errors=True)
    print(f"wrote {mp4} ({mp4.stat().st_size // 1024} KB) and {gif} ({gif.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:3001")
    ap.add_argument("--out", default="docs")
    a = ap.parse_args()
    run(a.base.rstrip("/"), Path(a.out))
