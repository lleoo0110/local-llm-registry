"""scripts/registry_bot.py のテスト．Hugging Face には繋がず，同じ形の偽物を使う．"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import registry_bot as bot  # noqa: E402

OLD = "0" * 40
NEW = "1" * 40
GB = 1_000_000_000


class FakeHub:
    """リポジトリ → Snapshot と，組織 → リポジトリ名の一覧を返す．"""

    def __init__(self, snapshots=None, listing=None):
        self.snapshots = snapshots or {}
        self.listing = listing or {}

    def snapshot(self, repo):
        if repo not in self.snapshots:
            raise OSError(f"404 {repo}")
        return self.snapshots[repo]

    def search(self, author, text):
        return [f"{author}/{name}" for name in self.listing.get(author, []) if text in name]


def mlx(size_gb=3.0, sha=NEW, license_="apache-2.0"):
    return bot.Snapshot(sha, {"config.json": 1, "model.safetensors": int(size_gb * GB)}, license_)


def document(*models):
    return {"schema_version": 2, "makers": {"qwen": {"label": "Qwen", "maker": "Alibaba Cloud", "kind": "local", "order": 4}}, "models": list(models)}


def model(model_id="qwen3.5-4b", repo="mlx-community/Qwen3.5-4B-4bit", revision=OLD, order=1, vendor="qwen"):
    return {
        "id": model_id,
        "vendor": vendor,
        "label": model_id,
        "kind": "llm",
        "license": "Apache-2.0",
        "order": order,
        "variants": [
            {"id": model_id, "runtime": "mlx", "repo": repo, "revision": revision, "quant": "4bit", "size_gb": 3.1, "memory_gb": 8, "disk_gb": 4, "chip": "Apple M1 以降"}
        ],
    }


WATCH = {
    "rules": [
        {
            "vendor": "qwen", "kind": "llm", "author": "mlx-community", "search": "Qwen3.",
            "pattern": r"Qwen(?P<version>3\.\d+)-(?P<size>\d+(?:\.\d+)?B(?:-A\d+B)?)-4bit",
            "min_version": "3.5",
            "id": "qwen{version}-{size}", "family": "qwen{version}", "label": "Qwen{version} {size}",
            "quant": "4bit", "capabilities": ["chat"], "gguf": "unsloth/Qwen{version}-{size}-GGUF",
        }
    ],
    "ignore": ["mlx-community/Qwen3.9-1B-4bit"],
}


def test_the_writer_keeps_the_hand_written_layout():
    path = ROOT / "registry" / "local-models.json"
    assert bot.dump(bot.load(path)) == path.read_text(encoding="utf-8")


def test_a_newer_main_revision_replaces_the_pinned_one():
    doc = document(model())
    notes = bot.update_revisions(doc, FakeHub({"mlx-community/Qwen3.5-4B-4bit": mlx(3.24)}))
    variant = doc["models"][0]["variants"][0]
    assert (variant["revision"], variant["size_gb"], variant["disk_gb"]) == (NEW, 3.2, 4)
    assert notes == [f"[update] qwen3.5-4b: {OLD[:7]} → {NEW[:7]}（3.2 GB）"]


def test_a_revision_without_weights_is_left_alone():
    doc = document(model())
    broken = bot.Snapshot(NEW, {"config.json": 1})
    notes = bot.update_revisions(doc, FakeHub({"mlx-community/Qwen3.5-4B-4bit": broken}))
    assert doc["models"][0]["variants"][0]["revision"] == OLD and "重みが欠けている" in notes[0]
    # 読めないリポジトリも変えない
    notes = bot.update_revisions(doc, FakeHub())
    assert doc["models"][0]["variants"][0]["revision"] == OLD and notes[0].startswith("[skip]")


def test_unchanged_revisions_produce_no_notes():
    doc = document(model(revision=NEW))
    assert bot.update_revisions(doc, FakeHub({"mlx-community/Qwen3.5-4B-4bit": mlx()})) == []


@pytest.mark.parametrize(("size", "memory"), [(3.1, 8), (6.0, 16), (11.2, 24), (16.1, 32), (20.4, 32), (69.6, 128), (224.0, None)])
def test_recommended_memory_follows_the_weights(size, memory):
    assert bot.memory_gb(size) == memory


def test_discover_builds_rows_for_new_models_only():
    listing = {
        "mlx-community": [
            "Qwen3.5-4B-4bit",  # 台帳にある
            "Qwen3.9-14B-4bit",  # 新しい
            "Qwen3.6-35B-A3B-4bit",  # 新しい（MoE）
            "Qwen3.2-8B-4bit",  # 最小の版より古い
            "Qwen3.9-1B-4bit",  # ignore
            "Qwen3.9-14B-8bit",  # 形が違う
            "Qwen3.9-500B-4bit",  # 大きすぎる
        ]
    }
    snapshots = {
        "mlx-community/Qwen3.9-14B-4bit": mlx(8.0),
        "mlx-community/Qwen3.6-35B-A3B-4bit": mlx(20.4),
        "mlx-community/Qwen3.9-500B-4bit": mlx(280.0),
        "unsloth/Qwen3.9-14B-GGUF": bot.Snapshot(NEW, {"Qwen3.9-14B-UD-Q4_K_M.gguf": 9 * GB, "Qwen3.9-14B-Q4_K_M.gguf": int(8.5 * GB)}),
    }
    found = bot.discover(document(model()), WATCH, FakeHub(snapshots, listing))
    assert [m["id"] for m in found] == ["qwen3.6-35b-a3b", "qwen3.9-14b"]
    fresh = found[1]
    assert (fresh["label"], fresh["family"], fresh["license"]) == ("Qwen3.9 14B", "qwen3.9", "Apache-2.0")
    mlx_variant, gguf = fresh["variants"]
    assert (mlx_variant["memory_gb"], mlx_variant["disk_gb"], mlx_variant["chip"]) == (16, 9, "Apple M1 以降")
    assert (gguf["id"], gguf["file"], gguf["runtime"]) == ("qwen3.9-14b-gguf", "Qwen3.9-14B-Q4_K_M.gguf", "llamacpp")
    assert found[0]["variants"][0]["chip"] == "Apple M2 Max 以降"


def test_apply_appends_after_the_same_maker_with_the_next_order():
    doc = document(model(), model("other", "mlx-community/Other-4bit", vendor="gemma"))
    new = model("qwen3.9-14b", "mlx-community/Qwen3.9-14B-4bit")
    del new["order"]
    bot.add(doc, new)
    assert [m["id"] for m in doc["models"]] == ["qwen3.5-4b", "qwen3.9-14b", "other"]
    assert doc["models"][1]["order"] == 2 and list(doc["models"][1])[-1] == "variants"


def test_commands_write_candidates_and_apply_them(tmp_path, monkeypatch):
    registry = tmp_path / "local-models.json"
    registry.write_text(bot.dump(document(model())), encoding="utf-8")
    watch = tmp_path / "watch.json"
    watch.write_text(json.dumps(WATCH), encoding="utf-8")
    monkeypatch.setattr(bot, "REGISTRY", registry)
    monkeypatch.setattr(bot, "WATCH", watch)
    hub = FakeHub({"mlx-community/Qwen3.9-14B-4bit": mlx(8.0)}, {"mlx-community": ["Qwen3.9-14B-4bit"]})
    monkeypatch.setattr(bot, "Hub", lambda _token=None: hub)
    out, body = tmp_path / "candidates.json", tmp_path / "body.md"
    assert bot.main(["discover", "--out", str(out)]) == 0
    assert bot.main(["apply", "--from", str(out), "--id", "qwen3.9-14b", "--body", str(body)]) == 0
    assert [m["id"] for m in bot.load(registry)["models"]] == ["qwen3.5-4b", "qwen3.9-14b"]
    assert "推定値" in body.read_text(encoding="utf-8")
    assert bot.main(["apply", "--from", str(out), "--id", "missing"]) == 1


def test_the_writer_keeps_the_companies_block():
    document = bot.load()
    assert list(document)[:3] == ["schema_version", "companies", "makers"]
    assert json.loads(bot.dump(document)) == document
    # 開発元はすべて会社に属する
    assert all(maker["company"] in document["companies"] for maker in document["makers"].values())
