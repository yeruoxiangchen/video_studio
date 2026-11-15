import sys

import pytest
from camcat.media.ffmpeg import MediaCommandError, _run


def test_media_command_deadline_terminates_work_and_raises_a_useful_error() -> None:
    with pytest.raises(MediaCommandError, match="timed out"):
        _run([sys.executable, "-c", "import time; time.sleep(60)"], timeout_seconds=0.05)
