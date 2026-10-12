from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import re
import statistics
import sys
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPERIMENT_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = EXPERIMENT_ROOT.parents[1]
DEFAULT_IMAGE_ROOT = PROJECT_ROOT / "test_pictures"
DEFAULT_ANNOTATION_INVENTORY = (
    PROJECT_ROOT / "output/image-annotation/annotation-002/inventory.json"
)
DEFAULT_WORKBOOK = PROJECT_ROOT / "output/image-annotation/annotation-002/图片辅助标注.xlsx"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "output/image-router-benchmark"
DEFAULT_WORK_ROOT = PROJECT_ROOT / ".work/image-router-benchmark"
DEFAULT_INPUT_MANIFEST = DEFAULT_OUTPUT_ROOT / "input-manifest.json"
DEFAULT_CATEGORIES = EXPERIMENT_ROOT / "config/categories-v1.json"
DEFAULT_MODELS = EXPERIMENT_ROOT / "config/models-v1.json"
DEFAULT_BENCHMARK_MAPPING = EXPERIMENT_ROOT / "config/benchmark-mapping-v1.json"
SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
TYPE_LABELS = {
    "qr_code",
    "plain_text_or_decorative_text",
    "code_screenshot",
    "table",
    "chart_or_plot",
    "diagram_or_flowchart",
    "ui_screenshot",
    "informative_photo",
    "decorative_image",
    "logo_or_icon",
    "other_or_uncertain",
}
ROUTE_LABELS = {
    "local_qr",
    "local_ocr",
    "cloud_code",
    "cloud_structured_visual",
    "cloud_visual_description",
    "skip_candidate",
    "uncertain",
}
LOCAL_ROUTES = {"local_qr", "local_ocr", "skip_candidate"}
SAFE_RUN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stable_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(value, encoding="utf-8")
    os.replace(temp, path)


def write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    atomic_text(
        path,
        "".join(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n" for value in values),
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def directory_size(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError:
        return None


def safe_image_path(root: Path, relative_path: str) -> Path:
    root = root.resolve(strict=True)
    candidate = root / Path(relative_path)
    if candidate.is_symlink():
        raise ValueError(f"symbolic-link image is not allowed: {relative_path}")
    resolved = candidate.resolve(strict=True)
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"image path escapes input root: {relative_path}")
    if not resolved.is_file():
        raise ValueError(f"image is not a regular file: {relative_path}")
    return resolved


def validate_run_name(value: str, field_name: str = "run name") -> str:
    if not SAFE_RUN_NAME.fullmatch(value):
        raise ValueError(
            f"{field_name} must be 1-80 ASCII letters, digits, dots, underscores, or hyphens"
        )
    return value


def ensure_output_outside_input(output_root: Path, input_root: Path) -> None:
    output = output_root.resolve()
    source = input_root.resolve(strict=True)
    if output == source or source in output.parents:
        raise ValueError("output root must not be inside the image input directory")


def load_categories(path: Path) -> tuple[dict[str, Any], str]:
    value = read_json(path)
    if set(value.get("types", {})) != TYPE_LABELS:
        raise ValueError("category config does not define the exact allowed type labels")
    if set(value.get("routes", {})) != ROUTE_LABELS:
        raise ValueError("category config does not define the exact allowed route labels")
    for section in ("types", "routes"):
        for label, descriptions in value[section].items():
            if not 3 <= len(descriptions) <= 5 or not all(
                isinstance(item, str) and item.strip() for item in descriptions
            ):
                raise ValueError(f"{section}.{label} must have 3-5 non-empty descriptions")
    temperature = value.get("comparisonScores", {}).get("temperature")
    if not isinstance(temperature, (int, float)) or temperature <= 0:
        raise ValueError("comparison score temperature must be positive")
    return value, sha256_file(path)


def create_input_manifest(
    image_root: Path,
    annotation_inventory: Path | None,
    output_path: Path,
) -> dict[str, Any]:
    from PIL import Image

    root = image_root.resolve(strict=True)
    inventory_hash = (
        sha256_file(annotation_inventory)
        if annotation_inventory is not None and annotation_inventory.is_file()
        else None
    )
    files: list[dict[str, Any]] = []
    for path in sorted(
        (
            item
            for item in root.rglob("*")
            if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES
        ),
        key=lambda item: item.relative_to(root).as_posix().casefold(),
    ):
        relative_path = path.relative_to(root).as_posix()
        digest = sha256_file(path)
        entry: dict[str, Any] = {
            "imageSha256": digest,
            "relativePath": relative_path,
            "sizeBytes": path.stat().st_size,
            "decodable": False,
            "format": None,
            "width": None,
            "height": None,
            "error": None,
        }
        try:
            with Image.open(path) as image:
                image.load()
                entry.update(
                    {
                        "decodable": True,
                        "format": image.format,
                        "width": image.width,
                        "height": image.height,
                    }
                )
        except (OSError, ValueError) as exc:
            entry["error"] = f"{type(exc).__name__}: {exc}"
        files.append(entry)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in files:
        groups[entry["imageSha256"]].append(entry)
    for digest, members in groups.items():
        duplicate = digest if len(members) > 1 else None
        for entry in members:
            entry["duplicateGroup"] = duplicate
            entry["duplicateCount"] = len(members)
    dataset_value = [
        {key: entry[key] for key in ("imageSha256", "relativePath", "sizeBytes")} for entry in files
    ]
    manifest = {
        "schemaVersion": "1.0.0",
        "createdAt": utc_now(),
        "imageRoot": str(root),
        "sourceInventoryPath": str(annotation_inventory.resolve())
        if annotation_inventory is not None and annotation_inventory.exists()
        else None,
        "sourceInventorySha256": inventory_hash,
        "datasetFingerprintSha256": stable_sha(dataset_value),
        "statistics": {
            "files": len(files),
            "uniqueImages": len(groups),
            "duplicateGroups": sum(len(items) > 1 for items in groups.values()),
            "decodable": sum(bool(entry["decodable"]) for entry in files),
            "decodeFailures": sum(not entry["decodable"] for entry in files),
            "formats": dict(
                sorted(
                    Counter(entry["format"] or "UNKNOWN" for entry in files).items(),
                    key=lambda item: str(item[0]),
                )
            ),
        },
        "files": files,
    }
    atomic_json(output_path, manifest)
    return manifest


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def system_info() -> dict[str, Any]:
    import psutil
    import torch

    memory = psutil.virtual_memory()
    gpu = None
    if torch.cuda.is_available():
        properties = torch.cuda.get_device_properties(0)
        gpu = {"name": properties.name, "memoryBytes": properties.total_memory}
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "cpu": platform.processor(),
        "physicalCores": psutil.cpu_count(logical=False),
        "logicalCores": psutil.cpu_count(logical=True),
        "totalMemoryBytes": memory.total,
        "gpu": gpu,
        "dependencies": {
            name: package_version(name)
            for name in (
                "torch",
                "torchvision",
                "transformers",
                "open-clip-torch",
                "timm",
                "Pillow",
                "numpy",
                "psutil",
                "huggingface-hub",
            )
        },
    }


@dataclass
class Encoder:
    encode_text: Callable[[list[str]], Any]
    encode_image: Callable[[Any], Any]
    snapshot_path: Path


def download_snapshot(model_config: dict[str, Any], model_cache: Path, offline: bool) -> Path:
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            repo_id=model_config["repository"],
            revision=model_config["revision"],
            cache_dir=model_cache,
            local_files_only=offline,
            allow_patterns=model_config.get("downloadPatterns"),
        )
    )


def load_encoder(
    model_config: dict[str, Any], model_cache: Path, device: str, offline: bool
) -> Encoder:
    import torch

    snapshot = download_snapshot(model_config, model_cache, offline)
    backend = model_config["backend"]
    if backend in {"transformers_clip", "transformers_siglip2"}:
        from transformers import AutoModel, AutoProcessor

        processor = AutoProcessor.from_pretrained(snapshot, local_files_only=True)
        model = AutoModel.from_pretrained(snapshot, local_files_only=True).to(device).eval()

        def encode_text(texts: list[str]):
            text_options: dict[str, Any] = {"padding": True, "truncation": True}
            if backend == "transformers_siglip2":
                text_options.update({"padding": "max_length", "max_length": 64})
            inputs = processor(text=texts, return_tensors="pt", **text_options)
            inputs = {key: value.to(device) for key, value in inputs.items()}
            with torch.inference_mode():
                return model.get_text_features(**inputs).float().cpu()

        def encode_image(image):
            inputs = processor(images=[image], return_tensors="pt")
            inputs = {key: value.to(device) for key, value in inputs.items()}
            with torch.inference_mode():
                return model.get_image_features(**inputs).float().cpu()

        return Encoder(encode_text, encode_image, snapshot)
    if backend == "mobileclip":
        try:
            import open_clip
            from mobileclip.modules.common.mobileone import reparameterize_model
        except ImportError as exc:
            raise RuntimeError(
                "MobileCLIP requires the isolated setup_environment.ps1 environment"
            ) from exc

        checkpoint = snapshot / model_config["checkpoint"]
        model, _, preprocess = open_clip.create_model_and_transforms(
            "MobileCLIP2-S0",
            pretrained=str(checkpoint),
            image_mean=(0, 0, 0),
            image_std=(1, 1, 1),
        )
        model = reparameterize_model(model.eval()).to(device).eval()
        tokenizer = open_clip.get_tokenizer("MobileCLIP2-S0")

        def encode_text(texts: list[str]):
            tokens = tokenizer(texts).to(device)
            with torch.inference_mode():
                return model.encode_text(tokens).float().cpu()

        def encode_image(image):
            pixels = preprocess(image).unsqueeze(0).to(device)
            with torch.inference_mode():
                return model.encode_image(pixels).float().cpu()

        return Encoder(encode_text, encode_image, snapshot)
    raise ValueError(f"unsupported model backend: {backend}")


def normalize_rows(tensor):
    import torch

    return torch.nn.functional.normalize(tensor, dim=-1)


def build_label_embeddings(encoder: Encoder, labels: dict[str, list[str]]) -> tuple[list[str], Any]:
    import torch

    names = list(labels)
    vectors = []
    for name in names:
        prompt_vectors = normalize_rows(encoder.encode_text(labels[name]))
        vectors.append(normalize_rows(prompt_vectors.mean(dim=0, keepdim=True))[0])
    return names, torch.stack(vectors)


def score_vector(
    image_vector, label_vectors, temperature: float
) -> tuple[list[float], list[float]]:
    import torch

    image_vector = normalize_rows(image_vector)[0]
    similarities = (image_vector @ label_vectors.T).tolist()
    probabilities = torch.softmax(
        torch.tensor(similarities, dtype=torch.float32) / temperature, dim=0
    ).tolist()
    return [float(item) for item in similarities], [float(item) for item in probabilities]


def ranked_scores(
    names: list[str], similarities: list[float], probabilities: list[float]
) -> list[dict[str, Any]]:
    return sorted(
        (
            {
                "label": name,
                "cosineSimilarity": similarities[index],
                "comparisonScore": probabilities[index],
            }
            for index, name in enumerate(names)
        ),
        key=lambda item: (-item["cosineSimilarity"], item["label"]),
    )


def apply_route_threshold(
    ranked: list[dict[str, Any]], thresholds: dict[str, Any]
) -> tuple[str, list[str]]:
    top, second = ranked[:2]
    if top["label"] == "uncertain":
        return "uncertain", ["model_predicted_uncertain"]
    rule = thresholds.get(top["label"], thresholds["default"])
    reasons = []
    if top["comparisonScore"] < rule["minComparisonScore"]:
        reasons.append("comparison_score_below_threshold")
    if top["cosineSimilarity"] - second["cosineSimilarity"] < rule["minCosineMargin"]:
        reasons.append("top1_top2_margin_below_threshold")
    return ("uncertain", reasons) if reasons else (top["label"], [])


def prediction_cache_key(
    image_sha256: str,
    model_name: str,
    model_config: dict[str, Any],
    categories_sha256: str,
) -> str:
    return stable_sha(
        {
            "imageSha256": image_sha256,
            "model": model_name,
            "repository": model_config["repository"],
            "revision": model_config["revision"],
            "implementationRevision": model_config.get("implementationRevision"),
            "adapterRevision": model_config["adapterRevision"],
            "preprocessId": model_config["preprocessId"],
            "categoriesSha256": categories_sha256,
            "precision": "float32",
            "batchSize": 1,
        }
    )


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def weight_evidence(snapshot: Path) -> list[dict[str, Any]]:
    suffixes = {".pt", ".bin", ".safetensors"}
    values = []
    for path in sorted(item for item in snapshot.rglob("*") if item.suffix in suffixes):
        values.append(
            {
                "relativePath": path.relative_to(snapshot).as_posix(),
                "sizeBytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return values


def run_inference(
    input_manifest_path: Path,
    categories_path: Path,
    models_path: Path,
    model_name: str,
    run_name: str,
    output_root: Path,
    work_root: Path,
    device: str,
    threads: int,
    offline: bool,
) -> tuple[Path, dict[str, Any]]:
    import psutil
    import torch
    from PIL import Image

    if device != "cpu":
        raise ValueError("the frozen baseline permits only --device cpu")
    if threads < 1:
        raise ValueError("--threads must be positive")
    validate_run_name(run_name)
    torch.set_num_threads(threads)
    input_manifest = read_json(input_manifest_path)
    categories, categories_sha = load_categories(categories_path)
    models = read_json(models_path)
    try:
        model_config = models["models"][model_name]
    except KeyError as exc:
        raise ValueError(f"unknown model: {model_name}") from exc
    image_root = Path(input_manifest["imageRoot"])
    output_root = output_root.resolve()
    ensure_output_outside_input(output_root, image_root)
    ensure_output_outside_input(work_root, image_root)
    run_directory = output_root / "runs" / run_name
    if run_directory.exists():
        raise FileExistsError(f"run already exists: {run_directory}")
    run_directory.mkdir(parents=True)
    cache_root = work_root.resolve() / "prediction-cache" / model_name
    model_cache = work_root.resolve() / "models"
    cache_root.mkdir(parents=True, exist_ok=True)
    model_cache.mkdir(parents=True, exist_ok=True)
    process = psutil.Process()
    start_rss = process.memory_info().rss
    started = time.perf_counter()
    manifest: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "runId": run_name,
        "status": "loading_model",
        "createdAt": utc_now(),
        "model": model_name,
        "modelConfiguration": model_config,
        "device": device,
        "batchSize": 1,
        "precision": "float32",
        "torchThreads": threads,
        "categoriesPath": str(categories_path.resolve()),
        "categoriesSha256": categories_sha,
        "descriptionVersion": categories["descriptionVersion"],
        "inputManifestPath": str(input_manifest_path.resolve()),
        "inputManifestSha256": sha256_file(input_manifest_path),
        "datasetFingerprintSha256": input_manifest["datasetFingerprintSha256"],
        "environment": system_info(),
    }
    atomic_json(run_directory / "run-manifest.json", manifest)
    try:
        fully_cached: list[dict[str, Any]] = []
        for entry in input_manifest["files"]:
            if not entry["decodable"]:
                fully_cached = []
                break
            key = prediction_cache_key(
                entry["imageSha256"], model_name, model_config, categories_sha
            )
            cache_path = cache_root / f"{key}.json"
            try:
                cached = read_json(cache_path)
            except (FileNotFoundError, OSError, ValueError):
                fully_cached = []
                break
            if cached.get("cacheKey") != key or cached.get("status") != "success":
                fully_cached = []
                break
            fully_cached.append({**cached, "relativePath": entry["relativePath"], "cacheHit": True})
        if len(fully_cached) == len(input_manifest["files"]):
            fully_cached.sort(key=lambda item: item["relativePath"].casefold())
            write_jsonl(run_directory / "predictions.jsonl", fully_cached)
            memory = process.memory_info()
            manifest.update(
                {
                    "status": "completed",
                    "completedAt": utc_now(),
                    "snapshotPath": None,
                    "snapshotSizeBytes": None,
                    "weightFiles": [],
                    "modelLoadSkippedDueToFullCache": True,
                    "timing": {
                        "coldModelLoadMs": 0,
                        "textEmbeddingMs": 0,
                        "wallClockTotalMs": round((time.perf_counter() - started) * 1000),
                        "imageInferenceMsP50": None,
                        "imageInferenceMsP95": None,
                        "imageInferenceMsTotal": 0,
                    },
                    "memory": {
                        "processRssBeforeLoadBytes": start_rss,
                        "processRssAfterRunBytes": memory.rss,
                        "processPeakWorkingSetBytes": getattr(memory, "peak_wset", None),
                    },
                    "statistics": {
                        "files": len(fully_cached),
                        "success": len(fully_cached),
                        "failures": 0,
                        "cacheHits": len(fully_cached),
                        "freshPredictions": 0,
                    },
                    "notes": [
                        "Inference did not open the human benchmark workbook.",
                        "All prediction cache entries were validated before model loading.",
                        "The model was not loaded and no image inference was repeated.",
                    ],
                }
            )
            atomic_json(run_directory / "run-manifest.json", manifest)
            return run_directory / "run-manifest.json", manifest
        load_started = time.perf_counter()
        encoder = load_encoder(model_config, model_cache, device, offline)
        load_ms = round((time.perf_counter() - load_started) * 1000)
        text_started = time.perf_counter()
        type_names, type_vectors = build_label_embeddings(encoder, categories["types"])
        route_names, route_vectors = build_label_embeddings(encoder, categories["routes"])
        text_ms = round((time.perf_counter() - text_started) * 1000)
        temperature = float(categories["comparisonScores"]["temperature"])
        predictions: list[dict[str, Any]] = []
        durations: list[float] = []
        cache_hits = 0
        for entry in input_manifest["files"]:
            key = prediction_cache_key(
                entry["imageSha256"], model_name, model_config, categories_sha
            )
            cache_path = cache_root / f"{key}.json"
            if cache_path.is_file():
                try:
                    cached = read_json(cache_path)
                    if cached.get("cacheKey") == key and cached.get("status") == "success":
                        predictions.append(
                            {
                                **cached,
                                "relativePath": entry["relativePath"],
                                "cacheHit": True,
                            }
                        )
                        cache_hits += 1
                        continue
                except (OSError, ValueError):
                    pass
            base = {
                "schemaVersion": "1.0.0",
                "cacheKey": key,
                "cacheHit": False,
                "imageSha256": entry["imageSha256"],
                "relativePath": entry["relativePath"],
                "model": model_name,
                "modelRevision": model_config["revision"],
                "preprocessId": model_config["preprocessId"],
                "categoriesSha256": categories_sha,
                "processedAt": utc_now(),
            }
            if not entry["decodable"]:
                predictions.append(
                    {
                        **base,
                        "status": "decode_error",
                        "error": entry["error"] or "input manifest reports decode failure",
                    }
                )
                continue
            image_started = time.perf_counter()
            try:
                path = safe_image_path(image_root, entry["relativePath"])
                if sha256_file(path) != entry["imageSha256"]:
                    raise ValueError("image SHA-256 changed after inventory")
                with Image.open(path) as image:
                    image.load()
                    vector = encoder.encode_image(image.convert("RGB"))
                type_similarity, type_probability = score_vector(vector, type_vectors, temperature)
                route_similarity, route_probability = score_vector(
                    vector, route_vectors, temperature
                )
                type_scores = ranked_scores(type_names, type_similarity, type_probability)
                route_scores = ranked_scores(route_names, route_similarity, route_probability)
                final_route, rejection_reasons = apply_route_threshold(
                    route_scores, categories["routeThresholds"]
                )
                elapsed_ms = round((time.perf_counter() - image_started) * 1000, 3)
                durations.append(elapsed_ms)
                prediction = {
                    **base,
                    "status": "success",
                    "elapsedMs": elapsed_ms,
                    "typePrediction": {
                        "top1": type_scores[0],
                        "top2": type_scores[1],
                        "cosineMargin": type_scores[0]["cosineSimilarity"]
                        - type_scores[1]["cosineSimilarity"],
                        "scores": type_scores,
                    },
                    "routePrediction": {
                        "rawTop1": route_scores[0],
                        "rawTop2": route_scores[1],
                        "cosineMargin": route_scores[0]["cosineSimilarity"]
                        - route_scores[1]["cosineSimilarity"],
                        "finalLabel": final_route,
                        "rejectionReasons": rejection_reasons,
                        "scores": route_scores,
                    },
                }
                prediction_for_cache = {**prediction, "cacheHit": False}
                atomic_json(cache_path, prediction_for_cache)
                predictions.append(prediction)
            except (OSError, RuntimeError, ValueError) as exc:
                predictions.append(
                    {
                        **base,
                        "status": "inference_error",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        predictions.sort(key=lambda item: item["relativePath"].casefold())
        write_jsonl(run_directory / "predictions.jsonl", predictions)
        memory = process.memory_info()
        snapshot_size = directory_size(encoder.snapshot_path)
        successful = [item for item in predictions if item["status"] == "success"]
        manifest.update(
            {
                "status": "completed"
                if len(successful) == len(predictions)
                else "completed_with_failures",
                "completedAt": utc_now(),
                "snapshotPath": str(encoder.snapshot_path),
                "snapshotSizeBytes": snapshot_size,
                "weightFiles": weight_evidence(encoder.snapshot_path),
                "timing": {
                    "coldModelLoadMs": load_ms,
                    "textEmbeddingMs": text_ms,
                    "wallClockTotalMs": round((time.perf_counter() - started) * 1000),
                    "imageInferenceMsP50": statistics.median(durations) if durations else None,
                    "imageInferenceMsP95": percentile(durations, 0.95),
                    "imageInferenceMsTotal": sum(durations) if durations else None,
                },
                "memory": {
                    "processRssBeforeLoadBytes": start_rss,
                    "processRssAfterRunBytes": memory.rss,
                    "processPeakWorkingSetBytes": getattr(memory, "peak_wset", None),
                },
                "statistics": {
                    "files": len(predictions),
                    "success": len(successful),
                    "failures": len(predictions) - len(successful),
                    "cacheHits": cache_hits,
                    "freshPredictions": len(successful) - cache_hits,
                },
                "notes": [
                    "Inference did not open the human benchmark workbook.",
                    "Comparison scores are relative softmax values, not calibrated probabilities.",
                    "CPU batch size was fixed at one for the baseline.",
                ],
            }
        )
    except Exception as exc:
        manifest.update(
            {
                "status": "failed",
                "completedAt": utc_now(),
                "failure": {"type": type(exc).__name__, "message": str(exc)},
            }
        )
        atomic_json(run_directory / "run-manifest.json", manifest)
        raise
    atomic_json(run_directory / "run-manifest.json", manifest)
    return run_directory / "run-manifest.json", manifest


def load_benchmark(workbook_path: Path, mapping_path: Path) -> tuple[dict[str, Any], dict]:
    from openpyxl import load_workbook

    mapping = read_json(mapping_path)
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    main = workbook["标注主表"]
    files = workbook["文件与来源"]
    main_headers = {main.cell(4, column).value: column for column in range(1, main.max_column + 1)}
    file_headers = {
        files.cell(4, column).value: column for column in range(1, files.max_column + 1)
    }
    required_main = {
        "序号",
        "短 imageId",
        "原文件名",
        "图片主类型",
        "知识价值",
        "推荐处理方式",
        "审核状态",
    }
    if not required_main <= set(main_headers):
        raise ValueError("benchmark workbook main sheet is missing required columns")
    full_by_ordinal = {
        files.cell(row, file_headers["序号"]).value: files.cell(
            row, file_headers["完整 imageId / SHA-256"]
        ).value
        for row in range(5, files.max_row + 1)
    }
    benchmark: dict[str, Any] = {}
    for row in range(5, main.max_row + 1):
        ordinal = main.cell(row, main_headers["序号"]).value
        if ordinal is None:
            continue
        image_sha = full_by_ordinal.get(ordinal)
        if not isinstance(image_sha, str) or len(image_sha) != 64:
            raise ValueError(f"workbook row {row} has no full image SHA-256 mapping")
        human_type = main.cell(row, main_headers["图片主类型"]).value
        knowledge = main.cell(row, main_headers["知识价值"]).value
        action = main.cell(row, main_headers["推荐处理方式"]).value
        if human_type == mapping["photoRules"]["humanType"]:
            is_decorative = (
                action in mapping["photoRules"]["decorativeActions"]
                or knowledge in mapping["photoRules"]["decorativeKnowledgeValues"]
            )
            target_type = mapping["photoRules"][
                "decorativeType" if is_decorative else "otherwiseType"
            ]
        else:
            target_type = mapping["humanTypeToExperimentType"].get(human_type)
        target_route = mapping["humanActionToRoute"].get(action)
        if target_type not in TYPE_LABELS:
            raise ValueError(f"unmapped human image type at workbook row {row}: {human_type!r}")
        if target_route not in ROUTE_LABELS:
            raise ValueError(f"unmapped human action at workbook row {row}: {action!r}")
        benchmark[image_sha] = {
            "ordinal": ordinal,
            "fileName": main.cell(row, main_headers["原文件名"]).value,
            "humanTypeRaw": human_type,
            "targetType": target_type,
            "knowledgeValue": knowledge,
            "humanActionRaw": action,
            "targetRoute": target_route,
            "reviewStatus": main.cell(row, main_headers["审核状态"]).value,
        }
    workbook.close()
    return benchmark, mapping


def confusion_rows(
    values: list[dict[str, Any]], actual_key: str, predicted_key: str, labels: list[str]
) -> list[list[Any]]:
    matrix = Counter((item[actual_key], item[predicted_key]) for item in values)
    return [["actual/predicted", *labels]] + [
        [actual, *(matrix[(actual, predicted)] for predicted in labels)] for actual in labels
    ]


def write_csv(path: Path, rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temp.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows(rows)
    os.replace(temp, path)


def classification_metrics(
    values: list[dict[str, Any]], actual_key: str, predicted_key: str, labels: list[str]
) -> dict[str, Any]:
    total = len(values)
    correct = sum(item[actual_key] == item[predicted_key] for item in values)
    per_label = {}
    for label in labels:
        tp = sum(item[actual_key] == label and item[predicted_key] == label for item in values)
        predicted = sum(item[predicted_key] == label for item in values)
        actual = sum(item[actual_key] == label for item in values)
        per_label[label] = {
            "support": actual,
            "predicted": predicted,
            "precision": tp / predicted if predicted else None,
            "recall": tp / actual if actual else None,
        }
    return {
        "count": total,
        "correct": correct,
        "accuracy": correct / total if total else None,
        "perLabel": per_label,
    }


def route_policy_metrics(
    values: list[dict[str, Any]], min_score: float, min_margin: float
) -> dict[str, Any]:
    evaluated = []
    for item in values:
        top = item["routeScores"][0]
        second = item["routeScores"][1]
        accepted = (
            top["label"] != "uncertain"
            and top["comparisonScore"] >= min_score
            and top["cosineSimilarity"] - second["cosineSimilarity"] >= min_margin
        )
        label = top["label"] if accepted else "uncertain"
        evaluated.append((item, label, accepted))
    accepted_items = [(item, label) for item, label, accepted in evaluated if accepted]
    correct = sum(item["targetRoute"] == label for item, label in accepted_items)
    dangerous = [
        item
        for item, label in accepted_items
        if label == "skip_candidate" and item["knowledgeValue"] in {"中", "高"}
    ]
    complex_to_ocr = [
        item
        for item, label in accepted_items
        if label == "local_ocr" and item["targetRoute"] in {"cloud_code", "cloud_structured_visual"}
    ]
    local = sum(label in LOCAL_ROUTES for _, label in accepted_items)
    total = len(values)
    return {
        "minComparisonScore": min_score,
        "minCosineMargin": min_margin,
        "coverage": len(accepted_items) / total if total else None,
        "accepted": len(accepted_items),
        "acceptedPrecision": correct / len(accepted_items) if accepted_items else None,
        "rejected": total - len(accepted_items),
        "estimatedCloudReduction": local / total if total else None,
        "dangerousSkipCount": len(dangerous),
        "complexToLocalOcrCount": len(complex_to_ocr),
    }


def score_predictions(
    predictions_path: Path,
    workbook_path: Path,
    mapping_path: Path,
    output_root: Path,
    score_name: str | None,
) -> tuple[Path, dict[str, Any]]:
    predictions = read_jsonl(predictions_path)
    run_manifest_path = predictions_path.parent / "run-manifest.json"
    run_manifest = read_json(run_manifest_path)
    name = score_name or run_manifest["runId"]
    validate_run_name(name, "score name")
    output_directory = output_root.resolve() / "scores" / name
    if output_directory.exists():
        raise FileExistsError(f"score output already exists: {output_directory}")
    output_directory.mkdir(parents=True)
    benchmark, mapping = load_benchmark(workbook_path, mapping_path)
    scored = []
    missing_benchmark = []
    for prediction in predictions:
        if prediction["status"] != "success":
            continue
        human = benchmark.get(prediction["imageSha256"])
        if human is None:
            missing_benchmark.append(prediction["imageSha256"])
            continue
        scored.append(
            {
                "imageSha256": prediction["imageSha256"],
                "relativePath": prediction["relativePath"],
                **human,
                "predictedType": prediction["typePrediction"]["top1"]["label"],
                "predictedRouteRaw": prediction["routePrediction"]["rawTop1"]["label"],
                "predictedRouteFinal": prediction["routePrediction"]["finalLabel"],
                "rejectionReasons": prediction["routePrediction"]["rejectionReasons"],
                "typeScores": prediction["typePrediction"]["scores"],
                "routeScores": prediction["routePrediction"]["scores"],
            }
        )
    if not scored:
        raise ValueError("no successful predictions align with the benchmark workbook")
    type_labels = sorted(TYPE_LABELS)
    route_labels = sorted(ROUTE_LABELS)
    type_metrics = classification_metrics(scored, "targetType", "predictedType", type_labels)
    route_raw_metrics = classification_metrics(
        scored, "targetRoute", "predictedRouteRaw", route_labels
    )
    route_final_metrics = classification_metrics(
        scored, "targetRoute", "predictedRouteFinal", route_labels
    )
    dangerous_skips = [
        item
        for item in scored
        if item["predictedRouteFinal"] == "skip_candidate"
        and item["knowledgeValue"] in mapping["mediumHighKnowledgeValues"]
    ]
    complex_to_ocr = [
        item
        for item in scored
        if item["predictedRouteFinal"] == "local_ocr"
        and item["targetRoute"] in mapping["complexRoutes"]
    ]
    threshold_rows = []
    for min_score in (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        for min_margin in (0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.08):
            threshold_rows.append(route_policy_metrics(scored, min_score, min_margin))
    review_counts = dict(sorted(Counter(item["reviewStatus"] for item in scored).items()))
    result = {
        "schemaVersion": "1.0.0",
        "createdAt": utc_now(),
        "scoreId": name,
        "runId": run_manifest["runId"],
        "model": run_manifest["model"],
        "predictionsPath": str(predictions_path.resolve()),
        "predictionsSha256": sha256_file(predictions_path),
        "workbookPath": str(workbook_path.resolve()),
        "workbookSha256": sha256_file(workbook_path),
        "mappingPath": str(mapping_path.resolve()),
        "mappingSha256": sha256_file(mapping_path),
        "benchmarkStatus": "provisional_v1"
        if any(item["reviewStatus"] != "已确认" for item in scored)
        else "confirmed",
        "reviewStatusCounts": review_counts,
        "statistics": {
            "predictionRows": len(predictions),
            "successfulPredictions": sum(item["status"] == "success" for item in predictions),
            "scoredUniqueImages": len(scored),
            "missingBenchmark": len(missing_benchmark),
            "benchmarkRows": len(benchmark),
        },
        "typeMetrics": type_metrics,
        "routeRawMetrics": route_raw_metrics,
        "routeFinalMetrics": route_final_metrics,
        "frozenPolicy": {
            "coverage": sum(item["predictedRouteFinal"] != "uncertain" for item in scored)
            / len(scored),
            "accepted": sum(item["predictedRouteFinal"] != "uncertain" for item in scored),
            "acceptedPrecision": (
                sum(
                    item["predictedRouteFinal"] == item["targetRoute"]
                    and item["predictedRouteFinal"] != "uncertain"
                    for item in scored
                )
                / max(1, sum(item["predictedRouteFinal"] != "uncertain" for item in scored))
            ),
            "estimatedCloudReduction": sum(
                item["predictedRouteFinal"] in LOCAL_ROUTES for item in scored
            )
            / len(scored),
            "dangerousSkipCount": len(dangerous_skips),
            "complexToLocalOcrCount": len(complex_to_ocr),
        },
        "dangerousSkips": [
            {
                key: item[key]
                for key in (
                    "imageSha256",
                    "relativePath",
                    "knowledgeValue",
                    "targetRoute",
                    "predictedRouteFinal",
                )
            }
            for item in dangerous_skips
        ],
        "complexToLocalOcr": [
            {
                key: item[key]
                for key in (
                    "imageSha256",
                    "relativePath",
                    "knowledgeValue",
                    "targetRoute",
                    "predictedRouteFinal",
                )
            }
            for item in complex_to_ocr
        ],
        "thresholdAnalysis": threshold_rows,
        "notes": [
            "Inference predictions were generated before this command opened the workbook.",
            "All workbook rows are scored, but unconfirmed rows remain provisional evidence.",
            "Threshold comparison scores are not calibrated probabilities.",
        ],
    }
    atomic_json(output_directory / "score.json", result)
    write_jsonl(output_directory / "scored-images.jsonl", scored)
    write_csv(
        output_directory / "type-confusion.csv",
        confusion_rows(scored, "targetType", "predictedType", type_labels),
    )
    write_csv(
        output_directory / "route-confusion.csv",
        confusion_rows(scored, "targetRoute", "predictedRouteFinal", route_labels),
    )
    threshold_header = [
        "minComparisonScore",
        "minCosineMargin",
        "coverage",
        "acceptedPrecision",
        "rejected",
        "estimatedCloudReduction",
        "dangerousSkipCount",
        "complexToLocalOcrCount",
    ]
    write_csv(
        output_directory / "threshold-analysis.csv",
        [threshold_header] + [[row.get(key) for key in threshold_header] for row in threshold_rows],
    )
    report = render_score_report(result, run_manifest)
    atomic_text(output_directory / "report.md", report)
    return output_directory / "score.json", result


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def render_score_report(score: dict[str, Any], run: dict[str, Any]) -> str:
    policy = score["frozenPolicy"]
    timing = run.get("timing", {})
    lines = [
        f"# {run['model']} 零样本路由评测",
        "",
        f"- 基准状态：`{score['benchmarkStatus']}`；审核状态：{score['reviewStatusCounts']}",
        f"- 对齐图片：{score['statistics']['scoredUniqueImages']} / {score['statistics']['benchmarkRows']}",
        f"- 主类型 Top-1 准确率：{percent(score['typeMetrics']['accuracy'])}",
        f"- 原始路线 Top-1 准确率：{percent(score['routeRawMetrics']['accuracy'])}",
        f"- 冻结阈值自动覆盖率：{percent(policy['coverage'])}",
        f"- 冻结阈值已接收精度：{percent(policy['acceptedPrecision'])}",
        f"- 预计减少云端调用：{percent(policy['estimatedCloudReduction'])}",
        f"- 中高价值错误跳过：{policy['dangerousSkipCount']}",
        f"- 复杂图片误送本地 OCR：{policy['complexToLocalOcrCount']}",
        "",
        "## CPU 资源",
        "",
        f"- 冷启动加载：{timing.get('coldModelLoadMs')} ms",
        f"- 单图 P50 / P95：{timing.get('imageInferenceMsP50')} / {timing.get('imageInferenceMsP95')} ms",
        f"- 45 图推理总时间：{timing.get('imageInferenceMsTotal')} ms",
        f"- 进程峰值工作集：{run.get('memory', {}).get('processPeakWorkingSetBytes')} bytes",
        f"- 权重快照大小：{run.get('snapshotSizeBytes')} bytes",
        "",
        "## 解释限制",
        "",
        "比较分数只是冻结类别集合内的相对 softmax，不是真实正确率。工作簿未确认行仍是暂定基准。",
        "推理阶段没有读取人工标签；本报告由评分阶段按完整 SHA-256 对齐后生成。",
        "",
    ]
    if score["dangerousSkips"]:
        lines.extend(["## 中高价值错误跳过", ""])
        lines.extend(
            f"- `{item['relativePath']}` `{item['imageSha256']}`"
            for item in score["dangerousSkips"]
        )
        lines.append("")
    if score["complexToLocalOcr"]:
        lines.extend(["## 复杂图片误送本地 OCR", ""])
        lines.extend(
            f"- `{item['relativePath']}` `{item['imageSha256']}`"
            for item in score["complexToLocalOcr"]
        )
        lines.append("")
    return "\n".join(lines)


def compare_scores(
    score_paths: list[Path],
    output_root: Path,
    comparison_name: str,
    work_root: Path,
) -> tuple[Path, dict[str, Any]]:
    validate_run_name(comparison_name, "comparison name")
    output_directory = output_root.resolve() / "comparisons" / comparison_name
    if output_directory.exists():
        raise FileExistsError(f"comparison output already exists: {output_directory}")
    output_directory.mkdir(parents=True)
    candidates = []
    threshold_rows = []
    for score_path in score_paths:
        score = read_json(score_path)
        run_path = Path(score["predictionsPath"]).parent / "run-manifest.json"
        run = read_json(run_path)
        timing = run.get("timing", {})
        memory = run.get("memory", {})
        weight_bytes = sum(item["sizeBytes"] for item in run.get("weightFiles", []))
        candidate = {
            "model": score["model"],
            "runId": score["runId"],
            "benchmarkStatus": score["benchmarkStatus"],
            "scoredImages": score["statistics"]["scoredUniqueImages"],
            "typeAccuracy": score["typeMetrics"]["accuracy"],
            "routeRawAccuracy": score["routeRawMetrics"]["accuracy"],
            "frozenCoverage": score["frozenPolicy"]["coverage"],
            "frozenAcceptedPrecision": score["frozenPolicy"]["acceptedPrecision"],
            "estimatedCloudReduction": score["frozenPolicy"]["estimatedCloudReduction"],
            "dangerousSkipCount": score["frozenPolicy"]["dangerousSkipCount"],
            "complexToLocalOcrCount": score["frozenPolicy"]["complexToLocalOcrCount"],
            "coldModelLoadMs": timing.get("coldModelLoadMs"),
            "textEmbeddingMs": timing.get("textEmbeddingMs"),
            "imageInferenceMsP50": timing.get("imageInferenceMsP50"),
            "imageInferenceMsP95": timing.get("imageInferenceMsP95"),
            "imageInferenceMsTotal": timing.get("imageInferenceMsTotal"),
            "processPeakWorkingSetBytes": memory.get("processPeakWorkingSetBytes"),
            "processRssBeforeLoadBytes": memory.get("processRssBeforeLoadBytes"),
            "incrementalPeakWorkingSetBytes": (
                memory["processPeakWorkingSetBytes"] - memory["processRssBeforeLoadBytes"]
                if memory.get("processPeakWorkingSetBytes") is not None
                and memory.get("processRssBeforeLoadBytes") is not None
                else None
            ),
            "snapshotSizeBytes": run.get("snapshotSizeBytes"),
            "inferenceWeightBytes": weight_bytes or None,
            "scorePath": str(score_path.resolve()),
            "runManifestPath": str(run_path.resolve()),
        }
        candidates.append(candidate)
        for row in score["thresholdAnalysis"]:
            threshold_rows.append({"model": score["model"], "runId": score["runId"], **row})
    candidates.sort(key=lambda item: item["model"])
    max_reduction = max(item["estimatedCloudReduction"] or 0 for item in candidates)
    recommendation = (
        "pause_broad_local_router_integration"
        if max_reduction <= 0.1
        else "consider_high_confidence_partial_router"
    )
    work = work_root.resolve()
    comparison = {
        "schemaVersion": "1.0.0",
        "comparisonId": comparison_name,
        "createdAt": utc_now(),
        "status": "completed",
        "candidates": candidates,
        "experimentDisk": {
            "virtualEnvironmentBytes": directory_size(work / "venv"),
            "vendorCodeBytes": directory_size(work / "vendor"),
            "modelCacheBytes": directory_size(work / "models"),
            "predictionCacheBytes": directory_size(work / "prediction-cache"),
            "totalWorkBytes": directory_size(work),
        },
        "recommendation": {
            "decision": recommendation,
            "reason": (
                "Under the frozen safety policy, the best observed reduction in cloud calls "
                f"was {max_reduction:.1%}; broad integration is not justified by this "
                "provisional 45-image benchmark."
            ),
            "fallback": "uncertain_to_cloud",
        },
        "notes": [
            "All compared scores were produced after inference, using the workbook only in scoring.",
            "Threshold rows are exploratory calibration evidence and are not part of zero-shot-v1.",
            "A provisional workbook cannot establish deployment-grade generalization.",
        ],
    }
    atomic_json(output_directory / "comparison.json", comparison)
    summary_columns = [
        "model",
        "runId",
        "benchmarkStatus",
        "scoredImages",
        "typeAccuracy",
        "routeRawAccuracy",
        "frozenCoverage",
        "frozenAcceptedPrecision",
        "estimatedCloudReduction",
        "dangerousSkipCount",
        "complexToLocalOcrCount",
    ]
    write_csv(
        output_directory / "candidate-summary.csv",
        [summary_columns]
        + [[candidate.get(column) for column in summary_columns] for candidate in candidates],
    )
    resource_columns = [
        "model",
        "coldModelLoadMs",
        "textEmbeddingMs",
        "imageInferenceMsP50",
        "imageInferenceMsP95",
        "imageInferenceMsTotal",
        "processPeakWorkingSetBytes",
        "processRssBeforeLoadBytes",
        "incrementalPeakWorkingSetBytes",
        "snapshotSizeBytes",
        "inferenceWeightBytes",
    ]
    write_csv(
        output_directory / "resource-report.csv",
        [resource_columns]
        + [[candidate.get(column) for column in resource_columns] for candidate in candidates],
    )
    threshold_columns = [
        "model",
        "runId",
        "minComparisonScore",
        "minCosineMargin",
        "coverage",
        "accepted",
        "acceptedPrecision",
        "rejected",
        "estimatedCloudReduction",
        "dangerousSkipCount",
        "complexToLocalOcrCount",
    ]
    write_csv(
        output_directory / "threshold-analysis.csv",
        [threshold_columns]
        + [[row.get(column) for column in threshold_columns] for row in threshold_rows],
    )
    report_lines = [
        "# 本地图片路由器零样本对比",
        "",
        "| 模型 | 类型准确率 | 路线准确率 | 冻结覆盖率 | 接收精度 | 云端减少 | 危险跳过 | 复杂图误送 OCR |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in candidates:
        report_lines.append(
            f"| {item['model']} | {percent(item['typeAccuracy'])} | "
            f"{percent(item['routeRawAccuracy'])} | {percent(item['frozenCoverage'])} | "
            f"{percent(item['frozenAcceptedPrecision'])} | "
            f"{percent(item['estimatedCloudReduction'])} | {item['dangerousSkipCount']} | "
            f"{item['complexToLocalOcrCount']} |"
        )
    if recommendation == "pause_broad_local_router_integration":
        conclusion = [
            "当前建议暂停把通用零样本路由器接入正式管线。三种候选在冻结安全策略下能够真正留在本地的",
            f"图片最多只有 {percent(max_reduction)}；收益不足以抵消模型磁盘、内存和维护成本。",
            "二维码高置信度识别可作为后续独立、确定性更强的小实验；其他图片继续走 `uncertain → Qwen`。",
        ]
    else:
        conclusion = [
            "冻结安全策略显示出值得复核的本地覆盖率；在正式集成前仍需使用独立保留集确认风险。",
            "任何低置信度结果继续走 `uncertain → Qwen`。",
        ]
    report_lines.extend(
        [
            "",
            "## 结论",
            "",
            *conclusion,
            "",
            "工作簿审核状态仍使这些结果属于暂定基线。阈值表只用于观察，不得回写成 zero-shot-v1。",
            "",
        ]
    )
    atomic_text(output_directory / "report.md", "\n".join(report_lines))
    return output_directory / "comparison.json", comparison


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated local image router benchmark")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser("inventory")
    inventory.add_argument("--image-root", type=Path, default=DEFAULT_IMAGE_ROOT)
    inventory.add_argument(
        "--annotation-inventory", type=Path, default=DEFAULT_ANNOTATION_INVENTORY
    )
    inventory.add_argument("--output", type=Path, default=DEFAULT_INPUT_MANIFEST)

    infer = subparsers.add_parser("infer")
    infer.add_argument("--input-manifest", type=Path, default=DEFAULT_INPUT_MANIFEST)
    infer.add_argument("--categories", type=Path, default=DEFAULT_CATEGORIES)
    infer.add_argument("--models", type=Path, default=DEFAULT_MODELS)
    infer.add_argument(
        "--model", required=True, choices=sorted(read_json(DEFAULT_MODELS)["models"])
    )
    infer.add_argument("--run-name", required=True)
    infer.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    infer.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    infer.add_argument("--device", default="cpu", choices=["cpu"])
    infer.add_argument("--threads", type=int, default=8)
    infer.add_argument("--offline", action="store_true")

    score = subparsers.add_parser("score")
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    score.add_argument("--mapping", type=Path, default=DEFAULT_BENCHMARK_MAPPING)
    score.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    score.add_argument("--score-name")

    compare = subparsers.add_parser("compare")
    compare.add_argument("--scores", nargs="+", type=Path, required=True)
    compare.add_argument("--comparison-name", required=True)
    compare.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    compare.add_argument("--work-root", type=Path, default=DEFAULT_WORK_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inventory":
            result = create_input_manifest(
                args.image_root,
                args.annotation_inventory if args.annotation_inventory.exists() else None,
                args.output,
            )
            print(json.dumps(result["statistics"], ensure_ascii=False, indent=2))
        elif args.command == "infer":
            path, result = run_inference(
                args.input_manifest,
                args.categories,
                args.models,
                args.model,
                args.run_name,
                args.output_root,
                args.work_root,
                args.device,
                args.threads,
                args.offline,
            )
            print(
                json.dumps(
                    {
                        "runManifest": str(path),
                        "status": result["status"],
                        "statistics": result.get("statistics"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "score":
            path, result = score_predictions(
                args.predictions,
                args.workbook,
                args.mapping,
                args.output_root,
                args.score_name,
            )
            print(
                json.dumps(
                    {
                        "score": str(path),
                        "model": result["model"],
                        "frozenPolicy": result["frozenPolicy"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            path, result = compare_scores(
                args.scores,
                args.output_root,
                args.comparison_name,
                args.work_root,
            )
            print(
                json.dumps(
                    {
                        "comparison": str(path),
                        "recommendation": result["recommendation"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        return 0
    except (FileExistsError, OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
