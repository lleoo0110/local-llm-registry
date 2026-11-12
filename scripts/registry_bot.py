#!/usr/bin/env python3
"""公開台帳（registry/local-models.json）を Hugging Face に合わせて更新する．GitHub Actions が毎日回す．

update-revisions: 載っている全ての版について main の版（sha）を取り，台帳と違えば同じリポジトリのまま
    revision・size_gb・disk_gb を書き換える．重みが欠けた版は書き換えずに理由を出す．
discover: registry/watch.json の規則で新しいモデルを探し，台帳へ足す行を組んで書き出す．
apply: discover が書き出した候補の 1 つを台帳へ足す．ワークフローは候補ごとにブランチを作ってプルリクエストにする．

標準ライブラリだけで動く．環境変数 HF_TOKEN があれば Hugging Face へ添える（無くても公開のモデルは読める）．
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "registry" / "local-models.json"
WATCH = ROOT / "registry" / "watch.json"
HF = "https://huggingface.co"
TIMEOUT = 30
# 取得元にしてよい組織．schema（local-models.schema.json）の repo の正規表現と揃える
ALLOWED_ORGS = ("mlx-community", "unsloth", "ggml-org", "pfnet", "llm-jp")
# モデルカードのライセンスの id を，台帳の表記（SPDX の大文字小文字）へ直す
LICENSE_NAMES = {"apache-2.0": "Apache-2.0", "mit": "MIT", "gemma": "Gemma", "llama3.1": "Llama 3.1"}
# 推奨メモリの段（GB）．重みの大きさから，余裕を足して最も近い段を選ぶ
MEMORY_TIERS = (8, 16, 24, 32, 48, 64, 96, 128)
_ID = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?")
_REVISION = re.compile(r"[0-9a-f]{40}")


# ---- Hugging Face


@dataclass(frozen=True)
class Snapshot:
    """リポジトリの 1 つの版．files はファイル名からバイト数への対応である．"""

    sha: str
    files: dict[str, int] = field(default_factory=dict)
    license: str = ""


class Hub:
    """Hugging Face の公開 API．テストは同じ形の偽物に差し替える．"""

    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{HF}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Accept": "application/json", "User-Agent": "local-llm-registry-bot"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as response:  # noqa: S310 https の固定の根だけを読む
            return json.load(response)

    def snapshot(self, repo: str) -> Snapshot:
        """main の版とファイルの大きさ．"""
        data = self._get(f"/api/models/{repo}/revision/main", {"blobs": "true"})
        files = {item["rfilename"]: int(item.get("size") or 0) for item in data.get("siblings") or []}
        card = data.get("cardData") or {}
        license_: str = card["license"] if isinstance(card.get("license"), str) else ""
        return Snapshot(data["sha"], files, license_)

    def search(self, author: str, text: str) -> list[str]:
        """組織のリポジトリのうち，名前に text を含むものの id（<組織>/<名前>）．"""
        data = self._get("/api/models", {"author": author, "search": text, "limit": 1000})
        return [item["id"] for item in data if isinstance(item.get("id"), str)]


# ---- 台帳の読み書き


def load(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or REGISTRY).read_text(encoding="utf-8"))


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def dump(document: dict[str, Any]) -> str:
    """台帳を，開発元と版を 1 行ずつ置く書式で返す．差分が行単位で読めるよう，手で書いた形と揃える．"""
    lines = ["{", f'  "schema_version": {document["schema_version"]},', '  "makers": {']
    makers = list(document["makers"].items())
    for index, (maker_id, maker) in enumerate(makers):
        lines.append(f"    {_compact(maker_id)}: {_compact(maker)}" + ("," if index < len(makers) - 1 else ""))
    lines += ["  },", '  "models": [']
    models = document["models"]
    for index, model in enumerate(models):
        lines.append("    {")
        keys = [key for key in model if key != "variants"]
        for key in keys:
            lines.append(f"      {_compact(key)}: {_compact(model[key])},")
        lines.append('      "variants": [')
        variants = model["variants"]
        for position, variant in enumerate(variants):
            lines.append(f"        {_compact(variant)}" + ("," if position < len(variants) - 1 else ""))
        lines.append("      ]")
        lines.append("    }" + ("," if index < len(models) - 1 else ""))
    lines += ["  ]", "}"]
    return "\n".join(lines) + "\n"


def save(document: dict[str, Any], path: Path | None = None) -> None:
    (path or REGISTRY).write_text(dump(document), encoding="utf-8")


# ---- 大きさと推奨値


def weight_bytes(snapshot: Snapshot, variant: dict[str, Any]) -> int | None:
    """版の重みのバイト数．重みが欠けていれば None．mlx は config.json と safetensors，llamacpp は file を要る．"""
    if variant["runtime"] == "llamacpp":
        size = snapshot.files.get(variant.get("file", ""))
        return size if size else None
    weights = [size for name, size in snapshot.files.items() if name.endswith(".safetensors") and "/" not in name]
    if "config.json" not in snapshot.files or not weights:
        return None
    return sum(weights)


def size_gb(size: int) -> float:
    return max(0.1, round(size / 1e9, 1))


def disk_gb(size: float) -> int:
    return max(2, math.ceil(size * 1.1))


def memory_gb(size: float) -> int | None:
    """推奨メモリ．重みに推論の余裕（4 割と 2 GB）を足し，それ以上の最小の段を選ぶ．どの段にも収まらなければ None．"""
    need = size * 1.4 + 2
    return next((tier for tier in MEMORY_TIERS if tier >= need), None)


def chip(memory: int) -> str:
    return "Apple M2 Max 以降" if memory >= 32 else "Apple M1 以降"


# ---- update-revisions


def update_revisions(document: dict[str, Any], hub: Hub) -> list[str]:
    """main の版が台帳と違う版を書き換える．書き換えと見送りの説明を返す．"""
    notes = []
    for model in document["models"]:
        for variant in model["variants"]:
            repo = variant["repo"]
            try:
                snapshot = hub.snapshot(repo)
            except (OSError, ValueError, KeyError) as error:
                notes.append(f"[skip] {variant['id']}: {repo} を読めない（{error}）")
                continue
            if snapshot.sha == variant["revision"]:
                continue
            if not _REVISION.fullmatch(snapshot.sha):
                notes.append(f"[skip] {variant['id']}: 版の形が違う（{snapshot.sha}）")
                continue
            size = weight_bytes(snapshot, variant)
            if size is None:
                notes.append(f"[skip] {variant['id']}: {snapshot.sha[:7]} は重みが欠けている")
                continue
            old = variant["revision"]
            variant["revision"] = snapshot.sha
            variant["size_gb"] = size_gb(size)
            variant["disk_gb"] = max(variant["disk_gb"], disk_gb(variant["size_gb"]))
            notes.append(f"[update] {variant['id']}: {old[:7]} → {snapshot.sha[:7]}（{variant['size_gb']} GB）")
    return notes


# ---- discover


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def _fill(template: str, groups: dict[str, str]) -> str:
    values = {**groups, **{f"{key}_upper": value.upper() for key, value in groups.items()}}
    values |= {f"{key}_lower": value.lower() for key, value in groups.items()}
    return template.format(**values)


def _known_repos(document: dict[str, Any]) -> set[str]:
    return {variant["repo"] for model in document["models"] for variant in model["variants"]}


def _known_ids(document: dict[str, Any]) -> set[str]:
    ids = {model["id"] for model in document["models"]}
    return ids | {variant["id"] for model in document["models"] for variant in model["variants"]}


def _gguf_variant(rule: dict[str, Any], groups: dict[str, str], model_id: str, hub: Hub) -> dict[str, Any] | None:
    """同じモデルの GGUF（Q4_K_M）があれば llamacpp の版を返す．"""
    template = rule.get("gguf")
    if not template:
        return None
    repo = _fill(template, groups)
    if repo.split("/", 1)[0] not in ALLOWED_ORGS:
        return None
    try:
        snapshot = hub.snapshot(repo)
    except (OSError, ValueError, KeyError):
        return None
    # unsloth の UD-（量子化の配分を変えた版）より，素の Q4_K_M を先に選ぶ
    files = sorted(
        (name for name in snapshot.files if "/" not in name and name.upper().endswith("Q4_K_M.GGUF")),
        key=lambda name: ("UD-" in name.upper(), name),
    )
    if not files or not _REVISION.fullmatch(snapshot.sha):
        return None
    size = size_gb(snapshot.files[files[0]])
    memory = memory_gb(size)
    if memory is None:
        return None
    return {
        "id": f"{model_id}-gguf",
        "runtime": "llamacpp",
        "repo": repo,
        "file": files[0],
        "revision": snapshot.sha,
        "quant": "GGUF Q4_K_M",
        "size_gb": size,
        "memory_gb": memory,
        "disk_gb": disk_gb(size),
        "chip": "Vulkan 対応の GPU",
    }


def _candidate(rule: dict[str, Any], repo: str, groups: dict[str, str], hub: Hub) -> dict[str, Any] | None:
    """新しいモデルの行．重みが欠けているか，形が台帳に合わなければ None．"""
    model_id = _fill(rule["id"], groups).lower()
    if not _ID.fullmatch(model_id):
        return None
    snapshot = hub.snapshot(repo)
    variant: dict[str, Any] = {"id": model_id, "runtime": "mlx", "repo": repo, "revision": snapshot.sha}
    size = weight_bytes(snapshot, variant)
    if size is None or not _REVISION.fullmatch(snapshot.sha):
        return None
    if rule.get("quant"):
        variant["quant"] = rule["quant"]
    variant["size_gb"] = size_gb(size)
    memory = memory_gb(variant["size_gb"])
    # PC のメモリの段に収まらないモデル（数百 GB の重み）は候補にしない
    if memory is None:
        return None
    variant["memory_gb"] = memory
    variant["disk_gb"] = disk_gb(variant["size_gb"])
    variant["chip"] = chip(memory)
    model: dict[str, Any] = {
        "id": model_id,
        "vendor": rule["vendor"],
        "family": _fill(rule["family"], groups).lower(),
        "label": _fill(rule["label"], groups),
        "kind": rule["kind"],
        "license": rule.get("license") or LICENSE_NAMES.get(snapshot.license, snapshot.license) or "不明",
    }
    if rule.get("capabilities"):
        model["capabilities"] = list(rule["capabilities"])
    variants = [variant]
    gguf = _gguf_variant(rule, groups, model_id, hub)
    if gguf:
        variants.append(gguf)
    return {**model, "variants": variants}


def discover(document: dict[str, Any], watch: dict[str, Any], hub: Hub) -> list[dict[str, Any]]:
    """規則に合う，台帳に無いモデルの行を返す．"""
    ignored = set(watch.get("ignore", ()))
    repos = _known_repos(document)
    ids = _known_ids(document)
    found: list[dict[str, Any]] = []
    for rule in watch["rules"]:
        if rule["author"] not in ALLOWED_ORGS:
            continue
        pattern = re.compile(rule["pattern"])
        try:
            names = hub.search(rule["author"], rule["search"])
        except (OSError, ValueError) as error:
            print(f"[skip] {rule['author']}/{rule['search']} を探せない（{error}）", file=sys.stderr)
            continue
        for repo in sorted(set(names)):
            org, _, name = repo.partition("/")
            if org != rule["author"] or repo in repos or repo in ignored:
                continue
            match = pattern.fullmatch(name)
            if match is None:
                continue
            groups = {key: value for key, value in match.groupdict().items() if value is not None}
            if "min_version" in rule and _version(groups.get("version", "0")) < _version(rule["min_version"]):
                continue
            try:
                model = _candidate(rule, repo, groups, hub)
            except (OSError, ValueError, KeyError) as error:
                print(f"[skip] {repo} を読めない（{error}）", file=sys.stderr)
                continue
            if model is None or model["id"] in ids:
                continue
            ids.add(model["id"])
            found.append(model)
    return found


def add(document: dict[str, Any], model: dict[str, Any]) -> None:
    """同じ開発元の最後のモデルの後ろに足す．order はその開発元の最大の次にする．"""
    same = [index for index, entry in enumerate(document["models"]) if entry["vendor"] == model["vendor"]]
    orders = [document["models"][index].get("order", 0) for index in same]
    row = dict(model)
    variants = row.pop("variants")
    row["order"] = max(orders, default=0) + 1
    row["variants"] = variants
    position = same[-1] + 1 if same else len(document["models"])
    document["models"].insert(position, row)


def describe(model: dict[str, Any]) -> str:
    """プルリクエストの本文．推定値を確かめるよう促す．"""
    rows = "\n".join(
        f"| `{v['repo']}` | {v['runtime']} | {v['size_gb']} GB | {v['memory_gb']} GB | {v['disk_gb']} GB | `{v['revision'][:7]}` |"
        for v in model["variants"]
    )
    return f"""{model['label']}（`{model['id']}`）を台帳に足す．毎日の自動更新が Hugging Face で見つけた．

| リポジトリ | 実装 | 大きさ | 推奨メモリ | 空き容量 | 版 |
|---|---|---|---|---|---|
{rows}

- ライセンス: {model['license']}
- 推奨メモリと空き容量は，重みの大きさから求めた推定値である．実機で確かめ，必要なら直してからマージする
- マージすると，Tsuzuri と PaperStudioAI に最長 1 日で「新着」として並ぶ
- 足さないときは，このプルリクエストを閉じる．閉じた候補は二度と作らない
"""


# ---- コマンド


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="公開台帳を Hugging Face に合わせて更新する")
    actions = parser.add_subparsers(dest="action", required=True)
    update = actions.add_parser("update-revisions", help="載っている版を main の版へ揃える")
    update.add_argument("--dry-run", action="store_true", help="書き換えずに差分だけを出す")
    update.add_argument("--summary", type=Path, help="書き換えの説明を書くファイル（コミットの本文に使う）")
    found = actions.add_parser("discover", help="新しいモデルの候補を探す")
    found.add_argument("--out", type=Path, help="候補を書き出す JSON．省くと標準出力へ出す")
    apply = actions.add_parser("apply", help="候補の 1 つを台帳へ足す")
    apply.add_argument("--from", dest="source", type=Path, required=True, help="discover が書き出した JSON")
    apply.add_argument("--id", required=True, help="足すモデルの id")
    apply.add_argument("--body", type=Path, help="プルリクエストの本文を書くファイル")
    args = parser.parse_args(argv)

    hub = Hub(os.environ.get("HF_TOKEN") or None)
    document = load()
    if args.action == "update-revisions":
        notes = update_revisions(document, hub)
        print("\n".join(notes) or "[ok] 変わった版は無い")
        if not args.dry_run and any(note.startswith("[update]") for note in notes):
            save(document)
        if args.summary:
            args.summary.write_text("\n".join(n for n in notes if n.startswith("[update]")) + "\n", encoding="utf-8")
        return 0
    if args.action == "discover":
        models = discover(document, json.loads(WATCH.read_text(encoding="utf-8")), hub)
        text = json.dumps(models, ensure_ascii=False, indent=2)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        print(f"[ok] 候補 {len(models)} 本: {', '.join(m['id'] for m in models) or 'なし'}", file=sys.stderr)
        return 0
    candidates = json.loads(args.source.read_text(encoding="utf-8"))
    model = next((entry for entry in candidates if entry["id"] == args.id), None)
    if model is None:
        print(f"[error] 候補に {args.id} が無い", file=sys.stderr)
        return 1
    add(document, model)
    save(document)
    if args.body:
        args.body.write_text(describe(model), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
