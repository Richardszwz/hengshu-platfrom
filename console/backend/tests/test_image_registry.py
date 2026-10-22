"""镜像库扫描与匹配测试（自构造最小镜像 tar，任何环境可跑）。"""
import io
import json
import tarfile
from pathlib import Path

from app import image_registry
from app.config import settings


def _fake_image_tar(path: Path, tag: str, arch: str) -> None:
    """造一个 docker save 结构的最小 tar（manifest.json + config）。"""
    cfg_name = "abc123.json"
    with tarfile.open(path, "w") as tf:
        cfg = json.dumps({"architecture": arch}).encode()
        info = tarfile.TarInfo(cfg_name)
        info.size = len(cfg)
        tf.addfile(info, io.BytesIO(cfg))
        mf = json.dumps([{"Config": cfg_name, "RepoTags": [tag]}]).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(mf)
        tf.addfile(info, io.BytesIO(mf))


def _use_tmp(tmp_path: Path) -> Path:
    d = tmp_path / "dist"
    d.mkdir()
    _fake_image_tar(d / "hse-x86_64.tar", "hengshu/hse:latest", "amd64")
    _fake_image_tar(d / "hse-aarch64.tar", "hengshu/hse:latest-arm64", "arm64")
    (d / "not-an-image.txt").write_text("junk")   # 非 tar 文件不炸扫描
    object.__setattr__(settings, "install_tar_dir", str(d))
    image_registry._cache = {}
    image_registry._cache_at = 0.0
    return d


def test_scan(tmp_path):
    _use_tmp(tmp_path)
    lib = image_registry._scan()
    tags = sorted(lib)
    assert tags == ["hengshu/hse:latest", "hengshu/hse:latest-arm64"], tags
    assert lib["hengshu/hse:latest"].arch == "amd64"
    assert lib["hengshu/hse:latest-arm64"].arch == "arm64"


def test_find_tar_exact_and_fuzzy(tmp_path):
    _use_tmp(tmp_path)
    image_registry._scan()
    assert image_registry.find_tar("hengshu/hse:latest").arch == "amd64"
    assert image_registry.find_tar("hengshu/hse:latest-arm64").arch == "arm64"
    # 模糊：短名 + 架构优先
    e3 = image_registry.find_tar("hse", arch="arm64")
    assert e3 and "arm64" in e3.tag
    e4 = image_registry.find_tar("hse", arch="amd64")
    assert e4 and "arm64" not in e4.tag
    assert image_registry.find_tar("no-such-image-xyz") is None


def test_list_images_shape(tmp_path):
    _use_tmp(tmp_path)
    out = image_registry.list_images()
    assert out and all({"tag", "arch", "size_mb", "file"} <= set(x) for x in out)
