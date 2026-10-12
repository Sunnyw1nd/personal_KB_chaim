from __future__ import annotations

import argparse
import secrets
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

from ..application.image_tasks import prepare_revision_images, safe_child
from ..application.workbench import Workbench, read_json
from ..enrichment.security import redact_sensitive_object

ASSETS = Path(__file__).with_name("assets")


def create_app(core: Workbench, *, port: int = 8765) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config.update(
        MAX_CONTENT_LENGTH=100 * 1024 * 1024, MAX_FORM_MEMORY_SIZE=500000, MAX_FORM_PARTS=32
    )
    host = f"127.0.0.1:{port}"
    origin = f"http://{host}"
    token = secrets.token_urlsafe(32)

    @app.before_request
    def protect():
        if request.host != host:
            abort(403)
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            abort(403)
        if request.method != "GET":
            if request.method != "POST" or request.headers.get("Origin") != origin:
                abort(403)
            supplied = request.headers.get("X-Workbench-Token", "")
            if not secrets.compare_digest(supplied, token):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers.update(
            {
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
            }
        )
        return response

    @app.errorhandler(Exception)
    def errors(exc):
        status = getattr(exc, "code", 400)
        if not isinstance(status, int) or not 400 <= status <= 599:
            status = 400
        # Do not expose traceback, credentials or arbitrary SDK response bodies.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        safe, _ = redact_sensitive_object({"error": message})
        return jsonify(safe), status

    def reply(value):
        safe, _ = redact_sensitive_object(value)
        return jsonify(safe)

    def body():
        if (
            not request.is_json
            or request.content_length is None
            or request.content_length > 1024 * 1024
        ):
            abort(400)
        data = request.get_json()
        if not isinstance(data, dict):
            abort(400)
        return data

    @app.get("/")
    def index():
        return send_file(ASSETS / "index.html", mimetype="text/html")

    @app.get("/static/<name>")
    def static(name):
        if name == "observer.css":
            return send_file(
                Path(__file__).parents[1] / "enrichment/observer_assets/site.css",
                mimetype="text/css",
            )
        if name not in {"app.js", "app.css"}:
            abort(404)
        return send_file(ASSETS / name)

    @app.get("/api/bootstrap")
    def bootstrap():
        return reply(
            {
                "csrfToken": token,
                "keyConfigured": bool(core.key_reader()),
                "profile": "compact-json-v1",
                "actor": core.actor,
                "maximumSelectedGroups": 12,
                "perImageCallLimit": 3,
            }
        )

    @app.get("/api/documents")
    def documents():
        return reply(core.documents_list())

    @app.get("/api/document")
    def document():
        value = core.preview(request.args["doc"], request.args.get("rev"))
        directory, _ = core.revision(request.args["doc"], request.args.get("rev"))
        value["processingReport"] = read_json(safe_child(directory, "processing-report.json"))
        value["resourceManifest"] = read_json(safe_child(directory, "resource-manifest.json"))
        return reply(value)

    @app.get("/api/asset")
    def asset():
        directory, _ = core.revision(request.args["doc"], request.args["rev"])
        images = prepare_revision_images(directory, policy_path=core.policy)
        group = next(
            (
                g
                for g in images.groups
                if any(loc["assetId"] == request.args["asset"] for loc in g["locations"])
            ),
            None,
        )
        if group is None:
            abort(404)
        # Serve only independently decoded raster images; never HTML, scripts or SVG.
        return send_file(
            group["prepared"].path,
            mimetype=group["prepared"].request.image.media_type,
            as_attachment=False,
        )

    @app.post("/api/import")
    def import_zip():
        uploaded = request.files.getlist("files")
        if not 1 <= len(uploaded) <= 30:
            raise ValueError("select 1 to 30 ZIPs")
        rows = [core.save_upload(item.filename or "", item.stream) for item in uploaded]
        return reply(core.import_archives(rows, request.form.get("requestId", "")))

    @app.get("/api/tasks")
    def tasks():
        return reply(core.tasks())

    @app.get("/api/task/<task_id>")
    def task(task_id):
        return reply(core.task(task_id))

    @app.post("/api/enrich")
    def enrich():
        data = body()
        return reply(
            core.enrich(
                data["documentId"],
                data["revisionId"],
                data["keys"],
                consent=data.get("consent"),
                request_id=data["requestId"],
                new_run=data.get("newRun") is True,
            )
        )

    @app.post("/api/recover")
    def recover():
        return reply(core.recover(body()["taskId"]))

    @app.get("/api/result")
    def result():
        return reply(core.result(request.args["doc"], request.args["rev"], request.args["key"]))

    @app.post("/api/review")
    def review():
        data = body()
        return reply(
            core.review(
                data["documentId"], data["revisionId"], data["key"], data["status"], data["reason"]
            )
        )

    @app.post("/api/human/save")
    def save_human():
        data = body()
        return reply(
            core.save_human(
                data["documentId"],
                data["revisionId"],
                data["key"],
                data.get("description"),
                data.get("keyFacts"),
                data.get("codeText"),
            )
        )

    @app.post("/api/human/adopt")
    def adopt_human():
        data = body()
        return reply(
            core.adopt_human(
                data["documentId"], data["revisionId"], data["key"], data["humanRevisionId"]
            )
        )

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description="Loopback-only personal knowledge workbench")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--documents-root", type=Path)
    parser.add_argument("--enrichment-root", type=Path)
    parser.add_argument(
        "--verification-mode",
        action="store_true",
        help="Mark human edits as Codex tests; never register user approval; cloud lifetime cap=3.",
    )
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    core = Workbench(
        args.project_root,
        data_root=args.data_root,
        documents_root=args.documents_root,
        enrichment_root=args.enrichment_root,
        actor="codex_test" if args.verification_mode else "user",
        cloud_call_cap=3 if args.verification_mode else None,
    )
    from werkzeug.serving import make_server

    server = make_server("127.0.0.1", args.port, create_app(core, port=args.port), threaded=True)
    print(
        f"Workbench: http://127.0.0.1:{args.port}/ ; Ctrl+C stops; no automatic model calls.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        core.close()


if __name__ == "__main__":
    main()
