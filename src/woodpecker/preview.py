"""Print the morning message that Woodpecker would send now, with live data, without a
send and without a write: `python -m woodpecker.preview`. The text goes to stdout, and
the sidecar is copied into memory, so the frog record and the open prompt stay as they are."""

import sqlite3

from . import config, llm, scheduler
from .store import Store
from .vikunja import VikunjaClient


def main() -> None:
    disk = sqlite3.connect(f"file:{config.sidecar_path()}?mode=ro", uri=True)
    conn = sqlite3.connect(":memory:")
    disk.backup(conn)
    disk.close()
    conn.row_factory = sqlite3.Row
    vk = VikunjaClient(config.vikunja_url(), config.vikunja_token(), config.vikunja_project_id())
    client = llm.build_client(config.anthropic_api_key())
    # 09:00 today, so quiet hours never skip the preview.
    now = config.now_paris().replace(hour=config.FOCUS_HOUR, minute=0)
    scheduler.send_morning(Store(vk, conn), print, client, now, config.vault_path())


if __name__ == "__main__":
    main()
