"""Collect per-run Unix child resource counters without an external timing tool."""

import json
import resource
import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    result = subprocess.run(sys.argv[2:], check=False)
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    Path(sys.argv[1]).write_text(
        json.dumps(
            {
                "max_process_rss_kib": usage.ru_maxrss,
                "input_blocks": usage.ru_inblock,
                "output_blocks": usage.ru_oublock,
            }
        )
    )
    sys.exit(result.returncode if result.returncode >= 0 else 128 - result.returncode)
