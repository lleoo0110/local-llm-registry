# local-llm-registry

手元の PC で動かせる，重みが公開された LLM と音声認識のモデルの一覧である．

- `registry/local-models.json`: モデルごとの取得元（Hugging Face のリポジトリと版），大きさ，推奨メモリ，ライセンス名
- `registry/local-models.schema.json`: 上の一覧の JSON Schema
- `registry/watch.json`: 自動更新が新しいモデルを探す規則
- `releases.json`: 版番号の一覧

3 アプリ（PaperStudioAI・Tsuzuri・GraphBCI）が台帳と設定画面をどう扱うかは [SPEC.md](SPEC.md) にある．各アプリの同梱台帳へは `scripts/sync_registry.py` で取り込む．

モデル本体は含まない．取得は Hugging Face から直接行い，利用は各モデルのライセンスに従う．

## 自動更新

`.github/workflows/update.yml` が毎日 1 回（手動でも）`scripts/registry_bot.py` を回す．

- **版の更新**: 載っている全ての版について，Hugging Face の main の版を取る．台帳と違えば，同じリポジトリのまま `revision`・`size_gb`・`disk_gb` を書き換え，schema を通してから main へ反映する．重みが欠けた版は書き換えない
- **新しいモデル**: `registry/watch.json` の規則に合い，台帳に無いモデルを見つけたら，1 本ずつプルリクエストにする．推奨メモリと空き容量は重みの大きさから求めた推定値なので，確かめてからマージする．足さないときはプルリクエストを閉じる．閉じた候補は二度と作らない．候補にしたくないリポジトリは `watch.json` の `ignore` に書く

手元では次で確かめる．

```bash
python3 scripts/registry_bot.py update-revisions --dry-run
python3 scripts/registry_bot.py discover
pytest -q
```

環境変数 `HF_TOKEN`（リポジトリの secret）があれば Hugging Face へ添える．無くても公開のモデルは読める．
