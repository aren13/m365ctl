import pytest

from m365ctl.mail.cli.categories import build_parser


def test_categories_list_still_works_with_no_subcommand():
    args = build_parser().parse_args([])
    assert args.subcommand is None


def test_categories_list_subcommand_still_works():
    args = build_parser().parse_args(["list"])
    assert args.subcommand == "list"


def test_categories_add_subparser():
    args = build_parser().parse_args(["add", "Followup", "--color", "preset0", "--confirm"])
    assert args.subcommand == "add"
    assert args.name == "Followup"
    assert args.color == "preset0"
    assert args.confirm is True


def test_categories_add_default_color():
    args = build_parser().parse_args(["add", "X"])
    assert args.color == "preset0"


def test_categories_update_subparser():
    args = build_parser().parse_args(["update", "cat-id", "--name", "New", "--color", "preset2", "--confirm"])
    assert args.subcommand == "update"
    assert args.id == "cat-id"
    assert args.name == "New"
    assert args.color == "preset2"


def test_categories_remove_subparser():
    args = build_parser().parse_args(["remove", "cat-id", "--confirm"])
    assert args.subcommand == "remove"
    assert args.id == "cat-id"


def test_categories_sync_subparser():
    args = build_parser().parse_args(["sync", "--confirm"])
    assert args.subcommand == "sync"
