"""The ffmpeg command a plan renders to.

Rendering, not deciding. :mod:`trackstarr.planner` works out what a file
needs; this turns that into an argument list and :mod:`trackstarr.executor`
runs it. The split is what lets ``plan`` print the exact command without
going near a rewrite.

Pure. The only thing it reads is the plan it is handed.
"""

import os

from .layouts import encode_settings
from .media import GENERATED_TAG
from .planner import Plan
from .policy import MUXERS


def ffmpeg_args(plan: Plan, dest: str) -> list[str]:
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", plan.path]
    for out in plan.streams:
        args += ["-map", f"0:{out.src}"]
    args += ["-map_chapters", "0", "-c", "copy"]

    for out_index, out in enumerate(plan.streams):
        if out.encode:
            # A fresh encode must not inherit its source's tags: mkvmerge
            # statistics would advertise the old track's numbers on the new.
            args += [f"-map_metadata:s:{out_index}", "-1"]
        else:
            # One explicit per-stream mapping disables the default copy for
            # every stream, so each copied stream re-maps its own.
            args += [f"-map_metadata:s:{out_index}", f"0:s:{out.src}"]

    audio_streams = (out for out in plan.streams if out.kind == "audio")
    for idx, out in enumerate(audio_streams):
        if out.encode:
            args += [
                f"-c:a:{idx}",
                plan.policy.audio_codec,
                f"-ac:a:{idx}",
                str(out.channels),
                f"-b:a:{idx}",
                out.bitrate,
                f"-metadata:s:a:{idx}",
                f"title={out.title}",
                # Recorded so REGENERATE_DOWNMIXES can recognise this track
                # and its settings on a later pass.
                f"-metadata:s:a:{idx}",
                f"{GENERATED_TAG}={encode_settings(plan.policy.audio_codec, out.bitrate)}",
                # Dispositions come from the source, so without this the file
                # ends up with two default audio tracks.
                f"-disposition:a:{idx}",
                "0",
            ]
            if out.lang:
                args += [f"-metadata:s:a:{idx}", f"language={out.lang}"]
        elif out.clear_title:
            args += [f"-metadata:s:a:{idx}", "title="]
        elif out.title:
            # MP4 drops track names on a plain copy, which blinds the
            # commentary and SDH tests next pass. Redundant for Matroska.
            args += [f"-metadata:s:a:{idx}", f"title={out.title}"]

    sub_streams = (out for out in plan.streams if out.kind == "subtitle")
    for idx, out in enumerate(sub_streams):
        if out.sub_codec:
            args += [f"-c:s:{idx}", out.sub_codec]
        if out.clear_title:
            args += [f"-metadata:s:s:{idx}", "title="]
        elif out.title:
            args += [f"-metadata:s:s:{idx}", f"title={out.title}"]

    if plan.clear_container_title:
        args += ["-metadata", "title="]

    # The staging name has no extension for ffmpeg to infer a muxer from, so
    # name it from the container the plan actually writes.
    args += ["-f", MUXERS[os.path.splitext(plan.out_path)[1].lower()]]
    args += ["-max_muxing_queue_size", "9999", dest]
    return args
