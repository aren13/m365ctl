from __future__ import annotations

from fazla_od.mutate._pwsh import normalize_recycle_dir_name


def test_normalize_strips_graph_drives_prefix_with_root_marker():
    """Full Graph path (the shape recorded by the delete audit entry)
    drops everything up to and including ``root:``."""
    assert (
        normalize_recycle_dir_name("/drives/b!abc/root:/_fazla_smoke2")
        == "_fazla_smoke2"
    )


def test_normalize_strips_short_drive_root_prefix():
    """``/drive/root:/...`` (Graph's short form) also collapses to the
    site-relative tail."""
    assert (
        normalize_recycle_dir_name("/drive/root:/Folder/Sub")
        == "Folder/Sub"
    )


def test_normalize_is_idempotent_on_already_relative_path():
    """A path with no ``root:`` marker keeps its content; only the
    leading slash is trimmed so PnP's wildcard match sees a clean tail."""
    assert normalize_recycle_dir_name("/Folder/Sub") == "Folder/Sub"


def test_normalize_empty_string_returns_empty():
    """Empty input stays empty — callers rely on this to pass ``""`` to
    PnP when the audit record had no parent_path."""
    assert normalize_recycle_dir_name("") == ""
