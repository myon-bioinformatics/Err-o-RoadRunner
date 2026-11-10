import triage


def test_direct_traceback_is_parsed(capsys):
    text = 'Traceback (most recent call last):\n  File "app.py", line 1, in <module>\nValueError: bad input'
    assert triage.main([text]) == 0
    assert 'python exception type: ValueError' in capsys.readouterr().out


def test_single_line_suggests_python_without_claiming_language(capsys):
    assert triage.main(['ModuleNotFoundError:', 'No', 'module', 'named', 'example']) == 0
    assert 'possible Python exception (single line; language unconfirmed): ModuleNotFoundError' in capsys.readouterr().out


def test_direct_text_and_stderr_conflict(capsys):
    assert triage.main(['error message', '--stderr', 'x']) == 2
    assert 'cannot be used together' in capsys.readouterr().err
