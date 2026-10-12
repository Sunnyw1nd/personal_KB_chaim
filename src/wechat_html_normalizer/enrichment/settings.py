"""Shared frozen configuration, independent of command-line parsing."""

from pathlib import Path

PROMPT_VERSION = "image-enrichment-v1"
DEFAULT_PROMPT_PATH = Path(__file__).with_name("prompts") / f"{PROMPT_VERSION}.md"
FROZEN_PROMPT_SHA256 = {
    "compact-json-v1": "7db1cca91f4131ff32ea18b367603ab6147d2a1bc9fc2df9ddcc8905da0621f2",
    "image-enrichment-v1": "d81daa6b18608d8cc2dae1ff46b83bd69abf0a3e82172015de4c76e5660eb4f9",
    "image-enrichment-v2": "4096d161eeb0f364523619e3d48de73e802d78a8da1e90643b251feede0267a9",
}
