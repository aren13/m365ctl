from m365ctl.mail.cli.categories import build_parser


def test_mail_categories_parser_no_subcommand():
    args = build_parser().parse_args([])
    assert args.subcommand is None


def test_mail_categories_parser_list():
    args = build_parser().parse_args(["list"])
    assert args.subcommand == "list"
