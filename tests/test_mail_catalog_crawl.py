from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


from m365ctl.common.graph import GraphError
from m365ctl.mail.catalog.crawl import (
    crawl_folder,
    refresh_mailbox,
)
from m365ctl.mail.catalog.db import open_catalog


def _msg(mid: str, *, folder: str = "fld-inbox", subject: str = "x") -> dict:
    return {
        "id": mid,
        "parentFolderId": folder,
        "subject": subject,
        "receivedDateTime": "2026-04-01T10:00:00Z",
        "from": {"emailAddress": {"name": "A", "address": "a@example.com"}},
        "isRead": False,
    }


def test_crawl_folder_first_run_full_sync(tmp_path: Path) -> None:
    graph = MagicMock()
    # Drain loop: first round yields items + deltaLink, second round
    # (called from the deltaLink) is empty + deltaLink → loop exits.
    graph.get_paginated.side_effect = [
        iter([
            ([_msg("m1"), _msg("m2")], None),
            ([_msg("m3")], "https://graph.microsoft.com/.../delta?token=DELTA1"),
        ]),
        iter([([], "https://graph.microsoft.com/.../delta?token=DELTA1")]),
    ]
    with open_catalog(tmp_path / "m.duckdb") as conn:
        outcome = crawl_folder(
            graph,
            conn=conn,
            mailbox_upn="me",
            folder_id="fld-inbox",
            folder_path="Inbox",
            initial_path="/me/mailFolders/fld-inbox/messages/delta",
            page_top=200,
        )
        assert outcome.messages_seen == 3
        assert outcome.delta_link.endswith("DELTA1")
        assert outcome.status == "ok"
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM mail_messages WHERE mailbox_upn = 'me'"
        ).fetchone()
        assert n == 3
        (link,) = conn.execute(
            "SELECT delta_link FROM mail_deltas "
            "WHERE mailbox_upn = 'me' AND folder_id = 'fld-inbox'"
        ).fetchone()
        assert link.endswith("DELTA1")


def test_crawl_folder_resumes_from_stored_delta_link(tmp_path: Path) -> None:
    graph = MagicMock()
    graph.get_paginated.side_effect = [
        iter([([_msg("m4")], "https://graph.microsoft.com/.../delta?token=DELTA2")]),
        iter([([], "https://graph.microsoft.com/.../delta?token=DELTA2")]),
    ]
    with open_catalog(tmp_path / "m.duckdb") as conn:
        conn.execute(
            "INSERT INTO mail_deltas (mailbox_upn, folder_id, delta_link, "
            "last_refreshed_at, last_status) VALUES (?, ?, ?, ?, ?)",
            ["me", "fld-inbox", "https://stored/delta-prior", "2026-04-01", "ok"],
        )
        crawl_folder(
            graph,
            conn=conn,
            mailbox_upn="me",
            folder_id="fld-inbox",
            folder_path="Inbox",
            initial_path="/me/mailFolders/fld-inbox/messages/delta",
            page_top=200,
        )
    # The first pagination call should use the stored delta_link as
    # the starting path (drain loop subsequently calls from DELTA2).
    first_call_path = graph.get_paginated.call_args_list[0].args[0]
    assert first_call_path == "https://stored/delta-prior"


def test_crawl_folder_410_sync_state_not_found_restarts(tmp_path: Path) -> None:
    graph = MagicMock()
    # First call raises, second call (after we drop delta_link) succeeds.
    graph.get_paginated.side_effect = [
        _raises_sync_state_not_found(),
        iter([([_msg("m5")], "https://graph.microsoft.com/.../delta?token=FRESH")]),
        iter([([], "https://graph.microsoft.com/.../delta?token=FRESH")]),
    ]
    with open_catalog(tmp_path / "m.duckdb") as conn:
        conn.execute(
            "INSERT INTO mail_deltas (mailbox_upn, folder_id, delta_link, "
            "last_refreshed_at, last_status) VALUES (?, ?, ?, ?, ?)",
            ["me", "fld-inbox", "https://stored/delta-expired", "2026-04-01", "ok"],
        )
        outcome = crawl_folder(
            graph,
            conn=conn,
            mailbox_upn="me",
            folder_id="fld-inbox",
            folder_path="Inbox",
            initial_path="/me/mailFolders/fld-inbox/messages/delta",
            page_top=200,
        )
        assert outcome.status == "restarted"
        assert outcome.messages_seen == 1
        (status,) = conn.execute(
            "SELECT last_status FROM mail_deltas "
            "WHERE mailbox_upn = 'me' AND folder_id = 'fld-inbox'"
        ).fetchone()
        assert status == "restarted"
    # And the second pagination call must have used the initial_path, not
    # the expired stored deltaLink — otherwise the "restart" is fake and
    # we'd loop on the stale token.
    second_call_path = graph.get_paginated.call_args_list[1].args[0]
    assert second_call_path == "/me/mailFolders/fld-inbox/messages/delta"


def _raises_sync_state_not_found():
    def _gen():
        raise GraphError("HTTP410 syncStateNotFound: token expired")
        yield  # pragma: no cover
    return _gen()


def test_refresh_mailbox_picks_default_well_known_folders(tmp_path: Path) -> None:
    """refresh_mailbox enumerates Inbox/Sent/Drafts/DeletedItems by well-known name."""
    graph = MagicMock()
    # mail_folders root listing → 4 well-known folders.
    folders = [
        {
            "id": f"fld-{wk}",
            "displayName": wk.title(),
            "wellKnownName": wk,
            "childFolderCount": 0,
            "totalItemCount": 0,
            "unreadItemCount": 0,
        }
        for wk in ("inbox", "sentitems", "drafts", "deleteditems")
    ]
    graph.get_paginated.side_effect = (
        # First call: list_folders top-level.
        [iter([(folders, None)])]
        # Then four delta crawls, each one page, no items.
        + [iter([([], f"delta-{wk}")]) for wk in
           ("inbox", "sentitems", "drafts", "deleteditems")]
    )
    # Graph's /mailFolders listing doesn't return wellKnownName, so the
    # crawler resolves each well-known target via graph.get(...) directly.
    graph.get.side_effect = [
        {"id": f"fld-{wk}", "displayName": wk.title()}
        for wk in ("inbox", "sentitems", "drafts", "deleteditems")
    ]
    with open_catalog(tmp_path / "m.duckdb") as conn:
        outcomes = refresh_mailbox(
            graph,
            conn=conn,
            mailbox_spec="me",
            mailbox_upn="me",
            auth_mode="delegated",
        )
        assert {o.folder_id for o in outcomes} == {
            "fld-inbox", "fld-sentitems", "fld-drafts", "fld-deleteditems",
        }
        assert all(o.status == "ok" for o in outcomes)
        (n,) = conn.execute("SELECT COUNT(*) FROM mail_folders").fetchone()
        assert n == 4
