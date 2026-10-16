import os

import pytest

from mailmigrator.batch import format_accounts, parse_accounts
from mailmigrator.engine import (AUTH_OAUTH2, SECURITY_NONE, SECURITY_STARTTLS, Account,
                                 CommandError, Options, Server, build_command, preflight)
from mailmigrator.progress import ProgressParser, is_error_line

ENGINE = ["imapsync"]
ACCOUNT = Account("old@example.com", "p@ss word1", "new@example.com", "secret2")


def custom(host="imap.example.com", **kw):
    return Server(provider="custom", host=host, **kw)


def test_passwords_go_to_environment_not_argv():
    cmd = build_command(ENGINE, custom(), custom("imap.new.com"), ACCOUNT, Options())
    assert "p@ss word1" not in cmd.args and "secret2" not in cmd.args
    assert cmd.env == {"IMAPSYNC_PASSWORD1": "p@ss word1", "IMAPSYNC_PASSWORD2": "secret2"}
    assert cmd.args[:6] == ["imapsync", "--host1", "imap.example.com", "--ssl1",
                            "--user1", "old@example.com"]
    assert "--automap" in cmd.args
    assert cmd.args[cmd.args.index("--sslargs1") + 1] == "SSL_verify_mode=1"
    assert "p@ss" not in cmd.display()
    unverified = build_command(ENGINE, custom(), custom(), ACCOUNT,
                               Options(verify_certs=False))
    assert "--sslargs1" not in unverified.args


def test_security_modes_and_port():
    cmd = build_command(ENGINE, custom(port=1143, security=SECURITY_STARTTLS),
                        custom(security=SECURITY_NONE), ACCOUNT, Options(automap=False))
    a = cmd.args
    assert a[a.index("--port1") + 1] == "1143"
    assert "--tls1" in a and "--nossl1" in a
    assert "--notls2" in a and "--nossl2" in a
    assert "--sslargs1" in a and "--sslargs2" not in a  # nothing to verify without TLS
    assert "--automap" not in a


def test_provider_flags():
    cmd = build_command(ENGINE, Server(provider="gmail"), Server(provider="office365"),
                        ACCOUNT, Options())
    assert "--gmail1" in cmd.args and "--office2" in cmd.args
    assert "--host1" not in cmd.args and "--ssl1" not in cmd.args
    cmd = build_command(ENGINE, Server(provider="exchange", host="mail.corp"), custom(),
                        ACCOUNT, Options())
    assert cmd.args[1:4] == ["--exchange1", "--host1", "mail.corp"]


def test_oauth_token_written_to_private_file(tmp_path):
    cmd = build_command(ENGINE, Server(provider="office365", auth=AUTH_OAUTH2), custom(),
                        Account("a@x.com", "TOKEN123", "b@y.com", "pw"), Options(),
                        tmpdir=str(tmp_path))
    path = cmd.args[cmd.args.index("--oauthaccesstoken1") + 1]
    assert open(path).read().strip() == "TOKEN123"
    assert "TOKEN123" not in " ".join(cmd.args) and "IMAPSYNC_PASSWORD1" not in cmd.env
    if os.name != "nt":
        assert os.stat(path).st_mode & 0o777 == 0o600
    assert "<token-file>" in cmd.display()


def test_options_mapping():
    o = Options(dry_run=True, just_folders=True, max_age_days=30, min_age_days=2,
                only_folders=["INBOX", "Work Stuff"], exclude="^Spam$",
                subfolder2="Old mail", delete2=True, delete1=True,
                extra_args="--maxsize 1000 --regextrans2 's/a b/c/'")
    a = build_command(ENGINE, custom(), custom(), ACCOUNT, o, logdir="/l").args
    for flag in ("--dry", "--justfolders", "--delete1", "--delete2"):
        assert flag in a
    assert a[a.index("--maxage") + 1] == "30"
    assert a[a.index("--minage") + 1] == "2"
    assert [a[i + 1] for i, x in enumerate(a) if x == "--folderrec"] == ["INBOX", "Work Stuff"]
    assert a[a.index("--subfolder2") + 1] == "Old mail"
    assert a[a.index("--logdir") + 1] == "/l"
    assert a[-4:] == ["--maxsize", "1000", "--regextrans2", "s/a b/c/"]


def test_justlogin_ignores_copy_options():
    o = Options(just_login=True, delete1=True, dry_run=True)
    a = build_command(ENGINE, custom(), custom(), ACCOUNT, o).args
    assert "--justlogin" in a and "--delete1" not in a and "--dry" not in a


@pytest.mark.parametrize("src,account,message", [
    (custom(host=" "), ACCOUNT, "Source server address"),
    (custom(), Account("", "x", "u", "p"), "Source username"),
    (custom(), Account("u", "", "u", "p"), "Source password"),
    (custom(), Account("u", "p", "u", ""), "Destination password"),
])
def test_validation(src, account, message):
    with pytest.raises(CommandError, match=message):
        preflight(src, custom(), account, Options())


def test_extra_args_rejects_passwords_and_bad_quotes():
    with pytest.raises(CommandError, match="passwords"):
        preflight(custom(), custom(), ACCOUNT, Options(extra_args="--password1 x"))
    with pytest.raises(CommandError, match="not valid"):
        preflight(custom(), custom(), ACCOUNT, Options(extra_args="--exclude 'oops"))


def test_options_roundtrip():
    o = Options(max_age_days=5, only_folders=["A"], delete2=True, extra_args="--x")
    assert Options.from_dict(o.to_dict()) == o
    assert Options.from_dict({"max_age_days": "bad", "automap": 0}).automap is False


def test_progress_parser_on_real_output():
    lines = [
        "Log file is LOG_imapsync/2026_10_14_test1_test2.txt ( to change it, use --logfile filepath ; or use --nolog to turn off logging )",
        "ETA: Wednesday 14 October 2026-10-14 15:49:51 -0400 EDT  2 s  2263/2263 msgs left",
        "Folder    7/26 [BACKUP.test1_lamiral_info.INBOX]   -> [BACKUP.test1_lamiral_info.INBOX]  ",
        "ETA: Wednesday 14 October 2026-10-14 20:04:30 -0400 EDT  15274 s  2207/2263 msgs left",
        "Messages transferred                    : 12 ",
        "Messages skipped                        : 2251",
        "Total bytes transferred                 : 1234567 (1.177 MiB)",
        "Detected 0 errors",
    ]
    parser = ProgressParser()
    for line in lines:
        parser.feed(line)
    p = parser.progress
    assert (p.done, p.total) == (56, 2263)
    assert (p.folder_index, p.folder_count) == (7, 26)
    assert p.folder == "BACKUP.test1_lamiral_info.INBOX"
    assert (p.transferred, p.skipped, p.errors) == (12, 2251, 0)
    assert p.bytes_text == "1.177 MiB"
    assert p.logfile.endswith("test1_test2.txt")
    assert not p.cert_error
    parser.feed("DEBUG: .../IO/Socket/SSL.pm:884: ignoring less severe local error "
                "'SSL connect attempt failed error:0A000086:SSL routines::certificate "
                "verify failed', keep 'hostname verification failed'")
    assert parser.progress.cert_error
    assert is_error_line("Err 1/50: could not append")
    assert not is_error_line("Host1: imap server [x] port [993] user [y]")


def test_csv_parse_and_format():
    text = ("# comment\nuser1;password1;user2;password2\n"
            "a@old.com;pw;with;semi;a@new.com\n"
            "b@old.com;\"p;w\";b@new.com;pw2\n"
            "\n"
            "only;three;cols\n")
    accounts, problems = parse_accounts(text)
    assert [a.user1 for a in accounts] == ["a@old.com", "b@old.com"]
    assert accounts[1].secret1 == "p;w"
    assert len(problems) == 1 and "Line 6" in problems[0]
    accounts, _ = parse_accounts("x@o.com,pw 1,x@n.com,pw2\n")
    assert accounts[0].secret1 == "pw 1"
    out = format_accounts(accounts, include_passwords=False)
    assert out.splitlines()[1] == "x@o.com;;x@n.com;"
    again, _ = parse_accounts(format_accounts(accounts, include_passwords=True))
    assert again == accounts


def test_sizes_and_maxsize():
    o = Options(just_sizes=True, max_size_mb=50, delete1=True, delete2=True)
    a = build_command(ENGINE, custom(), custom(), ACCOUNT, o).args
    assert "--justfoldersizes" in a and "--delete1" not in a and "--delete2" not in a
    assert a[a.index("--maxsize") + 1] == "50000000"
    assert "just_sizes" not in o.to_dict()
    parser = ProgressParser()
    for line in ["Host1 Nb folders:                    26 folders",
                 "Host1 Nb messages:                 2263 messages",
                 "Host1 Total size:             412660890 bytes (393.544 MiB)",
                 "Host2 Nb messages:                 4083 messages"]:
        parser.feed(line)
    assert parser.progress.sizes[1] == {"folders": 26, "messages": 2263,
                                        "bytes": 412660890, "size": "393.544 MiB"}
    assert parser.progress.sizes[2] == {"messages": 4083}


def test_single_side():
    from mailmigrator.engine import single_side
    src, dst, acc = single_side(custom(), "u", "p")
    cmd = build_command(ENGINE, src, dst, acc, Options(just_login=True))
    assert cmd.env == {"IMAPSYNC_PASSWORD1": "p", "IMAPSYNC_PASSWORD2": "p"}
    assert cmd.args.count("imap.example.com") == 2
