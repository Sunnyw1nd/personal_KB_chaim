from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image


def image_bytes(image_format: str = "WEBP", size: tuple[int, int] = (32, 18)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (20, 100, 180)).save(output, format=image_format)
    return output.getvalue()


@pytest.fixture
def saved_article(tmp_path: Path) -> tuple[Path, Path, Path]:
    html_path = tmp_path / "示例文章.html"
    resources = tmp_path / "示例文章_files"
    output = tmp_path / "output"
    resources.mkdir()
    (resources / "640").write_bytes(image_bytes())
    (resources / "qr.png").write_bytes(image_bytes("PNG", (20, 20)))
    (resources / "appmsg.css").write_text("body { color: black; }", encoding="utf-8")
    (resources / "index.js.下载").write_text("throw new Error('must not run');", encoding="utf-8")
    (resources / "frame.html").write_text("<html>aux</html>", encoding="utf-8")
    html_path.write_text(
        """<!doctype html>
<html><head>
  <meta charset="utf-8">
  <meta property="og:url" content="https://mp.weixin.qq.com/s?id=abc">
  <title>fallback title</title>
  <link rel="stylesheet" href="示例文章_files/appmsg.css">
  <script src="示例文章_files/index.js.下载"></script>
</head><body>
  <h1 id="activity-name">标准化示例</h1>
  <span id="js_name">示例公众号</span>
  <span id="js_author_name">张三</span>
  <em id="publish_time">2026年8月10日 17:40</em>
  <img src="示例文章_files/qr.png">
  <iframe src="示例文章_files/frame.html"></iframe>
  <div id="js_content">
    <h3>第一节</h3>
    <p>普通 <strong>加粗</strong> 与 <a href="https://example.com/docs">链接</a>。</p>
    <p style="display: none">不应出现</p>
    <ol><li>第一项</li><li>第二项</li></ol>
    <blockquote>重要引用</blockquote>
    <pre><code class="language-python">print('ok')</code></pre>
    <table><tr><th>列 A</th><th>列 B</th></tr><tr><td>1</td><td>2</td></tr></table>
    <p>图片前文字<img src="示例文章_files/640" data-src="https://mmbiz.qpic.cn/example" data-type="png">图片后文字</p>
  </div>
</body></html>""",
        encoding="utf-8",
    )
    return html_path, resources, output


@pytest.fixture
def zhihu_article(tmp_path: Path) -> tuple[Path, Path, Path]:
    html_path = tmp_path / "知乎示例.html"
    resources = tmp_path / "知乎示例_files"
    output = tmp_path / "output"
    resources.mkdir()
    (resources / "body.jpg").write_bytes(image_bytes("WEBP", (80, 45)))
    (resources / "card.jpg").write_bytes(image_bytes("JPEG", (18, 12)))
    (resources / "poster.jpg").write_bytes(image_bytes("JPEG", (64, 36)))
    (resources / "avatar.jpg").write_bytes(image_bytes("JPEG", (16, 16)))
    (resources / "style.css.下载").write_text(".Post-RichText {}", encoding="utf-8")
    (resources / "bundle.js.下载").write_text("throw new Error('never execute')", encoding="utf-8")
    (resources / "icon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        encoding="utf-8",
    )
    html_path.write_text(
        """<!doctype html>
<html><head>
  <meta charset="utf-8">
  <meta property="og:url" content="https://zhuanlan.zhihu.com/p/123456">
  <meta property="og:title" content="备用标题">
  <link rel="stylesheet" href="知乎示例_files/style.css.下载">
  <script src="知乎示例_files/bundle.js.下载"></script>
</head><body>
  <nav>页面导航 <img src="知乎示例_files/avatar.jpg"></nav>
  <article>
    <h1 class="Post-Title">知乎 专栏示例</h1>
    <div class="AuthorInfo-name">李四 <span aria-hidden="true">\u200b</span></div>
    <meta itemprop="datePublished" content="2025-03-09T10:18:27.000Z">
    <meta itemprop="dateModified" content="2025-04-11T17:54:57.000Z">
    <div class="ContentItem-time">编辑于 2025-04-12 01:54 · 上海</div>
    <div class="Post-RichText">
      <h2>章节二</h2>
      <p>普通 <strong>加粗</strong>、<em>强调</em>、<code>inline()</code> 与
        <a href="https://example.com/doc">外链</a><svg><path d="M0 0"></path></svg>之后。<br>必要换行</p>
      <h3>章节三</h3>
      <ul><li>条目一</li><li>条目二</li></ul>
      <blockquote>引用内容</blockquote>
      <div class="highlight"><pre><code class="language-python"><span>def hello():</span>
<span>    print("``` inside")</span></code></pre></div>
      <figure><div><img src="知乎示例_files/body.jpg"
        data-original="https://pic.zhimg.com/original.jpg" class="origin_image"></div>
        <figcaption>正文图注</figcaption></figure>
      <div class="RichText-LinkCardContainer"><a class="LinkCard"
        href="https://link.zhihu.com/?target=https%3A%2F%2Fexample.com%2Frepo">
        <span class="LinkCard-image"><img src="知乎示例_files/card.jpg"></span>
        <span class="LinkCard-title">示例仓库</span><span class="LinkCard-desc">example.com</span>
      </a></div>
      <p>视频之前</p>
      <div class="VideoCard"><img src="知乎示例_files/poster.jpg"><span>演示视频</span>
        <video src="https://video.example/demo.mp4"></video></div>
      <p>视频之后</p>
    </div>
  </article>
  <div class="Comments-container">评论噪声 <img src="知乎示例_files/avatar.jpg"></div>
  <img src="知乎示例_files/icon.svg">
</body></html>""",
        encoding="utf-8",
    )
    return html_path, resources, output
