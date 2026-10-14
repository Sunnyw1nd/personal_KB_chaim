"""Real local browser operations with an explicitly fake provider; zero cloud requests.

Writes fresh evidence under .work. No originals, workbooks or historical runs are edited.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path
from zipfile import ZipFile

from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from wechat_html_normalizer.application.workbench import Workbench
from wechat_html_normalizer.enrichment.compact_provider import CompactResponse
from wechat_html_normalizer.enrichment.models import Usage
from wechat_html_normalizer.enrichment.providers import DEFAULT_MODEL
from wechat_html_normalizer.media import sha256_file
from wechat_html_normalizer.workbench.server import create_app


class ExplicitFake:
    def __init__(self):
        self.calls = 0

    def generate(self, request, image, prompt, **kwargs):
        self.calls += 1
        content = json.dumps(
            {
                "imageType": "architecture_flow",
                "knowledgeValue": "medium",
                "description": "[fake-provider：只验证界面及持久化，不代表识图事实]",
                "keyFacts": ["模拟事实"],
                "uncertainties": [],
            },
            ensure_ascii=False,
        )
        return CompactResponse(
            content,
            DEFAULT_MODEL,
            "round15-fake",
            "stop",
            Usage(inputTokens=1, outputTokens=1, totalTokens=2),
            {"content": content, "model": DEFAULT_MODEL, "finishReason": "stop"},
        )


def main():
    root = Path(__file__).resolve().parents[1]
    evidence = root / ".work/round15" / ("browser-" + uuid.uuid4().hex[:6])
    inputs = evidence / "inputs"
    inputs.mkdir(parents=True)
    text = inputs / "中文知识.txt"
    text.write_text("# 这是普通文本\n  保留缩进\n\n中文第二段", encoding="utf-8-sig")
    changed = inputs / "中文知识新版.txt"
    changed.write_text("# 这是普通文本\n  保留缩进\n\n中文第二段，更新后的内容", encoding="utf-8")
    known_image = root / "test_pictures/asset-ce52ad0983de.webp"
    # Only read this existing non-sensitive decorative sample. It is not sent to any provider.
    standalone = inputs / known_image.name
    standalone.write_bytes(known_image.read_bytes())
    updated_image = inputs / "独立图片新版.png"
    Image.new("RGB", (48, 32), "blue").save(updated_image)
    image = inputs / "image.png"
    Image.new("RGB", (80, 48), "green").save(image)
    md = "# Markdown本地验收\n\n正文**加粗**和`代码`。\n\n- 父级\n  - 子级\n\n> 引用内容\n\n```python\nif True:\n    print('TEXT')\n```\n\n| 项目 | 数量 |\n| --- | --- |\n| A | 2 |\n\n![已存在图](image.png)\n\n![待补图](missing.png)\n"
    packages = []
    for label, complete in (("缺图", False), ("补图", True)):
        path = inputs / (label + ".zip")
        with ZipFile(path, "w") as zipped:
            zipped.writestr("article.md", md)
            zipped.write(image, "image.png")
            if complete:
                zipped.write(image, "missing.png")
        packages.append(path)
    protected = {
        str(p): sha256_file(p)
        for p in [text, changed, known_image, standalone, updated_image, *packages]
    }
    fake = ExplicitFake()
    port = 8768

    def core():
        return Workbench(
            root,
            data_root=evidence / "data",
            documents_root=evidence / "documents",
            enrichment_root=evidence / "enrichment",
            provider_factory=lambda: fake,
            key_reader=lambda: "fake",
            actor="codex_test",
            cloud_call_cap=0,
        )

    workbench = core()
    # fake execution has no network transport. The cloud_call_cap is changed only for this known fake
    # so the normal runner code path is tested, without loading real credentials or client.
    workbench.cloud_call_cap = None
    server = make_server("127.0.0.1", port, create_app(workbench, port=port), threaded=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors, external = [], []
            page.on("pageerror", lambda err: errors.append(str(err)))

            def route(route):
                if route.request.url.startswith(f"http://127.0.0.1:{port}/"):
                    route.continue_()
                else:
                    external.append(route.request.url)
                    route.abort()

            page.route("**/*", route)
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_function(
                "()=>document.getElementById('message').textContent.includes('就绪')"
            )

            def import_ui(path, *, update=False, primary=""):
                before = len(workbench.tasks())
                page.locator("#mainMarkdown").fill(primary)
                page.locator("#updateUpload" if update else "#upload").set_input_files(str(path))
                # Await actual worker publication, not only the DOM text from a previous operation.
                page.wait_for_function(
                    "n=>document.querySelectorAll('#tasks .notice').length>n", arg=before
                )
                page.wait_for_function("()=>!importing")
                task = workbench.tasks()[-1]
                assert task["state"] == "completed", task
                row = task["items"][0]
                if row.get("documentId"):
                    page.wait_for_function(
                        "v=>{const s=sessionStorage.documentSelection;return s&&JSON.parse(s).id===v[0]&&JSON.parse(s).rev===v[1]}",
                        arg=[row["documentId"], row["currentRevisionId"]],
                    )
                return row

            txt = import_ui(text)
            assert "# 这是普通文本" in page.locator("#body pre").first.inner_text()
            page.locator("#body").screenshot(path=str(evidence / "txt.png"))
            duplicate_txt = import_ui(text)
            assert duplicate_txt["decision"] == "duplicate_candidate"
            same_txt = import_ui(text, update=True)
            assert same_txt["decision"] == "unchanged"
            update_txt = import_ui(changed, update=True)
            assert (
                update_txt["documentId"] == txt["documentId"]
                and update_txt["decision"] == "content_updated"
            )
            assert page.locator("#revision option").count() == 2
            markdown = import_ui(packages[0], primary="article.md")
            assert (
                page.locator("#body table").count() == 1
                and page.locator("#body strong").count() == 1
            )
            assert "print('TEXT')" in page.locator("#body pre").inner_text()
            page.locator("#body").screenshot(path=str(evidence / "md-missing.png"))
            duplicate_md = import_ui(packages[0], primary="article.md")
            assert duplicate_md["decision"] == "duplicate_candidate"
            same_md = import_ui(packages[0], update=True, primary="article.md")
            assert same_md["decision"] == "unchanged"
            completed_md = import_ui(packages[1], update=True, primary="article.md")
            assert (
                completed_md["decision"] == "resource_improved"
                and completed_md["documentId"] == markdown["documentId"]
            )
            page.locator("#body").screenshot(path=str(evidence / "md-complete.png"))
            degraded_md = import_ui(packages[0], update=True, primary="article.md")
            assert (
                degraded_md["decision"] == "resource_degraded"
                and degraded_md["currentRevisionId"] == completed_md["revisionId"]
            )
            # Explicit standalone image new/import/update, with no external requests.
            picture = import_ui(standalone)
            duplicate_picture = import_ui(standalone)
            assert duplicate_picture["decision"] == "duplicate_candidate"
            same_picture = import_ui(standalone, update=True)
            assert same_picture["decision"] == "unchanged"
            assert page.locator("#pictures .group").count() == 1
            page.locator("#pictures input").check()
            page.locator("#consent").check()
            page.locator("#enrich").click()
            page.wait_for_function(
                "()=>document.querySelector('#pictures .group label')?.textContent.includes('available')"
            )
            page.locator("#pictures button").click()
            page.wait_for_selector("#saveHuman")
            page.once("dialog", lambda dialog: dialog.accept("Codex fake功能测试，不代表用户认可"))
            page.locator("[data-review='needs_review']").click()
            page.wait_for_function(
                "()=>document.querySelector('#review h3').textContent.includes('needs_review')"
            )
            page.locator("#editDescription").fill("[Codex fake人工草稿，非用户验收]")
            page.locator("#saveHuman").click()
            page.wait_for_function(
                "()=>document.querySelector('#humanVersions').options.length===1"
            )
            page.locator("#adoptHuman").click()
            page.wait_for_function(
                "()=>document.querySelector('#review').textContent.includes('正在展示人工采用版本')"
            )
            page.locator("#review").screenshot(path=str(evidence / "review.png"))
            page.reload()
            page.wait_for_function("()=>document.querySelectorAll('#pictures .group').length===1")
            page.locator("#pictures button").click()
            page.wait_for_selector("#saveHuman")
            assert "Codex fake人工草稿" in page.locator("#editDescription").input_value()
            # Restart, then explicitly create a second task which must reuse the fake cache.
            server.shutdown()
            server.server_close()
            worker.join()
            workbench.close()
            workbench = core()
            workbench.cloud_call_cap = None
            server = make_server("127.0.0.1", port, create_app(workbench, port=port), threaded=True)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            page.reload()
            page.wait_for_function("()=>document.querySelectorAll('#pictures .group').length===1")
            page.locator("#pictures input").check()
            page.locator("#consent").check()
            page.once("dialog", lambda dialog: dialog.accept())
            page.locator("#rerun").click()
            page.wait_for_function(
                "async()=>{const t=await (await fetch('/api/tasks')).json();return t.at(-1).state==='completed'&&t.at(-1).operation==='enrichment'}"
            )
            assert fake.calls == 1
            update_picture = import_ui(updated_image, update=True)
            assert update_picture["documentId"] == picture["documentId"]
            page.locator("#pictures button").click()
            page.wait_for_function(
                "()=>document.querySelector('#review').textContent.includes('没有已保存结果')"
            )
            assert page.locator("#saveHuman").count() == 0, (
                "old human answer must not cross image/context revision"
            )
            assert all(sha256_file(Path(path)) == sha for path, sha in protected.items())
            assert not errors and not external, (errors, external)
            report = {
                "browser": browser.version,
                "cloudApiCalls": 0,
                "fakeProviderCalls": fake.calls,
                "pageErrors": errors,
                "externalRequests": external,
                "txt": [txt, duplicate_txt, same_txt, update_txt],
                "markdown": [markdown, duplicate_md, same_md, completed_md, degraded_md],
                "image": [picture, duplicate_picture, same_picture, update_picture],
                "userAcceptance": "pending",
                "actor": "codex_test",
                "inputsUnchanged": True,
            }
            (evidence / "validation.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(
                json.dumps(
                    {
                        "evidence": str(evidence),
                        "cloudApiCalls": 0,
                        "fakeProviderCalls": fake.calls,
                        "pageErrors": errors,
                    },
                    ensure_ascii=False,
                )
            )
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
        workbench.close()


if __name__ == "__main__":
    main()
