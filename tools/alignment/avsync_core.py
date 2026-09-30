"""Load only pinned AVSync visual functions from a separately supplied checkout."""

import ast
import glob
import importlib
import logging
import os
import re
import subprocess
import time
from pathlib import Path

from corpus import digest
from visual import assess

REVISION = "ece9cb6e66b4b7aa7437f23e5b21574aced6cdcc"
SOURCE_SHA256 = "28c4a72403062ae371cd860d90ea2a9114ecb8cdbd56241dec52821df43f2b34"
FUNCTIONS = {
    "run_ffmpeg",
    "get_file_duration",
    "extract_frames_ffmpeg",
    "filter_similar_ref_images",
    "filter_temporal_inconsistency",
    "run_image_pairing_stage",
}


def load_core(path: Path, ffmpeg: str, ffprobe: str) -> dict:
    if digest(path) != SOURCE_SHA256:
        raise ValueError("expected the pinned unmodified AVSync_v14.py")
    tree = ast.parse(path.read_text())
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in FUNCTIONS]
    if {f.name for f in functions} != FUNCTIONS:
        raise ValueError("missing visual core functions")
    namespace = {
        "os": os,
        "glob": glob,
        "re": re,
        "subprocess": subprocess,
        "time": time,
        "logger": logging.getLogger("avsync"),
        "cv2": importlib.import_module("cv2"),
        "np": importlib.import_module("numpy"),
        "tqdm": importlib.import_module("tqdm").tqdm,
        "Image": importlib.import_module("PIL.Image"),
        "imagehash": importlib.import_module("imagehash"),
        "similarity_libs_available": True,
        "RESIZE_WIDTH": 640,
        "RESIZE_HEIGHT": 360,
        "MATCH_WINDOW_PERCENT": 0.06,
        "ANCHOR_FOLLOW_FORWARD_WINDOW_S": 10.0,
        "FFMPEG_EXEC": ffmpeg,
        "FFPROBE_EXEC": ffprobe,
    }
    # Execute only these six verified upstream definitions, never main or checkpoint code.
    exec(  # noqa: S102 - only hash-verified upstream function definitions
        compile(ast.Module(body=list(functions), type_ignores=[]), str(path), "exec"), namespace
    )
    upstream_run = namespace["run_ffmpeg"]

    def run_compatible(command, *args, **kwargs):
        # Current FFmpeg removed -vsync. Preserve upstream VFR output semantics.
        command = ["-fps_mode" if value == "-vsync" else value for value in command]
        result = upstream_run(command, *args, **kwargs)
        if not result[0]:
            raise RuntimeError("AVSync frame extraction failed")
        return result

    namespace["run_ffmpeg"] = run_compatible
    return namespace


def analyse(
    path: Path,
    ffmpeg: str,
    ffprobe: str,
    target: Path,
    source: Path,
    workspace: Path,
    target_bounds: tuple,
    source_bounds: tuple,
) -> dict:
    core = load_core(path, ffmpeg, ffprobe)
    # Upstream resets input timestamps. Avoid presenting those as container timestamps.
    if target_bounds[0] != 0 or source_bounds[0] != 0:
        return {"status": "unsupported", "reason": "nonzero_start_time"}
    pairs = core["run_image_pairing_stage"](
        str(target),
        str(source),
        str(workspace),
        0.25,
        0.7,
        4,
    )
    anchors = [
        {"target_time_us": round(t * 1_000_000), "source_time_us": round(s * 1_000_000)}
        for _, _, t, s in (pairs or [])
    ]
    result = assess(anchors, target_bounds, source_bounds)
    # Upstream uses the first anchor to constrain subsequent searches, including validation.
    if result["status"] == "proposed":
        result.update(status="review_required", reason="dependent_search_and_unknown_ambiguity")
    return result | {
        "independent_validation_search": False,
        "ambiguity_evidence": False,
        "upstream_parameters": {
            "scene_threshold": 0.25,
            "match_threshold": 0.7,
            "similarity_threshold": 4,
        },
        "source_sha256": SOURCE_SHA256,
        "source_revision": REVISION,
        "compatibility_adapter": [
            "-vsync vfr replaced with -fps_mode vfr",
            "extraction failures raise",
        ],
    }
