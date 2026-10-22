# Sentinel エラー・スクワッド

`err-o-roadrunner sentinel` の **go** 経路（失敗時のエスカレーション）で、JUnit の失敗を機械可読な **records** に分解し、並列の **スクワッド** が追加の手がかりを集めます。ネットワークはデフォルトでは使いません。

## 目的

- テストケース単位の失敗（`error_type`、メッセージ、`signature`、`test_id`、トレースバック上の `source_file` / 行）を保持し、複数の JUnit ファイル（例: `py3.11` … `py3.15`）を **variants** としてマージする。
- `--group-by error_type` が `JUnitFailure` に潰れないようにする（従来の triage 行ベースの正規化の代わりに records を優先）。
- コード・履歴・（任意で）Web からの仮説を `hypotheses` と `fix_candidates.source_squads` に載せる。

## CLI

| フラグ | 既定 | 説明 |
|--------|------|------|
| `--squads` | `code,history` | `web,code,history` のカンマ区切り、`all`、`none` |
| `--squad-timeout` | `10` | 各スクワッドの上限（秒） |
| `--squad-budget` | `30` | 全体の壁時計バジェット（秒） |
| `--squad-workers` | `3` | `ThreadPoolExecutor` のワーカー数 |
| `--offline` | オフ | Web スクワッドをスキップ（ネットワークなし） |
| `--corpus` | リポジトリ内 `tests/fixtures/junit_corpus` があれば自動 | JUnit コーパスディレクトリ（繰り返し可）。**history スクワッドの署名索引のみ**（現在の失敗 `records` には含めない） |
| `--web-endpoint` | GitHub Search API | Web スクワッドの URL プレフィックス許可リスト（繰り返し可） |
| `--project-root` | `.` | コードスクワッドと汚染判定のルート |

**Web スクワッド**は `--squads` に `web` を含め、かつ `--offline` でないときだけ動きます。

## スクワッドの戻り値

各スクワッドは例外を外に出さず、次の形を返します。

```json
{
  "name": "code",
  "status": "ok",
  "elapsed_ms": 120,
  "findings": [],
  "hypotheses": []
}
```

`status` は `ok` / `timeout` / `error` / `skipped` です。プロセスの終了コードは変えません。

### code

- `test_id` からテストファイルを特定し、失敗行の前後数行を読む。
- `ast` で import を列挙し、未解決を `unresolved_imports` に載せる。
- `tests` / `conftest` 内の `sys.path.insert` / `sys.modules` 変更を検出する。

### history

- `.git` がない場合は `skipped`。
- 失敗ファイルごとに `git log -n20`、`git blame -L`（porcelain）。
- `--corpus` の XML から同じ `signature` の初出・最終出現を照合し、兄弟バリアントの pass/fail で **flaky** マーカーを付けることがあります。

### web

- `error_type` と正規化メッセージとフレームワーク推定から検索クエリを組み立て、常に `queries[]` を出力する。
- 既定は `https://api.github.com/search/issues` のみ。結果は `{title, url, score}` 最大 5 件。`junit_redact.redact_text` でパスやトークンを落とす。

## 仮説のマージ

- スクワッド間で同じ `summary` をまとめ、`source_squads` を和集合にする。
- `merge_squad_hypotheses` はスクワッドが返した `hypotheses` に加え、**HYPOTHESIS_RULES** を `records` に対して実行し、結果を同じ `summary` キーでマージする（並列エージェントはルールをリスト末尾に追加するだけ）。
- **import-path contamination**（H1）: `rule_import_cascade` — リポジトリに実在する短いモジュール名への `ModuleNotFoundError` / `AttributeError` が複数あるとき、根因候補として `explains` にテスト ID を列挙する。
- **stale installed metadata or env override**: `rule_stale_package_metadata` — 同一テスト関数の parametrize で `assert_left` が複数値に割れ、名前やメッセージに version / metadata / pyproject が含まれるとき。

## 拡張ポイント（`squad.py`）

並列エージェントは **既存の並びの末尾に関数を追加** する。先頭に挿入したり、既存エントリを差し替えない（順序が意味を持つものは下表のとおり）。

| リスト | 呼び出し元 | シグネチャ | 戻り値 | 順序 |
|--------|------------|------------|--------|------|
| `EXCEPTION_PARSERS` | `_exception_type` | `(message: str, blob: str, first_line: str) -> str \| None` | 例外型名、未判定は `None` | 先に成功したパーサーが採用。最後まで `None` なら `Unknown`。 |
| `VARIANT_PARSERS` | `variant_from_artifact` | `(path: str) -> str \| None` | バリアント名（例 `py3.12`）、未判定は `None` | 先に非 `None` の結果を採用。すべて `None` なら `unknown`。 |
| `LANGUAGE_DETECTORS` | `parse_junit_failure_records`（レコード構築時） | `(record: Mapping[str, Any]) -> str \| None` | `python` など、未判定は `None` | 先に非 `None` の言語を `records[].language` に設定。すべて `None` なら `language` は `null`。 |
| `HYPOTHESIS_RULES` | `merge_squad_hypotheses`（および `build_cascade_hypotheses`） | `(records, project_root, squad_names) -> list[dict]` | 仮説 dict のリスト（`summary`, `confidence`, `explains`, 任意 `id`） | 各ルールの出力を順にマージ。同一 `summary` は `source_squads` 和集合・`explains` 連結。 |
| `FIX_RULES` | `enrich_fix_candidates` | `(candidates, hypotheses) -> list[dict] \| None` | 更新後の候補リスト、変更なしは `None` | 先に非 `None` を返したルールのみ適用（import カスケード、editable 再インストール案など）。 |

`parse_junit_failure_records` は `AssertionError` 向けに `extract_assert_operands` で `assert_left` / `assert_right`（redact 済み・最大 80 文字）を `signature` の直後に付与する。`summarize_top_causes` は大きな JUnit ラン向けに上位署名を最大 3 件返す（レポート JSON の `top_causes`）。

`records[]` には JUnit 解析時に `language` が載る（既定は Python JUnit のみ `python` を検出）。`records_to_normalized_errors` はレコードの `language` を優先し、無いときだけ従来の推定にフォールバックする。

## レポート JSON

`docs/sentinel-report-schema.md` の任意フィールド:

- `records[]` — マージ後の失敗レコード（`variants` 付き）
- `squads[]` — 上記のスクワッド結果
- `hypotheses[]` — `{id, summary, confidence, explains, source_squads}`

## 標準出力

- **short**: `S code ok 120ms 4 findings`、`H1 import-path contamination (explains 9/19)` のような行。
- **porcelain**: `squad` 行 — 列順は `squad`、名前、`status`、経過 ms、findings 件数（タブ区切り）。
- **table**: グループ表の右にスクワッド名ごとの列（TOTAL 行に各 `status`）。

実装は `squad.py`（標準ライブラリのみ）にあり、`sentinel.py` から go 時に呼ばれます。
