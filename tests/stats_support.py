import logging
import os
import types


def stats_bucket(*, runs, deleted, channels=None, **fields):
    """Build a valid stats bucket while keeping scenario values explicit."""
    return {
        "runs": runs,
        "deleted": deleted,
        "channels": {} if channels is None else channels,
        **fields,
    }


class StatsConfigMixin:
    def _config_stub(self, tempdir: str):
        return types.SimpleNamespace(
            DATA_DIR=tempdir,
            STATS_FILE=os.path.join(tempdir, "stats.json"),
            log=logging.getLogger("test-stats"),
        )
