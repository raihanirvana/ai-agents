"""Browser-only acceptance. Report stdout belongs to this separate runner.

No candidate import, repo tests or candidate-supplied report is used here.
Each case opens a fresh browser context and permits only the pinned endpoint.
"""
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def menu(page):
    expect(page.get_by_role("button", name="Add cappuccino", exact=True)).to_be_visible()
    page.get_by_role("button", name="Add cappuccino", exact=True).click()
    expect(page.get_by_test_id("cart-total")).to_have_text(re.compile(r"^(?:Total:\s*)?\$?3\.50$"))


def quantity(page):
    for _ in range(2):
        page.get_by_role("button", name="Add latte", exact=True).click()
    expect(page.get_by_test_id("cart-latte")).to_contain_text(re.compile(r"\b2\b"))
    expect(page.get_by_test_id("cart-total")).to_have_text(re.compile(r"^(?:Total:\s*)?\$?8\.00$"))


def mixed(page):
    for name in ("latte", "latte", "cappuccino", "espresso"):
        page.get_by_role("button", name="Add " + name, exact=True).click()
    expect(page.get_by_test_id("cart-total")).to_have_text(re.compile(r"^(?:Total:\s*)?\$?14\.00$"))


def remove(page):
    page.get_by_role("button", name="Add latte", exact=True).click()
    page.get_by_role("button", name="Add latte", exact=True).click()
    page.get_by_role("button", name="Remove latte", exact=True).click()
    expect(page.get_by_test_id("cart-total")).to_have_text(re.compile(r"^(?:Total:\s*)?\$?4\.00$"))
    page.get_by_role("button", name="Remove latte", exact=True).click()
    expect(page.get_by_test_id("cart-latte")).to_have_count(0)
    expect(page.get_by_test_id("cart-total")).to_have_text(re.compile(r"^(?:Total:\s*)?\$?0\.00$"))


CASES = {"menu-cappuccino": menu, "cart-add-quantity": quantity,
         "cart-mixed-total": mixed, "cart-remove": remove}


def main():
    invocation, target_id, suite_digest, url = sys.argv[1:]
    origin = urlsplit(url)
    mandatory = json.loads(Path("/suite/mandatory.json").read_text())
    records = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        for case_id, case in CASES.items():
            started = time.monotonic()
            context = browser.new_context(service_workers="block")
            context.route("**/*", lambda route: route.continue_() if
                (urlsplit(route.request.url).scheme, urlsplit(route.request.url).netloc) ==
                (origin.scheme, origin.netloc) else route.abort())
            page = context.new_page()
            page.set_default_timeout(4000)
            try:
                page.goto(url, wait_until="networkidle", timeout=15000)
                case(page)
                record = {"id": case_id, "status": "passed"}
            except Exception as exc:
                record = {"id": case_id, "status": "failed", "error": str(exc)[:3000]}
            finally:
                context.close()
            record.update(duration_s=round(time.monotonic() - started, 3), uac=mandatory["mandatory"][case_id])
            records.append(record)
        browser.close()
    report = {"schema": 1, "invocation_id": invocation, "target_id": target_id,
              "suite_digest": suite_digest, "discovered": len(CASES), "executed": len(records),
              "passed": sum(r["status"] == "passed" for r in records),
              "failed": sum(r["status"] == "failed" for r in records), "skipped": 0, "tests": records}
    print("DEV006_REPORT " + json.dumps(report))
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
