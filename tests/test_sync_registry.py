"""scripts/sync_registry.py のテスト．公開台帳をアプリの同梱台帳へ取り込む規則を確かめる（通信しない）．"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_registry as sync  # noqa: E402

OLD = "0" * 40
NEW = "1" * 40


def variant(variant_id, revision=NEW, **extra):
    return {"id": variant_id, "runtime": "mlx", "repo": f"mlx-community/{variant_id}", "revision": revision,
            "size_gb": 3.1, "memory_gb": 8, "disk_gb": 4, "chip": "Apple M1 以降", **extra}


def public():
    return {
        "schema_version": 2,
        "companies": {"alibaba": {"label": "Alibaba Cloud", "order": 4, "summary": "Qwen を開発する会社"}},
        "makers": {"qwen": {"label": "Qwen", "maker": "Alibaba Cloud", "company": "alibaba", "kind": "local", "order": 4},
                   "whisper": {"label": "Whisper", "maker": "OpenAI", "company": "openai", "kind": "local", "order": 9}},
        "models": [
            {"id": "qwen-4b", "vendor": "qwen", "label": "Qwen 4B 改", "kind": "llm", "license": "Apache-2.0", "order": 1,
             "variants": [variant("qwen-4b", NEW)]},
            {"id": "qwen-8b", "vendor": "qwen", "label": "Qwen 8B", "kind": "llm", "license": "Apache-2.0", "order": 2,
             "variants": [variant("qwen-8b")]},
            {"id": "whisper-turbo", "vendor": "whisper", "label": "Whisper", "kind": "asr", "license": "MIT", "order": 1,
             "variants": [variant("whisper-turbo")]},
        ],
    }


def bundled():
    return {
        "_comment": "アプリの同梱台帳",
        "schema_version": 2,
        "companies": {"anthropic": {"label": "Anthropic", "order": 1}},
        "makers": {"claude": {"label": "Claude", "maker": "Anthropic", "company": "anthropic", "kind": "cloud", "order": 1},
                   "qwen": {"label": "Qwen", "maker": "Alibaba Cloud", "kind": "local", "order": 4, "summary": "同梱の説明"}},
        "models": [
            {"id": "qwen-4b", "vendor": "qwen", "label": "Qwen 4B", "kind": "llm", "license": "Apache-2.0", "order": 1,
             "summary": "同梱の説明", "recommended": ["translate"], "apps": ["paperstudioai"],
             "variants": [variant("qwen-4b", OLD, bundled=True), variant("qwen-4b-own", trust_remote_code=True)]},
            {"id": "app-only", "vendor": "qwen", "label": "App only", "kind": "llm", "license": "Apache-2.0", "order": 9,
             "variants": [variant("app-only")]},
        ],
    }


def test_public_values_replace_bundled_ones_and_app_only_fields_stay():
    merged = sync.merge(bundled(), public(), kinds=("llm",))
    first = merged["models"][0]
    assert (first["label"], first["summary"], first["recommended"], first["apps"]) == ("Qwen 4B 改", "同梱の説明", ["translate"], ["paperstudioai"])
    pinned, own = first["variants"]
    # 版の値は公開が正本．同梱の印はアプリ専用なので残す
    assert (pinned["revision"], pinned["bundled"]) == (NEW, True)
    # 公開に無い版（独自のモデル定義を読む版など）は残す
    assert own["id"] == "qwen-4b-own" and own["trust_remote_code"] is True


def test_new_public_models_are_added_after_the_same_maker_and_other_kinds_are_skipped():
    merged = sync.merge(bundled(), public(), kinds=("llm",))
    assert [model["id"] for model in merged["models"]] == ["qwen-4b", "app-only", "qwen-8b"]
    # 文字起こしの行は，そのアプリが読まない種類なので足さない
    assert "whisper" not in merged["makers"]


def test_companies_and_makers_follow_the_public_registry_and_cloud_makers_stay():
    merged = sync.merge(bundled(), public(), kinds=("llm", "asr"))
    assert list(merged["companies"]) == ["anthropic", "alibaba"]
    assert merged["makers"]["claude"]["kind"] == "cloud"
    assert merged["makers"]["qwen"] == {"label": "Qwen", "maker": "Alibaba Cloud", "kind": "local", "order": 4,
                                        "summary": "同梱の説明", "company": "alibaba"}
    assert "whisper" in merged["makers"]


def test_merging_twice_changes_nothing():
    once = sync.merge(bundled(), public(), kinds=("llm",))
    assert sync.merge(once, public(), kinds=("llm",)) == once


def test_the_writer_keeps_the_top_level_order_and_one_line_per_variant(tmp_path):
    merged = sync.merge(bundled(), public(), kinds=("llm",))
    text = sync.dump(merged)
    assert json.loads(text) == merged
    assert text.splitlines()[1].startswith('  "_comment"')
    assert sum('"id": "qwen-4b-own"' in line for line in text.splitlines()) == 1


def test_check_reports_a_bundled_registry_that_is_behind(tmp_path):
    source, target = tmp_path / "public.json", tmp_path / "bundled.json"
    source.write_text(json.dumps(public()), encoding="utf-8")
    target.write_text(sync.dump(bundled()), encoding="utf-8")
    args = ["--source", str(source), "--target", str(target), "--kinds", "llm"]
    assert sync.main([*args, "--check"]) == 1
    assert sync.main(args) == 0
    assert sync.main([*args, "--check"]) == 0
