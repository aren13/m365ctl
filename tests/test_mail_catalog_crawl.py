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


def test_crawl_folder_respects_max_rounds(tmp_path: Path) -> None:
    """When max_rounds is set, drain stops after N rounds and persists the
    deltaLink from the last completed round so subsequent refreshes resume."""
    graph = MagicMock()
    # Three rounds of content; cap at 2 → we MUST NOT request the 3rd round.
    graph.get_paginated.side_effect = [
        iter([([_msg("m1"), _msg("m2")],
               "https://graph.microsoft.com/.../delta?token=ROUND1")]),
        iter([([_msg("m3")],
               "https://graph.microsoft.com/.../delta?token=ROUND2")]),
        iter([([_msg("m4")],
               "https://graph.microsoft.com/.../delta?token=ROUND3")]),
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
            max_rounds=2,
        )
        assert outcome.messages_seen == 3  # m1, m2 from round 1 + m3 from round 2
        assert outcome.truncated is True
        assert outcome.delta_link.endswith("ROUND2")
        # Third round must NOT have been requested.
        assert graph.get_paginated.call_count == 2
        (link,) = conn.execute(
            "SELECT delta_link FROM mail_deltas "
            "WHERE mailbox_upn = 'me' AND folder_id = 'fld-inbox'"
        ).fetchone()
        assert link.endswith("ROUND2")


def test_crawl_folder_no_cap_sets_truncated_false(tmp_path: Path) -> None:
    """Without --max-rounds, a normal full drain reports truncated=False."""
    graph = MagicMock()
    graph.get_paginated.side_effect = [
        iter([([_msg("m1")], "https://graph.microsoft.com/.../delta?token=DELTA1")]),
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
        assert outcome.truncated is False


def test_drain_delta_passes_select_on_first_call(tmp_path):
    """First call to /messages/delta carries $select; subsequent links don't override."""
    from m365ctl.mail.catalog.crawl import _drain_delta
    graph = MagicMock()
    graph.get_paginated.side_effect = [
        iter([([_msg("m1")], "https://graph.microsoft.com/.../delta?token=DELTA1")]),
        iter([([], "https://graph.microsoft.com/.../delta?token=DELTA1")]),
    ]
    with open_catalog(tmp_path / "m.duckdb") as conn:
        _drain_delta(
            graph, conn,
            mailbox_upn="me", folder_id="fld-inbox", folder_path="Inbox",
            start_path="/me/mailFolders/fld-inbox/messages/delta",
            page_top=200,
        )
    # First call uses params={"$select": "<fields>"}; subsequent (deltaLink)
    # call uses no params (the link encodes them).
    first_kwargs = graph.get_paginated.call_args_list[0].kwargs
    assert "params" in first_kwargs
    assert "$select" in first_kwargs["params"]
    select_value = first_kwargs["params"]["$select"]
    # Sanity: must include the must-have fields normalize_message reads.
    for field in ("id", "internetMessageId", "from", "receivedDateTime",
                  "isRead", "bodyPreview", "ccRecipients"):
        assert field in select_value, f"{field!r} missing from $select"
    # Subsequent deltaLink call should NOT pass $select (the link encodes it).
    second_kwargs = graph.get_paginated.call_args_list[1].kwargs
    assert "params" not in second_kwargs or not second_kwargs.get("params")


def test_drain_delta_uses_transaction_per_round(tmp_path):
    """Each round wraps upserts in BEGIN/COMMIT for DuckDB throughput."""
    from m365ctl.mail.catalog.crawl import _drain_delta
    graph = MagicMock()
    graph.get_paginated.side_effect = [
        iter([
            ([_msg("m1"), _msg("m2"), _msg("m3")],
             "https://graph.microsoft.com/.../delta?token=DELTA1"),
        ]),
        iter([([], "https://graph.microsoft.com/.../delta?token=DELTA1")]),
    ]

    # Wrap conn.execute calls in a list so we can inspect the order.
    with open_catalog(tmp_path / "m.duckdb") as conn:
        executed: list[str] = []
        original_execute = conn.execute

        class _SpyConn:
            def __init__(self, inner):
                self._inner = inner
            def execute(self, sql, *args, **kwargs):
                executed.append(sql.strip().split()[0].upper())   # first SQL keyword
                return original_execute(sql, *args, **kwargs)
            def __getattr__(self, name):
                return getattr(self._inner, name)

        spy_conn = _SpyConn(conn)

        _drain_delta(
            graph, spy_conn,
            mailbox_upn="me", folder_id="fld-inbox", folder_path="Inbox",
            start_path="/me/mailFolders/fld-inbox/messages/delta",
            page_top=200,
        )
    # Round 1: BEGIN, 3 INSERTs, COMMIT. Round 2: BEGIN, 0 INSERTs, COMMIT.
    assert executed.count("BEGIN") == 2
    assert executed.count("COMMIT") == 2
    # 3 message upserts in round 1, 0 in round 2.
    insert_count = sum(1 for s in executed if s == "INSERT")
    assert insert_count == 3


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
