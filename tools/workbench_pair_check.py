"""Local UI ZIP missing/complete regression, without model requests."""

import hashlib
import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    root = Path(__file__).resolve().parents[1]
    expected = [
        "615ce9a58ac224feda6213278c66fbdc2d9fb91a37aa8e1a75b8877c4db9df44",
        "87b835f38a58bb2d28d6d655617e088ac027a625d639220d2ca42fb8eef44803",
    ]
    by_sha = {
        hashlib.sha256(p.read_bytes()).hexdigest(): p for p in (root / "samples").glob("*.zip")
    }
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto("http://127.0.0.1:8765/")
        page.wait_for_function(
            "()=>document.getElementById('message').textContent.includes('就绪')"
        )
        results = []
        for sha in expected:
            with page.expect_response(
                lambda r: r.url.endswith("/api/import") and r.request.method == "POST"
            ) as response:
                page.locator("#upload").set_input_files(str(by_sha[sha]))
            task_id = response.value.json()["taskId"]
            deadline = time.monotonic() + 30
            while True:
                task = page.request.get(f"http://127.0.0.1:8765/api/task/{task_id}").json()
                if task.get("state") not in {"queued", "running"}:
                    assert "error" not in task or task["error"] is None, task
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("pair import did not finish")
                page.wait_for_timeout(100)
            jobs = page.evaluate("async()=> (await fetch('/api/tasks')).json()")
            job = next(j for j in jobs if j["taskId"] == task_id)
            assert job["state"] == "completed", job
            results.append(job["items"][0])
        assert results[0]["documentId"] == results[1]["documentId"]
        assert results[0]["revisionId"] != results[1]["revisionId"]
        assert results[1]["completeness"]["available"] > results[0]["completeness"]["available"]
        assert results[1]["currentRevisionId"] == results[1]["revisionId"]
        page.get_by_role("button", name=results[1]["title"], exact=False).first.click()
        page.wait_for_function("()=>document.getElementById('revision').options.length === 2")
        page.locator("#revision").select_option(results[0]["revisionId"])
        page.wait_for_function(
            "()=>document.querySelector('#body').textContent.includes('[缺失／不支持图片]')"
        )
        page.screenshot(path=str(root / ".work/round14/browser/pair-history.png"), full_page=True)
        assert all(hashlib.sha256(by_sha[s].read_bytes()).hexdigest() == s for s in expected)
        (root / ".work/round14/browser/pair-check.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(results, ensure_ascii=True))
        browser.close()


if __name__ == "__main__":
    main()
