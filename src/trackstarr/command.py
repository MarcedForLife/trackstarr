"""The ffmpeg command a plan renders to. Pure: :mod:`trackstarr.planner`
decides, this renders, :mod:`trackstarr.executor` runs, which is what lets
``plan`` print the exact command."""

import os
from collections.abc import Sequence

from .media import GENERATED_TAG
from .planner import Plan, PreparedTrack
from .policy import MUXERS
from .tracks import encode_settings


def ffmpeg_args(plan: Plan, dest: str, prepared: Sequence[PreparedTrack] = ()) -> list[str]:
    if plan.inputs and tuple(track.source for track in prepared) != plan.inputs:
        raise ValueError("source inputs require matching preparation before rendering")
    args = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error"]
    if any(out.dv_strip for out in plan.streams):
        # dovi_rpu drops a packet it cannot parse and ffmpeg still exits 0,
        # which would only surface after the whole rewrite.
        args.append("-xerror")
    if prepared:
        args += ["-copyts"]
    args += ["-i", plan.path]
    for track in prepared:
        args += ["-i", track.path]
    for out in plan.streams:
        index = 0 if prepared and out.source.input_index else out.source.stream_index
        args += ["-map", f"{out.source.input_index}:{index}"]
    if prepared:
        args += ["-map_metadata", "0", "-avoid_negative_ts", "disabled"]
    args += ["-map_chapters", "0", "-c", "copy"]

    muxer = MUXERS[os.path.splitext(plan.out_path)[1].lower()]
    for out_index, out in enumerate(plan.streams):
        if out.encode:
            # A fresh encode must not inherit its source's tags, such as
            # mkvmerge's statistics.
            args += [f"-map_metadata:s:{out_index}", "-1"]
        else:
            # One explicit per-stream mapping disables the default copy for
            # all, so each copied stream re-maps its own.
            index = 0 if prepared and out.source.input_index else out.source.stream_index
            args += [
                f"-map_metadata:s:{out_index}",
                f"{out.source.input_index}:s:{index}",
            ]
            if muxer == "matroska" and out.src_bitrate:
                # Matroska has no per-stream bitrate field, so a natively
                # reported rate is written as the BPS tag mkvmerge uses. Only
                # fills a gap; a source's own BPS tag is left alone.
                args += [f"-metadata:s:{out_index}", f"BPS={out.src_bitrate}"]

    video_streams = (out for out in plan.streams if out.kind == "video")
    for idx, out in enumerate(video_streams):
        if out.dv_strip:
            args += [f"-bsf:v:{idx}", "dovi_rpu=strip=1"]

    audio_streams = (out for out in plan.streams if out.kind == "audio")
    for idx, out in enumerate(audio_streams):
        retimed = bool(
            prepared and out.source.input_index and prepared[out.source.input_index - 1].encode
        )
        if out.encode or retimed:
            args += [
                f"-c:a:{idx}",
                out.codec,
                f"-ac:a:{idx}",
                str(out.channels),
                f"-b:a:{idx}",
                out.bitrate,
            ]
        if retimed and not out.encode:
            # Keep descriptive metadata, but discard statistics of the original
            # encoding and any generated-downmix marker inherited from it.
            for tag in (
                "BPS",
                "BPS-eng",
                "NUMBER_OF_BYTES",
                "NUMBER_OF_BYTES-eng",
                "NUMBER_OF_FRAMES",
                "NUMBER_OF_FRAMES-eng",
                "_STATISTICS_WRITING_APP",
                "_STATISTICS_WRITING_DATE_UTC",
                "_STATISTICS_TAGS",
                GENERATED_TAG,
            ):
                args += [f"-metadata:s:a:{idx}", f"{tag}="]
        if prepared and out.source.input_index and not out.encode:
            args += [f"-disposition:a:{idx}", "-default"]
        if out.encode:
            args += [
                f"-metadata:s:a:{idx}",
                f"title={out.title}",
                # So the regenerate rule recognises this track later.
                f"-metadata:s:a:{idx}",
                f"{GENERATED_TAG}={encode_settings(out.codec, out.bitrate)}",
                # Without this the file has two default audio tracks.
                f"-disposition:a:{idx}",
                "0",
            ]
        elif out.clear_title:
            args += [f"-metadata:s:a:{idx}", "title="]
        elif out.title:
            # MP4 drops track names on a plain copy, blinding the commentary
            # and SDH tests next pass.
            args += [f"-metadata:s:a:{idx}", f"title={out.title}"]
        # Copies too, where the tag_original rule has one to write.
        if out.lang:
            args += [f"-metadata:s:a:{idx}", f"language={out.lang}"]

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

    # The staging name has no extension to infer a muxer from.
    args += ["-f", muxer]
    args += ["-max_muxing_queue_size", "9999", dest]
    return args
