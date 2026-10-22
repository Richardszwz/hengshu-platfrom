"""镜像库扫描与匹配测试（用 dist/ 真实 tar 产物）。"""
from app import image_registry
from app.config import settings


def _use_dist():
    object.__setattr__(settings, "install_tar_dir", r"D:\hengshu\dist")
    image_registry._cache = {}
    image_registry._cache_at = 0.0


def test_scan_real_tars():
    _use_dist()
    lib = image_registry._scan()
    tags = sorted(lib)
    print("镜像库:", tags)
    # agent / hse / image-server 至少该有
    assert any("hengshu/agent" in t for t in tags), tags
    assert any("hengshu/hse" in t for t in tags), tags
    # 架构解析
    for e in lib.values():
        assert e.arch in ("amd64", "arm64"), (e.tag, e.arch)


def test_find_tar_exact_and_fuzzy():
    _use_dist()
    image_registry._scan()
    e = image_registry.find_tar("hengshu/hse:latest")
    assert e and e.arch == "amd64"
    e2 = image_registry.find_tar("hengshu/hse:latest-arm64")
    assert e2 and e2.arch == "arm64"
    # 模糊：短名 + 架构优先
    e3 = image_registry.find_tar("hse", arch="arm64")
    assert e3 and "arm64" in e3.tag
    e4 = image_registry.find_tar("hse", arch="amd64")
    assert e4 and "arm64" not in e4.tag
    assert image_registry.find_tar("no-such-image-xyz") is None


def test_list_images_shape():
    _use_dist()
    out = image_registry.list_images()
    assert out and all({"tag", "arch", "size_mb", "file"} <= set(x) for x in out)
