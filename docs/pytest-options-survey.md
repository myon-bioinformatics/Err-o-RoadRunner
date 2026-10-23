# pytest options survey (for sentinel design)

Survey date: 2026-10-22  
Scope: `uv run pytest --help` output of `pytest 9.1.1` (CLI flags, \`[pytest]\` config keys, environment variables)

sentinel's new `--format` / `--tb` / `-v` align their names and summary codes with the pytest concepts marked as adopted (**yes**). Concepts not adopted are **no** (outside the wrapper's responsibility); ones to consider later are **later**.

| Category | Option | Summary | Adopted by sentinel | Reason |
|------|------------|--------------|---------------|------|
| general | `-k` | EXPRESSION         Only run tests which match the given substring | no | Test selection and fixture display are pytest-only |
| general | `-m` | MARKEXPR           Only run tests matching given mark expression. For | no | Test selection and fixture display are pytest-only |
| general | `-x, --exitfirst` | Exit instantly on first error or failed test | no | Early exit of the test runner is outside sentinel |
| general | `-s` | Shortcut for --capture=no | no | sentinel keeps the tail in its own ring buffer |
| Reporting | `-v, --verbose` | Increase verbosity | yes | sentinel's `-v` shows NOT FOUND in short (like pytest -v) |
| Reporting | `-q, --quiet` | Decrease verbosity | no | sentinel is a wrapper; quieting test collection is out of scope |
| Reporting | `-r` | chars, --report-chars=chars | yes | The short-format F/E/T/S/X/H/? codes follow `-r`'s summary characters |
| Reporting | `-l, --showlocals` | Show locals in tracebacks (disabled by default) | no | Option specific to pytest test sessions |
| pytest-warnings | `-W` | PYTHONWARNINGS, --pythonwarnings=PYTHONWARNINGS | no | Option specific to pytest test sessions |
| test session debugging and configuration | `-c` | FILE, --config-file=FILE | no | Option specific to pytest test sessions |
| test session debugging and configuration | `-V, --version` | Display pytest version and information about plugins. | yes | sentinel's `-v` shows NOT FOUND in short (like pytest -v) |
| test session debugging and configuration | `-h, --help` | Show help message and configuration info | no | Help display is left to sentinel's own argparse |
| test session debugging and configuration | `-p` | name               Early-load given plugin module name or entry point | no | Plugin loading is pytest-only |
| test session debugging and configuration | `-o` | OVERRIDE_INI, --override-ini=OVERRIDE_INI | no | Option specific to pytest test sessions |
| Environment variables | `CI` | set to a non-empty value, pytest knows it is running in a CI process and does not truncate summary info | no | pytest-specific CI/theme variable |
| Environment variables | `BUILD_NUMBER` | to CI | no | pytest-specific CI/theme variable |
| Environment variables | `PYTEST_ADDOPTS` | command line options | no | pytest-specific CI/theme variable |
| Environment variables | `PYTEST_PLUGINS` | plugins to load during startup | no | pytest-specific CI/theme variable |
| Environment variables | `PYTEST_DISABLE_PLUGIN_AUTOLOAD` | to disable plugin auto-loading | no | pytest-specific CI/theme variable |
| Environment variables | `PYTEST_DEBUG` | to enable debug tracing of pytest's internals | no | pytest-specific CI/theme variable |
| Environment variables | `PYTEST_DEBUG_TEMPROOT` | the system temporary directory | no | pytest-specific CI/theme variable |
| Environment variables | `PYTEST_THEME` | Pygments style to use for code output | no | pytest-specific CI/theme variable |
| Environment variables | `PYTEST_THEME_MODE` | the PYTEST_THEME to be either 'dark' or 'light' | no | pytest-specific CI/theme variable |
| [pytest] configuration | `markers` | linelist):   Register new markers for test functions | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `empty_parameter_set_mark` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `strict_config` | bool): Any warnings encountered while parsing the `pytest` | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `strict_markers` | bool): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `strict` | bool):        Enables all strictness options, currently: | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `filterwarnings` | linelist): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `max_warnings` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `norecursedirs` | args): Directory patterns to avoid for recursion | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `testpaths` | args):     Directories to search for tests when no files or | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `collect_imported_tests` | bool): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `consider_namespace_packages` | bool): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `usefixtures` | args):   List of default fixtures to be used with this project | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `python_files` | args):  Glob-style file patterns for Python test module | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `python_classes` | args): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `python_functions` | args): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `disable_test_id_escaping_and_forfeit_all_rights_to_community_support` | bool): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `strict_parametrization_ids` | bool): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `console_output_style` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `verbosity_test_cases` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `strict_xfail` | bool):  Default for the strict parameter of xfail markers when | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `tmp_path_retention_count` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `tmp_path_retention_policy` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `enable_assertion_pass_hook` | bool): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `truncation_limit_lines` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `truncation_limit_chars` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `assertion_text_diff_style` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `verbosity_assertions` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `junit_suite_name` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `junit_logging` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `junit_log_passing_tests` | bool): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `junit_duration_report` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `junit_family` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `doctest_optionflags` | args): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `doctest_encoding` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `cache_dir` | string):   Cache directory path | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_level` | string):   Default value for --log-level | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_format` | string):  Default value for --log-format | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_date_format` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_cli` | bool):       Enable log display during test run (also known as "live | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_cli_level` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_cli_format` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_cli_date_format` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_file` | string):    Default value for --log-file | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_file_mode` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_file_level` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_file_format` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_file_date_format` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `log_auto_indent` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `faulthandler_timeout` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `faulthandler_exit_on_timeout` | bool): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `verbosity_subtests` | string): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `addopts` | args):       Extra command line options | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `minversion` | string):  Minimally required pytest version | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `pythonpath` | paths):   Add paths to sys.path | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |
| [pytest] configuration | `required_plugins` | args): | no | pytest.ini / pyproject setting (sentinel is a single-file CLI) |

## Mapping to sentinel (summary of adopted items)

| pytest | sentinel |
|--------|----------|
| `-r` / `--report-chars` (`fE`, etc.) | Short-format `F`/`E`/`T`/`S`/`X`/`H`/`?` lines and the `== … ==` totals line |
| `--tb=short\|line\|no` | `--tb {short,line,no}` (stdout detail level) |
| `-v` / `--verbose` | `-v` (shows `? NOT FOUND` in short) |
| `-q` | (not adopted) the wrapper does not change the child pytest |
| `--junit-xml` | `--junit PATH` (triaged as input) |
| (no timeout) | `--timeout SECONDS` (ends the child with 124) |

## References

- Raw log: `uv run pytest --help` run at survey time (the same pytest 9.x line as this repository's CI)
- sentinel schema: `docs/sentinel-report-schema.md`
