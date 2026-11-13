#!/usr/bin/env python3
"""公開台帳（registry/local-models.json）を，アプリの同梱台帳へ取り込む．

台帳の正本は公開台帳である（SPEC.md）．各アプリの同梱台帳は，公開台帳の写しにアプリ専用の項目を足したものにする．
このスクリプトは 3 アプリ（PaperStudioAI・Tsuzuri・GraphBCI）の packaging/ へ同じ内容で写して使う．
標準ライブラリだけで動く．

取り込みの規則:
- 会社・開発元・モデル・版は，公開の値で上書きする．公開に無い項目（summary・recommended・apps・bundled・
  trust_remote_code など，アプリ専用の項目）は同梱の値を残す
- 公開に無い会社・開発元・モデル・版（クラウドの開発元，独自のモデル定義を読む版など）は残す．公開は行を消せない
- 公開にだけあるモデルは，アプリが読む種類（--kinds）のものだけを，同じ開発元の最後の行の後ろへ足す
- 何度取り込んでも結果は変わらない

  python3 packaging/sync_registry.py --target src/<app>/data/local_models.json --kinds llm,translate
  python3 packaging/sync_registry.py --target ... --kinds ... --check   # 取り込み漏れがあれば終了コード 1
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

SOURCE = "https://raw.githubusercontent.com/lleoo0110/local-llm-registry/main/registry/local-models.json"
TIMEOUT = 30
# 1 項目を 1 行に置く最上位の辞書．差分が行単位で読める
_ONE_PER_LINE = ("companies", "makers")


def read(source: str) -> dict[str, Any]:
    """公開台帳を読む．source は URL かファイルのパス．"""
    if source.startswith(("https://", "http://")):
        with urllib.request.urlopen(source, timeout=TIMEOUT) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    return json.loads(Path(source).read_text(encoding="utf-8"))


def _overlay(base: dict[str, Any], top: dict[str, Any]) -> dict[str, Any]:
    """base の鍵の順を保ったまま top の値で上書きし，base に無い鍵は末尾へ足す．"""
    merged = dict(base)
    merged.update(top)
    return merged


def _merge_model(own: dict[str, Any], public: dict[str, Any]) -> dict[str, Any]:
    merged = _overlay({key: value for key, value in own.items() if key != "variants"},
                      {key: value for key, value in public.items() if key != "variants"})
    variants = [dict(variant) for variant in own["variants"]]
    by_id = {variant["id"]: index for index, variant in enumerate(variants)}
    for variant in public["variants"]:
        if variant["id"] in by_id:
            variants[by_id[variant["id"]]] = _overlay(variants[by_id[variant["id"]]], variant)
        else:
            variants.append(dict(variant))
    merged["variants"] = variants
    return merged


def merge(bundled: dict[str, Any], public: dict[str, Any], kinds: tuple[str, ...]) -> dict[str, Any]:
    """同梱台帳に公開台帳を取り込んだ台帳を返す．引数は変えない．"""
    result = copy.deepcopy(bundled)
    if "companies" not in result:
        # 会社は schema_version の直後に置く（公開台帳と同じ並び）
        keys = list(result)
        at = keys.index("schema_version") + 1 if "schema_version" in keys else 0
        result = {**{key: result[key] for key in keys[:at]}, "companies": {}, **{key: result[key] for key in keys[at:]}}
    companies = result["companies"]
    for company_id, company in public.get("companies", {}).items():
        companies[company_id] = _overlay(companies.get(company_id, {}), company)

    wanted = [model for model in public["models"] if model["kind"] in kinds]
    used = {model["vendor"] for model in wanted}
    makers = result.setdefault("makers", {})
    for maker_id, maker in public["makers"].items():
        if maker_id in makers or maker_id in used:
            makers[maker_id] = _overlay(makers.get(maker_id, {}), maker)

    models = result.setdefault("models", [])
    for entry in wanted:
        index = next((position for position, model in enumerate(models) if model["id"] == entry["id"]), None)
        if index is not None:
            models[index] = _merge_model(models[index], entry)
            continue
        same = [position for position, model in enumerate(models) if model["vendor"] == entry["vendor"]]
        models.insert(same[-1] + 1 if same else len(models), copy.deepcopy(entry))
    return result


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def dump(document: dict[str, Any]) -> str:
    """台帳を，会社・開発元・版を 1 行ずつ置く書式で返す．公開台帳と同梱台帳で同じ書式にする．"""
    lines = ["{"]
    keys = list(document)
    for position, key in enumerate(keys):
        tail = "," if position < len(keys) - 1 else ""
        value = document[key]
        if key in _ONE_PER_LINE:
            items = list(value.items())
            lines.append(f"  {_compact(key)}: {{")
            for index, (item_id, item) in enumerate(items):
                lines.append(f"    {_compact(item_id)}: {_compact(item)}" + ("," if index < len(items) - 1 else ""))
            lines.append("  }" + tail)
        elif key == "models":
            lines.append('  "models": [')
            for index, model in enumerate(value):
                lines.append("    {")
                for field in (field for field in model if field != "variants"):
                    lines.append(f"      {_compact(field)}: {_compact(model[field])},")
                lines.append('      "variants": [')
                variants = model["variants"]
                for number, variant in enumerate(variants):
                    lines.append(f"        {_compact(variant)}" + ("," if number < len(variants) - 1 else ""))
                lines.append("      ]")
                lines.append("    }" + ("," if index < len(value) - 1 else ""))
            lines.append("  ]" + tail)
        else:
            lines.append(f"  {_compact(key)}: {_compact(value)}{tail}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="公開台帳をアプリの同梱台帳へ取り込む")
    parser.add_argument("--source", default=SOURCE, help="公開台帳の URL かパス（既定は公開リポジトリの main）")
    parser.add_argument("--target", required=True, help="アプリの同梱台帳のパス")
    parser.add_argument("--kinds", required=True, help="アプリが読む種類（カンマ区切り．例 llm,translate）")
    parser.add_argument("--check", action="store_true", help="書き換えず，取り込み漏れがあれば終了コード 1 を返す")
    args = parser.parse_args(argv)
    target = Path(args.target)
    current = target.read_text(encoding="utf-8")
    kinds = tuple(kind.strip() for kind in args.kinds.split(",") if kind.strip())
    text = dump(merge(json.loads(current), read(args.source), kinds))
    if args.check:
        if text != current:
            print(f"{target} は公開台帳に追いついていない．--check を外して取り込む", file=sys.stderr)
            return 1
        return 0
    if text != current:
        target.write_text(text, encoding="utf-8")
        print(f"{target} を更新した", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
