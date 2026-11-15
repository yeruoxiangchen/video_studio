"""Run the real Piper server on at most two CPUs without requiring cgroup CPU delegation."""

from __future__ import annotations

import json
import os


def main() -> None:
    available = sorted(os.sched_getaffinity(0))
    selected = available[-2:]
    os.sched_setaffinity(0, selected)
    print(json.dumps({"provider": "piper", "cpu_affinity": selected}), flush=True)

    import uvicorn

    uvicorn.run(
        "camcat.local_providers.speech:app", host="0.0.0.0", port=18961, workers=1, access_log=False
    )


if __name__ == "__main__":
    main()
