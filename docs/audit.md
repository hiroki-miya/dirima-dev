# Dirima 独立監査記録 — 2026-10-07

実装担当とは別の Codex に、提供された実装設計書とコードを照合する監査を依頼した。指摘を再現し、実装担当が修正と回帰テストを追加した後、同じ独立監査担当へ再監査を依頼した。監査担当はコードを編集していない。独立 DB 診断は専用 PostgreSQL 18 テスト DB の生成レコードだけを使い、診断後に rollback した。

## 監査・修正の記録

| 回 | 確認した指摘 | 修正・確認 |
| --- | --- | --- |
| 1 | 15 件。receipt による他ソースの無視、tool/status 抽出漏れ、古い自動解決、自己申告 PASS、検証対象の誤紐付け、token マスク漏れ、生成タイトル長、イベントキー長、入力と DB の境界、制約訂正の同時更新、metrics の例外、長い主張の誤同一視、semantic 失敗の再試行漏れ、一部候補しか比較しない問題、古い判断待ちの残留 | receipt と全ソースの統合、全 CAS の整合性確認、型付き captured command result、有効 Finding への紐付け、AUTO 再評価、Task/Constraint の行ロック、境界検証、全候補比較、旧 Case の終了。最初の 8 件の回帰テストは修正前に全件失敗し、修正後に全件通過。 |
| 2 | 6 件。接頭辞付き credential、再分類後の旧競合、長い高リスク件名の重複、不確実性と実行エラーの誤扱い、制約更新と receipt 生成の競合、structured output 境界 | credential suffix の認識、同一 cluster へ合流した Conflict/Case の無効化、ID による Risk Case の重複防止、全候補の不確実性 veto、Task ロック下の制約更新、モデル出力契約の修正。独立 DB 診断で再実行と再分類を確認。 |
| 3 | 2 件。Run 完了前の検証が永久に対象不明、external_sequence の DB 型超過 | 完了・抽出後に保存済み検証イベントを確定。独立 DB 診断で PASSED と正しい Finding の 1 件だけの保存を確認。sequence を strict int の 0..2147483647 に限定。 |
| 4 | 6 件。同名 Project の 500、semantic 出力の誤 UUID 参照、引用符による credential の尾漏れ、receipt generator 復旧後の Task 状態、高リスク判定復旧後の旧 Case、同じ主張の HIGH・追加証拠の破棄 | Project 競合を 409、入力 UUID 参照をモデル境界で検証して 3 回再試行、引用符・escape・切れた値のマスク、最終 receipt に基づく状態算出、不要になった Risk Case の終了、ACTIVE Finding の severity 上昇と追加証拠を監査付きで保持。 |
| 5 | 3 件。raw/digest キーの別原文への衝突、ModelCall の識別子長、同じ UUID の表記違い | 全文正規化後の digest を一貫して使用。ModelCall の provider/model を Text 化。UUID 型として比較。0007 で派生キーと抽出を再構築し、人間の判断・原本・監査履歴を保持。 |
| 6 | 1 件。主張と HARD 比較で大小文字・内部空白の意味を失う問題 | topic の正規化と claim の raw 全文 digest を分離。HARD は raw 全文一致。0008 で再構築・再評価。独立 DB で不一致候補を誤って AUTO にしないことを確認。 |
| 7 | 4 件。意味解析未完了時の古い AUTO、旧・重複 Conflict の再 open 衝突、機械 INVALIDATED の再分類抑止、終了した Risk Case による再上昇判断の抑止 | AUTO を失効し履歴を保持。現在の Finding 集合へ照合し最新 identity のみ保持。機械無効化は再生成、人間の判断・dismiss は保持。HIGH→LOW→HIGH で新しい Case を作成。コードと独立 PostgreSQL 診断の最終確認で、未解消の確定指摘 **0 件**。 |

7 回の独立監査と修正を行い、確認した **37 件を解消**した。最終監査の対象は過去の全指摘、API・schema・adapters・reduction・worker・redaction・frontend・Compose・migration 0001〜0008。監査担当の最終報告では、未解消の確定問題は残っていない。

## 最終実行結果

- Backend: **107 passed**。実 PostgreSQL 18、専用テスト DB 使用。
- Application / Policy / Reduction の合計 coverage: **93%**。
- Ruff / strict MyPy / ESLint / TypeScript: 通過。
- Frontend unit: **2 passed**。Vite production build: 通過。
- npm audit: 実行時点の検出脆弱性 **0**。
- 初期 migration、既存データから 0008 への upgrade、原 Finding・人間解決済み Conflict・監査履歴保持、再評価 Job を検証。
- Compose: 0008 migration が終了コード 0。PostgreSQL / API / Worker / Frontend は healthy。
- Playwright browser: **3 passed**。合意、競合と人間判断、高リスクを確認。
- Live CLI: 全入力形式・重複・redaction・Inbox/status 通過。未設定 extractor による FAILED 判定も確認し、ソースの黙殺がないことを検証。

回帰テストは [test_audit_regressions.py](../backend/tests/test_audit_regressions.py)、移行テストは [test_migrations.py](../backend/tests/test_migrations.py) に保存した。ローカル診断と実行ログは ignored の `.tools/audit/` にある。再実行方法は [acceptance.md](acceptance.md) と [development.md](development.md) を参照。

## 検証範囲

設計書との照合、API と DB の所有権・境界、入力 adapters、reduction/policy、訂正と再評価、非同期処理と復旧、migration、frontend の主要導線、Compose、CLI を確認した。モデルは fixture と mocked HTTP transport で検証し、課金される外部モデル API と外部 Durevi の実接続は行っていない。有限の監査とテストで未知の障害がないことまでは保証できない。
