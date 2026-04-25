"""CLI tests for `mail rules` CRUD verbs (Phase 8 G3).

These tests mock at the CLI module boundary:
  - `DelegatedCredential` / `GraphClient` are patched so no live auth/HTTP.
  - The five executors and `list_rules` / `list_folders` / `resolve_folder_path`
    are patched so the CLI's parsing + dispatch logic is what's exercised.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from m365ctl.mail.cli import rules as cli_rules
from m365ctl.mail.models import Folder, Rule


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f"""
tenant_id    = "00000000-0000-0000-0000-000000000000"
client_id    = "11111111-1111-1111-1111-111111111111"
cert_path    = "{tmp_path / 'c.pem'}"
cert_public  = "{tmp_path / 'p.cer'}"
default_auth = "delegated"

[scope]
allow_drives    = ["me"]
allow_mailboxes = ["me"]

[catalog]
path = "{tmp_path / 'cat.duckdb'}"

[mail]
catalog_path = "{tmp_path / 'mail.duckdb'}"

[logging]
ops_dir = "{tmp_path / 'logs'}"
""".lstrip()
    )
    return cfg


def _folder(folder_id: str, path: str) -> Folder:
    return Folder(
        id=folder_id,
        mailbox_upn="me",
        display_name=path.split("/")[-1],
        parent_id=None,
        path=path,
        total_items=0,
        unread_items=0,
        child_folder_count=0,
        well_known_name=None,
    )


def _rule(
    rule_id: str = "rule-1",
    *,
    display_name: str = "r",
    sequence: int = 10,
    is_enabled: bool = True,
    conditions: dict | None = None,
    actions: dict | None = None,
    exceptions: dict | None = None,
) -> Rule:
    return Rule(
        id=rule_id,
        display_name=display_name,
        sequence=sequence,
        is_enabled=is_enabled,
        has_error=False,
        is_read_only=False,
        conditions=conditions or {},
        actions=actions or {},
        exceptions=exceptions or {},
    )


def _common_patches():
    """Stack of context managers commonly used by every CLI test below.

    Each test calls _common_patches() inside an `ExitStack`-style `with`.
    Returns a dict of mocks keyed by their attribute name so tests can
    inspect call args without re-patching.
    """
    raise NotImplementedError("use _patched(...) helper instead")


class _Patched:
    """Convenience: patch credential, graph, and execute_* / lookups."""

    def __init__(self, *, list_rules_return=None, list_folders_return=None,
                 resolve_folder_path_return=None):
        self.list_rules_return = list_rules_return or []
        self.list_folders_return = list_folders_return or []
        self.resolve_folder_path_return = resolve_folder_path_return

    def __enter__(self):
        # Stub credential at the source ( _common ) so load_and_authorize
        # never instantiates a real MSAL ConfidentialClient.
        self._patches = [
            patch("m365ctl.mail.cli._common.DelegatedCredential"),
            patch("m365ctl.mail.cli._common.AppOnlyCredential"),
            patch("m365ctl.mail.cli.rules.GraphClient"),
            patch("m365ctl.mail.cli.rules.list_rules",
                  return_value=self.list_rules_return),
            patch("m365ctl.mail.cli.rules.list_folders",
                  return_value=iter(self.list_folders_return)),
            patch("m365ctl.mail.cli.rules.resolve_folder_path",
                  return_value=self.resolve_folder_path_return or ""),
            patch("m365ctl.mail.cli.rules.execute_create"),
            patch("m365ctl.mail.cli.rules.execute_update"),
            patch("m365ctl.mail.cli.rules.execute_delete"),
            patch("m365ctl.mail.cli.rules.execute_set_enabled"),
            patch("m365ctl.mail.cli.rules.execute_reorder"),
        ]
        self.cred_cls = self._patches[0].__enter__()
        self.app_cred_cls = self._patches[1].__enter__()
        self.graph_cls = self._patches[2].__enter__()
        self.list_rules = self._patches[3].__enter__()
        self.list_folders = self._patches[4].__enter__()
        self.resolve_folder_path = self._patches[5].__enter__()
        self.execute_create = self._patches[6].__enter__()
        self.execute_update = self._patches[7].__enter__()
        self.execute_delete = self._patches[8].__enter__()
        self.execute_set_enabled = self._patches[9].__enter__()
        self.execute_reorder = self._patches[10].__enter__()

        self.cred_cls.return_value.get_token.return_value = "tok"
        self.app_cred_cls.return_value.get_token.return_value = "tok"
        self.graph_cls.return_value = MagicMock()

        ok = MagicMock()
        ok.status = "ok"
        ok.error = None
        ok.after = {"id": "new-rule-id"}
        self.execute_create.return_value = ok
        self.execute_update.return_value = ok
        self.execute_delete.return_value = ok
        self.execute_set_enabled.return_value = ok
        self.execute_reorder.return_value = ok
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.__exit__(*exc)


# --------------------------------------------------------------------------
# 1. create --from-file --confirm  → execute_create called with parsed body
# --------------------------------------------------------------------------

def test_create_from_file_confirm_calls_execute_create(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    rule_yaml = tmp_path / "rule.yaml"
    rule_yaml.write_text(
        """
display_name: archive-newsletters
sequence: 10
enabled: true
conditions:
  sender_contains: ["@news.example.com"]
actions:
  move_to_folder: "Archive/Newsletters"
  mark_as_read: true
""".lstrip()
    )
    folders = [
        _folder("fld-arch", "Archive"),
        _folder("fld-arch-news", "Archive/Newsletters"),
    ]
    with _Patched(
        list_folders_return=folders,
        resolve_folder_path_return="fld-arch-news",
    ) as p:
        rc = cli_rules.main([
            "--config", str(cfg),
            "create", "--from-file", str(rule_yaml), "--confirm",
        ])
    assert rc == 0
    p.execute_create.assert_called_once()
    op = p.execute_create.call_args.args[0]
    assert op.action == "mail.rule.create"
    body = op.args["body"]
    assert body["displayName"] == "archive-newsletters"
    assert body["sequence"] == 10
    assert body["isEnabled"] is True
    # Folder path was translated to id.
    assert body["actions"]["moveToFolder"] == "fld-arch-news"
    assert body["actions"]["markAsRead"] is True
    assert body["conditions"]["senderContains"] == ["@news.example.com"]


# --------------------------------------------------------------------------
# 2. create --from-file (no --confirm) → dry-run, NO execute_create call
# --------------------------------------------------------------------------

def test_create_without_confirm_dry_runs(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    rule_yaml = tmp_path / "rule.yaml"
    rule_yaml.write_text(
        "display_name: r\nsequence: 1\nenabled: true\nactions: {delete: true}\n"
    )
    with _Patched() as p:
        rc = cli_rules.main([
            "--config", str(cfg),
            "create", "--from-file", str(rule_yaml),
        ])
    assert rc == 0
    p.execute_create.assert_not_called()


# --------------------------------------------------------------------------
# 3. update <id> --from-file --confirm → execute_update called
# --------------------------------------------------------------------------

def test_update_from_file_confirm_calls_execute_update(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    rule_yaml = tmp_path / "rule.yaml"
    rule_yaml.write_text(
        "display_name: renamed\nsequence: 20\nenabled: true\n"
        "actions: {mark_as_read: true}\n"
    )
    with _Patched(list_rules_return=[_rule("rule-99")]) as p:
        rc = cli_rules.main([
            "--config", str(cfg),
            "update", "rule-99",
            "--from-file", str(rule_yaml), "--confirm",
        ])
    assert rc == 0
    p.execute_update.assert_called_once()
    op = p.execute_update.call_args.args[0]
    assert op.action == "mail.rule.update"
    assert op.args["rule_id"] == "rule-99"
    assert op.args["body"]["displayName"] == "renamed"


# --------------------------------------------------------------------------
# 4a. delete <id> --confirm → execute_delete
# --------------------------------------------------------------------------

def test_delete_confirm_calls_execute_delete(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with _Patched(list_rules_return=[_rule("rule-99")]) as p:
        rc = cli_rules.main([
            "--config", str(cfg),
            "delete", "rule-99", "--confirm",
        ])
    assert rc == 0
    p.execute_delete.assert_called_once()
    op = p.execute_delete.call_args.args[0]
    assert op.action == "mail.rule.delete"
    assert op.args["rule_id"] == "rule-99"


# 4b. delete <id> (no --confirm) → exit 2 with stderr
def test_delete_without_confirm_returns_2(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_rules.main([
            "--config", str(cfg),
            "delete", "rule-99",
        ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--confirm" in err
    p.execute_delete.assert_not_called()


# --------------------------------------------------------------------------
# 5. enable <id> --confirm → execute_set_enabled(is_enabled=True)
# --------------------------------------------------------------------------

def test_enable_confirm_calls_set_enabled_true(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_rules.main([
            "--config", str(cfg),
            "enable", "rule-X", "--confirm",
        ])
    assert rc == 0
    p.execute_set_enabled.assert_called_once()
    op = p.execute_set_enabled.call_args.args[0]
    assert op.action == "mail.rule.set-enabled"
    assert op.args["rule_id"] == "rule-X"
    assert op.args["is_enabled"] is True


# --------------------------------------------------------------------------
# 6. disable <id> --confirm → execute_set_enabled(is_enabled=False)
# --------------------------------------------------------------------------

def test_disable_confirm_calls_set_enabled_false(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    with _Patched() as p:
        rc = cli_rules.main([
            "--config", str(cfg),
            "disable", "rule-X", "--confirm",
        ])
    assert rc == 0
    p.execute_set_enabled.assert_called_once()
    op = p.execute_set_enabled.call_args.args[0]
    assert op.args["rule_id"] == "rule-X"
    assert op.args["is_enabled"] is False


# --------------------------------------------------------------------------
# 7. reorder --from-file --confirm → execute_reorder with parsed ordering
# --------------------------------------------------------------------------

def test_reorder_from_file_confirm_calls_execute_reorder(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    order_yaml = tmp_path / "order.yaml"
    order_yaml.write_text(
        "- rule_id: rule-A\n  sequence: 10\n"
        "- rule_id: rule-B\n  sequence: 20\n"
    )
    with _Patched() as p:
        rc = cli_rules.main([
            "--config", str(cfg),
            "reorder", "--from-file", str(order_yaml), "--confirm",
        ])
    assert rc == 0
    p.execute_reorder.assert_called_once()
    op = p.execute_reorder.call_args.args[0]
    assert op.action == "mail.rule.reorder"
    assert op.args["ordering"] == [
        {"rule_id": "rule-A", "sequence": 10},
        {"rule_id": "rule-B", "sequence": 20},
    ]


# --------------------------------------------------------------------------
# 8. export --out PATH → writes YAML doc with rules: list
# --------------------------------------------------------------------------

def test_export_writes_yaml_with_rules_list(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    out = tmp_path / "rules.yaml"
    rules = [
        _rule("r1", display_name="rule-one", sequence=10,
              actions={"markAsRead": True}),
        _rule("r2", display_name="rule-two", sequence=20,
              actions={"delete": True}),
    ]
    with _Patched(list_rules_return=rules):
        rc = cli_rules.main([
            "--config", str(cfg),
            "export", "--out", str(out),
        ])
    assert rc == 0
    doc = yaml.safe_load(out.read_text())
    assert isinstance(doc, dict)
    assert "rules" in doc
    assert len(doc["rules"]) == 2
    names = [r["display_name"] for r in doc["rules"]]
    assert names == ["rule-one", "rule-two"]


# --------------------------------------------------------------------------
# 9. export (no --out) → writes to stdout
# --------------------------------------------------------------------------

def test_export_no_out_writes_to_stdout(tmp_path: Path, capsys) -> None:
    cfg = _write_config(tmp_path)
    rules = [_rule("r1", display_name="solo", sequence=5)]
    with _Patched(list_rules_return=rules):
        rc = cli_rules.main([
            "--config", str(cfg),
            "export",
        ])
    assert rc == 0
    out = capsys.readouterr().out
    doc = yaml.safe_load(out)
    assert "rules" in doc
    assert doc["rules"][0]["display_name"] == "solo"


# --------------------------------------------------------------------------
# 10. import --from-file --confirm → execute_create per rule
# --------------------------------------------------------------------------

def test_import_creates_each_rule(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    rules_yaml = tmp_path / "rules.yaml"
    rules_yaml.write_text(
        """
rules:
  - display_name: r1
    sequence: 10
    enabled: true
    actions: {mark_as_read: true}
  - display_name: r2
    sequence: 20
    enabled: true
    actions: {delete: true}
""".lstrip()
    )
    with _Patched() as p:
        rc = cli_rules.main([
            "--config", str(cfg),
            "import", "--from-file", str(rules_yaml), "--confirm",
        ])
    assert rc == 0
    assert p.execute_create.call_count == 2
    p.execute_delete.assert_not_called()
    bodies = [c.args[0].args["body"] for c in p.execute_create.call_args_list]
    assert [b["displayName"] for b in bodies] == ["r1", "r2"]


# --------------------------------------------------------------------------
# 11. import --replace --confirm → delete-all then create-each
# --------------------------------------------------------------------------

def test_import_replace_deletes_all_then_creates(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)
    rules_yaml = tmp_path / "rules.yaml"
    rules_yaml.write_text(
        """
rules:
  - display_name: new-r1
    sequence: 10
    enabled: true
    actions: {mark_as_read: true}
""".lstrip()
    )
    existing = [
        _rule("old-1", display_name="old-1", sequence=5),
        _rule("old-2", display_name="old-2", sequence=15),
    ]
    with _Patched(list_rules_return=existing) as p:
        rc = cli_rules.main([
            "--config", str(cfg),
            "import", "--from-file", str(rules_yaml),
            "--replace", "--confirm",
        ])
    assert rc == 0
    # Two existing rules deleted...
    assert p.execute_delete.call_count == 2
    deleted_ids = {c.args[0].args["rule_id"] for c in p.execute_delete.call_args_list}
    assert deleted_ids == {"old-1", "old-2"}
    # ...then one new rule created.
    assert p.execute_create.call_count == 1


# --------------------------------------------------------------------------
# 12. Round-trip: export → re-parse → import bodies match original Graph rules
# --------------------------------------------------------------------------

def test_export_then_import_round_trip(tmp_path: Path) -> None:
    cfg = _write_config(tmp_path)

    # Rules with mixed conditions/actions/exceptions including folder ids.
    src_rules = [
        _rule(
            "r1",
            display_name="archive-news",
            sequence=10,
            conditions={"senderContains": ["@news.example.com"]},
            actions={
                "moveToFolder": "fld-arch",
                "markAsRead": True,
                "stopProcessingRules": True,
            },
        ),
        _rule(
            "r2",
            display_name="flag-vip",
            sequence=20,
            conditions={
                "fromAddresses": [
                    {"emailAddress": {"name": "Boss", "address": "boss@example.com"}}
                ],
                "subjectContains": ["urgent"],
            },
            actions={"assignCategories": ["VIP"], "markImportance": "high"},
            exceptions={"hasAttachments": False},
        ),
        _rule(
            "r3",
            display_name="copy-receipts",
            sequence=30,
            conditions={"subjectContains": ["receipt"]},
            actions={"copyToFolder": "fld-receipts"},
        ),
    ]
    folders = [
        _folder("fld-arch", "Archive"),
        _folder("fld-receipts", "Inbox/Receipts"),
    ]

    # 1) Export.
    out = tmp_path / "exported.yaml"
    with _Patched(list_rules_return=src_rules,
                  list_folders_return=folders) as _pe:
        rc = cli_rules.main([
            "--config", str(cfg),
            "export", "--out", str(out),
        ])
    assert rc == 0
    exported = yaml.safe_load(out.read_text())
    assert len(exported["rules"]) == 3

    # 2) Import that exported file against a fresh patch set with the SAME
    #    folder map. Capture the bodies execute_create receives and compare
    #    against the original Graph dicts.
    def _resolve(path: str, *_a, **_kw) -> str:
        for f in folders:
            if f.path.lower() == path.strip("/").lower():
                return f.id
        raise KeyError(path)

    with _Patched(list_folders_return=folders) as p:
        # Override resolve_folder_path with the path-aware fake.
        p.resolve_folder_path.side_effect = _resolve
        rc = cli_rules.main([
            "--config", str(cfg),
            "import", "--from-file", str(out), "--confirm",
        ])
    assert rc == 0
    assert p.execute_create.call_count == 3

    bodies_by_name = {
        c.args[0].args["body"]["displayName"]: c.args[0].args["body"]
        for c in p.execute_create.call_args_list
    }
    # archive-news → moveToFolder should have round-tripped to fld-arch.
    archive_body = bodies_by_name["archive-news"]
    assert archive_body["actions"]["moveToFolder"] == "fld-arch"
    assert archive_body["actions"]["markAsRead"] is True
    assert archive_body["actions"]["stopProcessingRules"] is True
    assert archive_body["conditions"]["senderContains"] == ["@news.example.com"]

    # flag-vip → emailAddress wrapping preserved through round-trip.
    vip_body = bodies_by_name["flag-vip"]
    assert vip_body["conditions"]["fromAddresses"] == [
        {"emailAddress": {"name": "Boss", "address": "boss@example.com"}}
    ]
    assert vip_body["actions"]["assignCategories"] == ["VIP"]
    assert vip_body["actions"]["markImportance"] == "high"
    assert vip_body["exceptions"]["hasAttachments"] is False

    # copy-receipts → copyToFolder also resolved.
    rec_body = bodies_by_name["copy-receipts"]
    assert rec_body["actions"]["copyToFolder"] == "fld-receipts"


# --------------------------------------------------------------------------
# Parser-only sanity: no-subcommand behaviour preserved (list/show still work)
# --------------------------------------------------------------------------

def test_existing_list_subcommand_still_parses() -> None:
    # Sanity that the new subparsers don't break existing list/show.
    args = cli_rules.build_parser().parse_args(["list"])
    assert args.subcommand == "list"
    args = cli_rules.build_parser().parse_args(["show", "rule-id"])
    assert args.subcommand == "show"
