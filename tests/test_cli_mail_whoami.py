"""Parser + scope-presence smoke for `m365ctl mail whoami`."""
from __future__ import annotations

from m365ctl.common.auth import GRAPH_SCOPES_DELEGATED
from m365ctl.mail.cli.whoami import _REQUIRED_MAIL_SCOPES, build_parser


def test_required_scopes_are_declared():
    for s in _REQUIRED_MAIL_SCOPES:
        assert s in GRAPH_SCOPES_DELEGATED


def test_whoami_parser_accepts_config():
    args = build_parser().parse_args(["--config", "/tmp/cfg.toml"])
    assert args.config == "/tmp/cfg.toml"


def test_whoami_parser_default_config_path():
    args = build_parser().parse_args([])
    assert args.config == "config.toml"
