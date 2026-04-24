from m365ctl.mail.cli.categorize import _resolve_final_categories, build_parser


def test_categorize_parser_add():
    args = build_parser().parse_args(["--message-id", "m1", "--add", "X", "--confirm"])
    assert args.add == ["X"]


def test_categorize_parser_set_repeated():
    args = build_parser().parse_args(["--message-id", "m1", "--set", "X", "--set", "Y"])
    assert args.set_ == ["X", "Y"]


def test_categorize_parser_from_plan():
    args = build_parser().parse_args(["--from-plan", "/tmp/p.json", "--confirm"])
    assert args.from_plan == "/tmp/p.json"


def test_resolve_final_set_replaces():
    out = _resolve_final_categories(["A", "B"], [], [], ["X", "Y"])
    assert out == ["X", "Y"]


def test_resolve_final_add_removes_dedup():
    out = _resolve_final_categories(["A"], ["B", "A"], [], [])
    assert out == ["A", "B"]


def test_resolve_final_remove():
    out = _resolve_final_categories(["A", "B", "C"], [], ["B"], [])
    assert out == ["A", "C"]
