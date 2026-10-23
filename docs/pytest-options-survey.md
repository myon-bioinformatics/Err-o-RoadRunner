# pytest オプション調査（sentinel 設計用）

調査日: 2026-10-22
対象: `pytest 9.1.1` の `python3 -m pytest --help` 出力（CLI フラグ、\`[pytest]\` 設定キー、環境変数）

sentinel の新しい `--format` / `--tb` / `-v` は、採用（**はい**）とした pytest 概念に合わせて命名・要約コードを揃えています。採用しないものは **いいえ**（ラッパーの責務外）、将来検討は **後日** としています。

| 区分 | オプション | 概要（要約） | sentinel 採用 | 理由 |
|------|------------|--------------|---------------|------|
| general | `-k` | EXPRESSION         Only run tests which match the given substring | いいえ | テスト選択・フィクスチャ表示は pytest 専用 |
| general | `-m` | MARKEXPR           Only run tests matching given mark expression. For | いいえ | テスト選択・フィクスチャ表示は pytest 専用 |
| general | `-x, --exitfirst` | Exit instantly on first error or failed test | いいえ | テストランナーの早期終了は sentinel の外 |
| general | `-s` | Shortcut for --capture=no | いいえ | sentinel は独自のリングバッファで tail を保持 |
| Reporting | `-v, --verbose` | Increase verbosity | はい | sentinel の `-v` は short で NOT FOUND を出す（pytest -v 相当） |
| Reporting | `-q, --quiet` | Decrease verbosity | いいえ | sentinel はラッパーでありテスト収集の静粛化は対象外 |
| Reporting | `-r` | chars, --report-chars=chars | はい | short 形式の F/E/T/S/X/H/? コードは `-r` の要約文字に合わせた |
| Reporting | `-l, --showlocals` | Show locals in tracebacks (disabled by default) | いいえ | pytest テストセッション専用のオプション |
| pytest-warnings | `-W` | PYTHONWARNINGS, --pythonwarnings=PYTHONWARNINGS | いいえ | pytest テストセッション専用のオプション |
| test session debugging and configuration | `-c` | FILE, --config-file=FILE | いいえ | pytest テストセッション専用のオプション |
| test session debugging and configuration | `-V, --version` | Display pytest version and information about plugins. | はい | sentinel の `-v` は short で NOT FOUND を出す（pytest -v 相当） |
| test session debugging and configuration | `-h, --help` | Show help message and configuration info | いいえ | ヘルプ表示は sentinel 本体の argparse に委譲 |
| test session debugging and configuration | `-p` | name               Early-load given plugin module name or entry point | いいえ | プラグイン読み込みは pytest 専用 |
| test session debugging and configuration | `-o` | OVERRIDE_INI, --override-ini=OVERRIDE_INI | いいえ | pytest テストセッション専用のオプション |
| Environment variables | `CI` | set to a non-empty value, pytest knows it is running in a CI process and does not truncate summary info | いいえ | pytest 固有の CI/テーマ変数 |
| Environment variables | `BUILD_NUMBER` | to CI | いいえ | pytest 固有の CI/テーマ変数 |
| Environment variables | `PYTEST_ADDOPTS` | command line options | いいえ | pytest 固有の CI/テーマ変数 |
| Environment variables | `PYTEST_PLUGINS` | plugins to load during startup | いいえ | pytest 固有の CI/テーマ変数 |
| Environment variables | `PYTEST_DISABLE_PLUGIN_AUTOLOAD` | to disable plugin auto-loading | いいえ | pytest 固有の CI/テーマ変数 |
| Environment variables | `PYTEST_DEBUG` | to enable debug tracing of pytest's internals | いいえ | pytest 固有の CI/テーマ変数 |
| Environment variables | `PYTEST_DEBUG_TEMPROOT` | the system temporary directory | いいえ | pytest 固有の CI/テーマ変数 |
| Environment variables | `PYTEST_THEME` | Pygments style to use for code output | いいえ | pytest 固有の CI/テーマ変数 |
| Environment variables | `PYTEST_THEME_MODE` | the PYTEST_THEME to be either 'dark' or 'light' | いいえ | pytest 固有の CI/テーマ変数 |
| [pytest] configuration | `markers` | linelist):   Register new markers for test functions | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `empty_parameter_set_mark` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `strict_config` | bool): Any warnings encountered while parsing the `pytest` | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `strict_markers` | bool): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `strict` | bool):        Enables all strictness options, currently: | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `filterwarnings` | linelist): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `max_warnings` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `norecursedirs` | args): Directory patterns to avoid for recursion | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `testpaths` | args):     Directories to search for tests when no files or | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `collect_imported_tests` | bool): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `consider_namespace_packages` | bool): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `usefixtures` | args):   List of default fixtures to be used with this project | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `python_files` | args):  Glob-style file patterns for Python test module | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `python_classes` | args): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `python_functions` | args): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `disable_test_id_escaping_and_forfeit_all_rights_to_community_support` | bool): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `strict_parametrization_ids` | bool): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `console_output_style` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `verbosity_test_cases` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `strict_xfail` | bool):  Default for the strict parameter of xfail markers when | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `tmp_path_retention_count` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `tmp_path_retention_policy` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `enable_assertion_pass_hook` | bool): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `truncation_limit_lines` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `truncation_limit_chars` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `assertion_text_diff_style` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `verbosity_assertions` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `junit_suite_name` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `junit_logging` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `junit_log_passing_tests` | bool): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `junit_duration_report` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `junit_family` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `doctest_optionflags` | args): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `doctest_encoding` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `cache_dir` | string):   Cache directory path | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_level` | string):   Default value for --log-level | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_format` | string):  Default value for --log-format | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_date_format` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_cli` | bool):       Enable log display during test run (also known as "live | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_cli_level` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_cli_format` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_cli_date_format` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_file` | string):    Default value for --log-file | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_file_mode` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_file_level` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_file_format` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_file_date_format` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `log_auto_indent` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `faulthandler_timeout` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `faulthandler_exit_on_timeout` | bool): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `verbosity_subtests` | string): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `addopts` | args):       Extra command line options | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `minversion` | string):  Minimally required pytest version | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `pythonpath` | paths):   Add paths to sys.path | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |
| [pytest] configuration | `required_plugins` | args): | いいえ | pytest.ini / pyproject の設定項目（sentinel は単一ファイル CLI） |

## sentinel との対応（採用分のまとめ）

| pytest | sentinel |
|--------|----------|
| `-r` / `--report-chars`（`fE` 等） | short 形式の `F`/`E`/`T`/`S`/`X`/`H`/`?` 行と `== … ==` 合計行 |
| `--tb=short\|line\|no` | `--tb {short,line,no}`（stdout の詳細度） |
| `-v` / `--verbose` | `-v`（short で `? NOT FOUND` を表示） |
| `-q` | （非採用）ラッパーは子の pytest を変更しない |
| `--junit-xml` | `--junit PATH`（入力として triage） |
| （タイムアウトなし） | `--timeout SECONDS`（子を 124 で終了） |

## 参考

- 生ログ: 調査時に `python3 -m pytest --help` を実行（本リポジトリ CI と同じ pytest 9.x 系）
- sentinel スキーマ: `docs/sentinel-report-schema.md`
