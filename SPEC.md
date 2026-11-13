# モデル設定と台帳の共通仕様

PaperStudioAI・Tsuzuri・GraphBCI の 3 アプリは，モデルの設定画面とローカルモデルの台帳を，この仕様に従って同じ方法で扱う．各アプリの文書はここを指し，アプリ固有の差だけを書く．

## 台帳の正本と取り込み

- 台帳の正本は，このリポジトリの `registry/local-models.json` である．毎日 bot が版を更新し，新しいモデルをプルリクエストにする（README の「自動更新」）
- 各アプリの同梱台帳（`data/local_models.json`）は，正本の写しにアプリ専用の項目を足したものである．取り込みは `scripts/sync_registry.py` を各アプリの `packaging/` へ写して行う
- 同梱台帳から正本を書き出して上書きしない．アプリから行を足すときは，このリポジトリへプルリクエストを出すか，`registry/watch.json` に規則を足す

アプリ専用の項目は次のとおりで，正本には置かない．

| 階層 | 項目 |
|---|---|
| 最上位 | `_comment` |
| 開発元 | クラウドの開発元の行（`kind: cloud`）．`summary` |
| モデル | `summary`・`license_note`・`recommended`・`uses`・`apps` |
| 版 | `bundled`・`trust_remote_code`．`trust_remote_code` の要る版は版ごと同梱台帳にだけ置く |

## 台帳の形（schema_version 2）

- `companies`: 会社．`label`・`order`・`summary`・`aliases`（開発元の `maker` と照らす別名）
- `makers`: 開発元．`label`・`maker`（会社名）・`company`（会社の id）・`kind`（正本は `local` だけ）・`order`
- `models[]`: モデル．`id`・`vendor`（開発元の id）・`family`・`label`・`kind`（`llm`・`translate`・`asr`）・`license`・`capabilities`・`order`・`deprecated`・`variants[]`
- `variants[]`: 版．`id`（設定と置き場のフォルダ名）・`runtime`（`mlx` は Apple Silicon の mlx-lm，`llamacpp` は Windows の llama-server が読む GGUF）・`repo`・`file`（GGUF だけ必須）・`revision`（40 桁の sha）・`quant`・`size_gb`・`memory_gb`・`disk_gb`・`chip`

厳密な定義は `registry/local-models.schema.json` にある．

## アプリが正本を読む規則

アプリは同梱台帳を土台にし，起動時に取得した正本を重ねる．

| 項目 | 規則 |
|---|---|
| 取得先 | `https://raw.githubusercontent.com/lleoo0110/local-llm-registry/main/registry/local-models.json` |
| 取得の間隔 | 起動時に裏で 1 回．前回から 24 時間以内なら通信しない．`refresh(force=True)` は間隔を無視する |
| 取得の停止 | 設定の「更新を確認する」を切ると通信しない．取得先の環境変数（`<APP>_FEED_BASE`）を空文字にしても止まる |
| キャッシュ | `<データフォルダ>/feeds/local-models.json`．読むたびに検証する |
| 検証 | `schema_version` が 2 でなければ全体を捨てる．形に合わない行だけを捨てる．id は Windows の装置名（`con`・`nul` など）を弾く．`repo` の組織は `mlx-community`・`unsloth`・`ggml-org`・`pfnet`・`llm-jp`・`kotoba-tech` に限る．`trust_remote_code` と `bundled` は写さない |
| 会社 | 同梱の会社に正本の会社を重ねる．正本にだけある開発元は `company`，無ければ `maker` を会社の `label` か `aliases` と照らして属させる．照らせなければ会社名をそのまま 1 社にする |
| 重ねる項目 | 会社・開発元・モデルの `label`・`summary`・`order`，モデルの `capabilities`，版の `size_gb`・`disk_gb`・`revision`（`repo` が同じときだけ） |
| 足す行 | 正本にだけある版とモデルを足す．開発元が `local` で，アプリが読む種類のものに限る |
| 取り下げ | `deprecated` のモデルは，手元に無ければ一覧から隠す．取得済みなら使い続ける |

## 取得済みモデルの更新

- 正本の `revision` が手元の版と違えば，そのモデルの行に「更新」を出す
- 更新の取得中は旧い版で動く．取得し終えたら新しい版へ切り替え，旧い版を消す
- 中止しても書きかけを残し，次の取得で続きから取る
- 取得中のモデルと同梱のモデルは削除できない

## 設定画面

### 括り

画面の括りは会社である．会社のカード 1 枚に，その会社の経路を並べる．

| 経路 | 中身 |
|---|---|
| Subscription | CLI のサブスクリプション（Claude Code・Codex・Antigravity）．ログインの操作 |
| API | API のキー．OpenAI 互換の URL と CLI のコマンド名の入力欄 |
| この PC | 台帳のローカルモデルの行 |

### Connections

- 先頭に端末情報の行を置く: 「この PC: {chip}・メモリ {n} GB・空き容量 {n} GB」
- カードの見出しは会社名．ホバーで会社の説明を出す．右端に状態（「N 本インストール済み」「使える」「Subscription だけ使える」「未設定」）を出す
- ローカルモデルの行: 状態点・名前・大きさ・操作（インストール / 中止 / 更新 / 削除）・進捗「インストール中 N%」・失敗「インストールできなかった: 理由」
- モデル名のホバー: 説明・大きさ・推奨メモリとチップ・ライセンスの 4 行
- 推奨の札: `recommended` の工程を「翻訳の推奨」のような札で出す
- 削除はアプリ内の確認ダイアログで確かめる
- 新着の印: 台帳に初めて現れたモデルに「新着」を付ける．既読の id はアプリの設定ファイルに保存する．初回は全部を既読にし，カードを開いたら既読にする（開いている間は印を保つ）

### モデルのピッカー

- 1 段目は会社．右端に状態，ローカルに新着があれば「新着」
- 2 段目は経路のタブ（Subscription / API / この PC．経路が 1 本なら出さない）とモデル
- 工程に使えないモデルは出さない（翻訳専用のモデルは翻訳の行だけ）
- 未インストールの行は灰色で押せる．押すと Connections のその会社のカードを開き，その行を強調する
- 「既定」の行は「既定（実際のモデル名）」と出し，トリガーの下に「会社・経路」を添える
- モデルのホバーは Connections と同じ 4 行

## 版の知らせ

- `releases.json` の自アプリの項（`paperstudioai`・`tsuzuri`・`graphbci`）と今の版を比べる．版は数字と `.` だけ
- 新しい版があれば，画面上部の帯と About（Storage & App）に「新しい版 X がある．配布元から受け取る」と出す．リンクは出さない
- 帯を閉じた版はアプリの設定ファイルに保存し，同じ版では再び出さない
- 「更新を確認する」のスイッチに，送るもの（何も送らない．公開ファイルを読むだけ）と受け取るもの（台帳と版番号）を説明する

## アプリごとの差

| アプリ | 読む種類 | ローカルの実装 | 工程 |
|---|---|---|---|
| PaperStudioAI | `llm`・`translate` | mlx（Mac）・llama.cpp（Windows） | Translate・Summarize・AI など．翻訳専用モデルは Translate だけ |
| Tsuzuri | `llm`・`asr` | mlx・transformers（文字起こし） | 整形・要約・文字起こしなど．文字起こしは別のピッカー |
| GraphBCI | `llm` | mlx（Mac）・llama.cpp（Windows） | AI のチャット |

設定ファイルの鍵の名前とローカルの経路名（`local-mlx` / `local` など）は各アプリの内部の都合で，画面と操作には現れない．
