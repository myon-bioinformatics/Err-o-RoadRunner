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
    assert 'choose only one' in capsys.readouterr().err


def test_paste_multiline_from_stdin():
    import subprocess
    import sys
    from pathlib import Path
    script = Path(triage.__file__)
    text = 'Traceback (most recent call last):\n  File "app.py", line 1, in <module>\nValueError: bad input\n'
    result = subprocess.run([sys.executable, str(script), '--paste'], input=text, text=True, capture_output=True)
    assert result.returncode == 0
    assert 'python exception type: ValueError' in result.stdout


def test_direct_tokens_from_shell():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, triage.__file__, 'ModuleNotFoundError:', 'No', 'module', 'named', 'example'], text=True, capture_output=True)
    assert result.returncode == 0
    assert 'possible Python exception' in result.stdout
