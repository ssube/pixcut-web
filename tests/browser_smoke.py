"""Optional real-browser acceptance check: python tests/browser_smoke.py.

Requires Playwright + Chromium in the invoking Python environment and a built web/dist.
Uses a temporary data directory, loopback server and simulator worker only.
"""

import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import urllib.request
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
AXE = ROOT / "web/node_modules/axe-core/axe.min.js"


def assert_accessible(page):
    page.add_script_tag(path=AXE)
    violations = page.evaluate(
        """async () => (await axe.run(document, {
          resultTypes: ['violations']
        })).violations.filter(item => ['serious', 'critical'].includes(item.impact))"""
    )
    assert not violations, [
        {
            "id": item["id"],
            "help": item["help"],
            "nodes": [node["target"] for node in item["nodes"]],
        }
        for item in violations
    ]


with tempfile.TemporaryDirectory(prefix="pixcut-browser-") as tmp:
    env = {**os.environ, "PIXCUT_DATA_DIR": tmp}
    env.pop("PIXCUT_API_TOKEN", None)
    cli = str(ROOT / ".venv/bin/pixcut")
    subprocess.run([cli, "migrate"], cwd=ROOT, env=env, check=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    logfile = open(Path(tmp) / "server.log", "w")
    server = subprocess.Popen(
        [cli, "serve", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=logfile,
        stderr=logfile,
    )
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(origin + "/api/v1/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Server did not start")
        art = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
        draw = ImageDraw.Draw(art)
        draw.rounded_rectangle((15, 15, 185, 285), radius=35, fill="#f4ba63")
        draw.ellipse((55, 65, 95, 105), fill="#205b49")
        draw.ellipse((120, 65, 160, 105), fill="#205b49")
        draw.arc((55, 90, 155, 190), 0, 180, fill="#205b49", width=8)
        source = Path(tmp) / "Sunshine.png"
        art.save(source)
        sticker_art = Image.new("RGBA", (320, 180), (0, 0, 0, 0))
        sticker_draw = ImageDraw.Draw(sticker_art)
        sticker_draw.rounded_rectangle(
            (15, 20, 125, 160), radius=25, fill="#f4ba63"
        )
        sticker_draw.ellipse((190, 20, 300, 160), fill="#205b49")
        sticker_source = Path(tmp) / "Two stickers.png"
        sticker_art.save(sticker_source)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1050})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(origin)
            page.get_by_role(
                "button", name="Turn on pumpkin spice mode", exact=True
            ).click()
            assert page.locator("html").get_attribute("data-theme") == "pumpkin-spice"
            assert (
                page.locator('meta[name="theme-color"]').get_attribute("content")
                == "#a94620"
            )
            page.reload()
            page.get_by_role(
                "button", name="Turn off pumpkin spice mode", exact=True
            ).wait_for()
            assert page.locator("html").get_attribute("data-theme") == "pumpkin-spice"
            # Photo is the default workspace. Exercise local edits, explicit save,
            # and exact proofing without submitting hardware in simulator mode.
            page.get_by_label("Import a photo", exact=True).set_input_files(str(source))
            page.get_by_role("heading", name="Sunshine.png", exact=True).wait_for()
            page.get_by_role("button", name="Photo booth").click()
            page.get_by_text("Repeat layouts use portrait 4×6 paper.").wait_for()
            page.get_by_label("Exposure", exact=True).fill("0.4")
            page.get_by_label("Print quality", exact=True).fill("90")
            page.get_by_text("Unsaved changes", exact=True).wait_for()
            page.get_by_role("button", name="Save changes", exact=True).click()
            page.get_by_role("button", name="Review print").click()
            page.get_by_text("Print-ready 4×6 photo", exact=True).wait_for()
            page.get_by_text("Quality 90", exact=False).wait_for()
            assert page.get_by_role("button", name="Printer not connected").is_disabled()
            page.get_by_role("button", name="Show original", exact=True).click()
            assert page.get_by_role(
                "button", name="Show edits", exact=True
            ).get_attribute("aria-pressed") == "true"
            page.get_by_text("Fine-tune crop", exact=True).click()
            page.get_by_role("button", name="Move left", exact=True).click()
            page.get_by_label("Exposure", exact=True).fill("0.5")
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.get_by_role("button", name="Sticker", exact=True).click()
            page.get_by_role("heading", name="Sunshine.png", exact=True).wait_for()
            assert_accessible(page)
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Sticker", exact=True).click()

            page.get_by_label("Import an image", exact=True).set_input_files(
                str(sticker_source)
            )
            page.get_by_text("Layout locked", exact=True).wait_for()
            page.get_by_role("button", name="Restore imported size", exact=True).click()
            page.get_by_text(
                "Imported size restored from the original image. Check the cut lines before printing.",
                exact=True,
            ).wait_for()
            page.get_by_role("button", name="Unlock layout", exact=True).click()
            page.get_by_text(
                "Layout unlocked. You can now rearrange the stickers on this sheet.",
                exact=True,
            ).wait_for()
            page.get_by_role("button", name="Arrange at this size").wait_for(
                state="visible"
            )
            page.wait_for_function("!document.querySelector('fieldset').disabled")
            page.get_by_role("button", name="Arrange at this size").click()
            usage = page.get_by_role("region", name="Sheet material use")
            usage.get_by_text("Sticker area", exact=True).wait_for()
            usage.get_by_text("Unused paper", exact=True).wait_for()
            usage.get_by_text(
                "Estimated from the cut areas at $0.60 per sheet.", exact=True
            ).wait_for()
            page.get_by_role("button", name="Review print").wait_for()
            page.wait_for_function(
                "Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('Review print')).disabled === false"
            )
            page.get_by_role("button", name="Review print").click()
            page.get_by_text("Ready to preview").wait_for()
            page.get_by_alt_text("Sticker sheet print preview").wait_for()
            page.screenshot(path="/tmp/pixcut-studio-desktop.png", full_page=True)
            page.get_by_label("I checked every sheet.").check()
            page.get_by_role("button", name="Save preview to history").click()
            page.get_by_role("heading", name="Print history", exact=True).wait_for()
            assert_accessible(page)
            subprocess.run([cli, "worker", "--once"], cwd=ROOT, env=env, check=True)
            page.get_by_role("button", name="Refresh", exact=True).click()
            page.get_by_text("1 of 1 sheet finished").wait_for()
            page.reload()
            page.get_by_role("button", name="History", exact=True).click()
            page.get_by_role("button", name="Open print").click()
            page.get_by_text("Saved print", exact=True).wait_for()
            page.get_by_label("I checked every sheet.").check()
            page.get_by_role("button", name="Print again", exact=True).click()
            page.get_by_role("button", name="Open print").first.wait_for()
            assert page.get_by_role("button", name="Open print").count() == 2
            page.get_by_role("button", name="Open print").last.click()
            page.get_by_text("Saved print", exact=True).wait_for()
            # All export inputs and modules are now loaded; block the network.
            page.route("**/*", lambda route: route.abort())
            page.get_by_label(
                "Add a centered magnet pocket to the back of each STL part",
                exact=True,
            ).check()
            page.get_by_label("Solid thickness · mm", exact=True).fill("2")
            page.get_by_label("Magnet diameter · mm", exact=True).fill("6")
            page.get_by_label("Pocket depth · mm", exact=True).fill("3")
            page.get_by_text("The pocket reaches through the part.", exact=True).wait_for()
            for kind in ["SVG", "DXF", "STL"]:
                with page.expect_download() as download:
                    page.get_by_role("button", name=kind, exact=True).click()
                path = Path(tmp) / download.value.suggested_filename
                download.value.save_as(path)
                assert path.stat().st_size > 100
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path="/tmp/pixcut-studio-mobile.png", full_page=True)
            assert page.evaluate(
                "document.documentElement.scrollWidth <= window.innerWidth"
            )
            assert not errors, errors
            browser.close()
        print(
            "Browser smoke passed: photo edit/save/exact proof → sticker upload/layout/render/simulate → restart history → exact reprint → offline SVG/DXF/magnet-pocket STL; mobile has no horizontal overflow."
        )
    finally:
        server.terminate()
        server.wait(timeout=10)
        logfile.close()
