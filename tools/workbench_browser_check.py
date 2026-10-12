"""Explicit local browser acceptance; never selects uncached images or requests a cloud model."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--inspect-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    evidence = root / ".work/round14/browser"
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto("http://127.0.0.1:8765/")
        page.wait_for_function(
            "()=>document.getElementById('message').textContent.includes('就绪')"
        )
        if not args.resume and not args.inspect_only:
            page.locator("#upload").set_input_files(str(root / "samples/html1.zip"))
        page.wait_for_function("()=>document.querySelectorAll('#documents button').length > 0")
        page.locator("#documents button").first.click()
        page.wait_for_function("()=>document.querySelectorAll('#pictures .group').length > 0")
        doc = page.evaluate(
            "async()=>{const id=JSON.parse(sessionStorage.documentSelection).id;return (await fetch('/api/document?doc='+id)).json()}"
        )
        available = [g for g in doc["groups"] if g["cache"] == "available" and not g["localOnly"]]
        if not available:
            raise RuntimeError(
                "No eligible historical compact cache; this check refuses cloud calls"
            )
        target = next(
            (g for g in available if g["image"]["sha256"].startswith("50933b2e")), available[0]
        )
        checkbox = page.locator(f"#pictures input[value='{target['key']}']")
        group = checkbox.locator("xpath=ancestor::div[contains(@class,'group')]")
        group.get_by_role("button", name="查看结果／审核").click()
        page.wait_for_selector("#saveHuman")
        page.locator("#review").screenshot(path=str(evidence / "review-panel.png"))
        page.screenshot(
            path=str(evidence / ("restart.png" if args.resume else "document.png")), full_page=True
        )
        if not args.resume and not args.inspect_only:
            checkbox.check()
            page.locator("#consent").check()
            page.locator("#enrich").click()
            page.wait_for_function(
                "()=>document.getElementById('message').textContent.includes('任务结束')"
            )
            page.locator(f"#pictures input[value='{target['key']}']").locator("..").locator(
                ".."
            ).get_by_role("button", name="查看结果／审核").click()
            page.wait_for_selector("#saveHuman")
            page.once(
                "dialog",
                lambda dialog: dialog.accept("Codex仅功能验证，非用户认可；内容需进一步核对"),
            )
            page.locator("[data-review='needs_review']").click()
            page.wait_for_function(
                "()=>document.querySelector('#review h3').textContent.includes('needs_review')"
            )
            original = page.locator("#editDescription").input_value()
            page.locator("#editDescription").fill(
                original + "\n[Codex功能验证草稿，不代表用户验收]"
            )
            page.locator("#saveHuman").click()
            page.wait_for_function(
                "()=>document.querySelector('#humanVersions').options.length > 0"
            )
            page.locator("#adoptHuman").click()
            page.wait_for_function(
                "()=>document.querySelector('#review').textContent.includes('正在展示人工采用版本')"
            )
            page.reload()
            page.wait_for_function("()=>document.querySelectorAll('#pictures .group').length > 0")
            page.locator(f"#pictures input[value='{target['key']}']").locator("..").locator(
                ".."
            ).get_by_role("button", name="查看结果／审核").click()
        page.wait_for_function(
            "()=>document.querySelector('#review').textContent.includes('正在展示人工采用版本')"
        )
        assert "Codex功能验证草稿" in page.locator("#editDescription").input_value()
        if args.resume and not args.inspect_only:
            page.locator(f"#pictures input[value='{target['key']}']").check()
            page.locator("#consent").check()
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#rerun").click()
            page.wait_for_function(
                "()=>document.getElementById('message').textContent.includes('任务结束')"
            )
        tasks = page.evaluate("async()=> (await fetch('/api/tasks')).json()")
        calls = sum(i.get("metrics", {}).get("apiCalls", 0) for t in tasks for i in t["items"])
        assert calls == 0, "Acceptance must only use historical cache"
        assert not errors, errors
        page.screenshot(
            path=str(evidence / ("restart-cache.png" if args.resume else "review.png")),
            full_page=True,
        )
        report = {
            "resume": args.resume,
            "browser": browser.version,
            "imageKey": target["key"],
            "documentId": doc["document"]["documentId"],
            "apiCalls": calls,
            "humanActor": "codex_test",
            "userAcceptance": "pending",
            "pageErrors": errors,
            "tasks": [
                {"taskId": t["taskId"], "state": t["state"], "operation": t["operation"]}
                for t in tasks
            ],
        }
        (
            evidence
            / (
                "inspect-check.json"
                if args.inspect_only
                else "restart-check.json"
                if args.resume
                else "first-check.json"
            )
        ).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        browser.close()


if __name__ == "__main__":
    main()
