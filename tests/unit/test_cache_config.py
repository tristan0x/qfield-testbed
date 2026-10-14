from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from qfield_testbed import cache as cache_module
from qfield_testbed.cache import Cache, dir_size, human_size
from qfield_testbed.config import DEFAULT_CONFIG_FILE, GlobalConfig, IosHost
from qfield_testbed.paths import Paths

# --- cache ------------------------------------------------------------------------------------------------


@pytest.fixture
def cache(config: GlobalConfig) -> Cache:
    return Cache(Paths.from_config(config))


def test_load_without_index_is_empty(cache: Cache) -> None:
    assert cache.load().entries == {}


def test_touch_records_users_and_images(cache: Cache, tmp_path: Path) -> None:
    cache.touch("qfieldcloud/images/v1", tmp_path / "a", user="d1", images=["b:1", "a:1"])
    cache.touch("qfieldcloud/images/v1", tmp_path / "b", user="d2", images=["a:1", "c:1"])
    cache.touch("qfieldcloud/images/v1", tmp_path / "b")
    entry = cache.load().entries["qfieldcloud/images/v1"]
    assert entry.path == tmp_path / "b"
    assert entry.users == {"d1", "d2"}
    assert entry.images == ["a:1", "b:1", "c:1"]
    assert entry.last_used >= entry.created_at
    on_disk = json.loads(cache.paths.cache_index.read_text())
    assert set(on_disk["entries"]) == {"qfieldcloud/images/v1"}


def test_release_keeps_entries_but_forgets_the_user(cache: Cache, tmp_path: Path) -> None:
    cache.touch("a", tmp_path, user="d1")
    cache.touch("b", tmp_path, user="d2")
    cache.release("d1")
    entries = cache.load().entries
    assert (entries["a"].users, entries["b"].users) == (set(), {"d2"})


def test_prunable_skips_live_and_recent_entries(cache: Cache, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_now = cache_module._now()
    monkeypatch.setattr(cache_module, "_now", lambda: real_now - timedelta(days=40))
    cache.touch("old-dead", tmp_path, user="gone")
    cache.touch("old-live", tmp_path, user="alive")
    monkeypatch.setattr(cache_module, "_now", lambda: real_now)
    cache.touch("recent", tmp_path)
    assert set(cache.prunable(older_than=timedelta(days=30), live_users={"alive"})) == {"old-dead"}
    assert set(cache.prunable(older_than=timedelta(0), live_users=set())) == {"old-dead", "old-live"}
    monkeypatch.setattr(cache_module, "_now", lambda: real_now + timedelta(seconds=1))
    assert "recent" in cache.prunable(older_than=timedelta(0), live_users=set())


def test_drop_removes_files_and_directories(cache: Cache, tmp_path: Path) -> None:
    directory = tmp_path / "dir"
    (directory / "sub").mkdir(parents=True)
    (directory / "sub" / "f").write_text("x")
    file = tmp_path / "file.apk"
    file.write_text("x")
    cache.touch("dir", directory)
    cache.touch("file", file)
    cache.touch("gone", tmp_path / "missing")
    assert cache.drop("dir") is not None
    assert cache.drop("file") is not None
    dropped = cache.drop("gone")
    assert dropped is not None
    assert dropped.path == tmp_path / "missing"
    assert cache.drop("unknown") is None
    assert not directory.exists()
    assert not file.exists()
    assert cache.load().entries == {}


def test_dir_size(tmp_path: Path) -> None:
    (tmp_path / "a").write_bytes(b"12345")
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "b").write_bytes(b"123")
    assert dir_size(tmp_path) == 8
    assert dir_size(tmp_path / "a") == 5


@pytest.mark.parametrize(
    ("size", "text"),
    [(0, "0.0 B"), (1023, "1023.0 B"), (1536, "1.5 KiB"), (5 * 1024**2, "5.0 MiB"), (3 * 1024**3, "3.0 GiB")],
)
def test_human_size(size: int, text: str) -> None:
    assert human_size(size) == text


def test_human_size_tebibytes() -> None:
    assert human_size(2 * 1024**4) == "2.0 TiB"


# --- config -----------------------------------------------------------------------------------------------


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("QFT_CONFIG", "QFT_HOME", "QFT_CACHE_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("clean_env")
def test_load_missing_file_gives_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    config = GlobalConfig.load(tmp_path / "absent.yaml")
    assert config.home == tmp_path / ".local/share/qfield-testbed"
    assert config.cache_dir == tmp_path / ".cache/qfield-testbed"
    assert (config.port_base, config.max_slots, config.cache.max_image_sets) == (20000, 50, 4)
    assert config.repositories.qfield == "https://github.com/opengisch/QField"


@pytest.mark.usefixtures("clean_env")
def test_load_reads_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    file = tmp_path / "config.yaml"
    file.write_text(
        "home: ~/qft\nport_base: 40000\nios_hosts:\n  mac: {ssh: me@mac.local}\n"
        "repositories: {qfield: https://github.com/me/QField}\ncache: {prune_after_days: 0}\n"
    )
    config = GlobalConfig.load(file)
    assert config.home == tmp_path / "qft"
    assert config.port_base == 40000
    assert config.ios_hosts == {"mac": IosHost(ssh="me@mac.local")}
    assert config.ios_hosts["mac"].cache_dir == "~/.cache/qfield-testbed"
    assert config.repositories.qfield == "https://github.com/me/QField"
    assert config.cache.prune_after_days == 0


@pytest.mark.usefixtures("clean_env")
def test_load_empty_file_and_env_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file = tmp_path / "config.yaml"
    file.write_text("")
    monkeypatch.setenv("QFT_CONFIG", str(file))
    monkeypatch.setenv("QFT_HOME", str(tmp_path / "h"))
    monkeypatch.setenv("QFT_CACHE_DIR", str(tmp_path / "c"))
    config = GlobalConfig.load()
    assert (config.home, config.cache_dir) == (tmp_path / "h", tmp_path / "c")
    assert GlobalConfig.file_path() == file


@pytest.mark.usefixtures("clean_env")
def test_file_path_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    assert GlobalConfig.file_path() == tmp_path / DEFAULT_CONFIG_FILE.relative_to("~")


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize(
    "content",
    ["unknown_key: 1\n", "port_base: 80\n", "max_slots: 0\n", "ios_hosts: {mac: {}}\n", "cache: {max_image_sets: 0}\n"],
)
def test_load_rejects_invalid_config(tmp_path: Path, content: str) -> None:
    file = tmp_path / "config.yaml"
    file.write_text(content)
    with pytest.raises(ValidationError):
        GlobalConfig.load(file)


def test_trim_forgets_the_oldest_built_sets(cache: Cache, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = iter(range(10))
    monkeypatch.setattr(cache_module, "_now", lambda: datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=next(clock)))
    for name in ("a", "b", "c", "d"):
        cache.touch(f"qfieldcloud/images/{name}", tmp_path, images=[f"qft-qfieldcloud-app:{name}"])
    cache.touch("qfieldcloud/images/release", tmp_path, images=["ghcr.io/opengisch/qfieldcloud-app:26.28"])
    cache.touch("qfieldcloud/checkout/x", tmp_path)
    dropped = cache.trim("qfieldcloud/images/", keep=2, protect={"qfieldcloud/images/a"})
    assert sorted(dropped) == ["qfieldcloud/images/b", "qfieldcloud/images/c"]  # a protected, d newest
    remaining = set(cache.load().entries)
    assert {"qfieldcloud/images/a", "qfieldcloud/images/d", "qfieldcloud/images/release"} <= remaining
    assert tmp_path.is_dir()  # only index entries go: the checkout they point at stays


def test_trim_with_nothing_to_drop(cache: Cache, tmp_path: Path) -> None:
    cache.touch("qfieldcloud/images/a", tmp_path, images=["qft-qfieldcloud-app:a"])
    assert cache.trim("qfieldcloud/images/", keep=1, protect={"qfieldcloud/images/a", "qfieldcloud/images/b"}) == {}
