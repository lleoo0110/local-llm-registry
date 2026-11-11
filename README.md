# local-llm-registry

手元の PC で動かせる，重みが公開された LLM の一覧である．

- `registry/local-models.json`: モデルごとの取得元（Hugging Face のリポジトリと版），大きさ，推奨メモリ，ライセンス名
- `registry/local-models.schema.json`: 上の一覧の JSON Schema
- `releases.json`: 版番号の一覧

モデル本体は含まない．取得は Hugging Face から直接行い，利用は各モデルのライセンスに従う．
