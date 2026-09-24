"""Jetson tegrastats 解析器测试（Orin 真实输出样例）。"""
from app.collectors.tegra import parse_tegrastats

ORIN_LINE = (
    "09-24-2026 20:11:03 RAM 6543/62841MB (lfb 1218x4MB) SWAP 0/31420MB (cached 0MB) "
    "CPU [3%@1190,1%@1190,0%@1190,0%@1190,off,off,off,off] EMC_FREQ 0% "
    "GR3D_FREQ 37% AO@35C GPU@35.5C Tdiode@36C PMIC@50C AUX@35C CPU@36C "
    "thermal@35.3C VDD_IN 5444mW/5444mW VDD_CPU_GPU 764mW/764mW VDD_SOC 1374mW/1374mW"
)


def test_parse_full_line():
    m = parse_tegrastats(ORIN_LINE)
    assert m["gpu_util"] == 37.0
    assert abs(m["vram"] - 6543 / 62841 * 100) < 0.05  # 折算百分比
    assert m["temp"] == 35.5
    assert m["power"] == 5.4


def test_parse_zero_gpu():
    line = ORIN_LINE.replace("GR3D_FREQ 37%", "GR3D_FREQ 0%")
    assert parse_tegrastats(line)["gpu_util"] == 0.0


def test_parse_no_date_prefix():
    line = "RAM 1024/32000MB GR3D_FREQ 12% GPU@40C VDD_IN 2000mW/2000mW"
    m = parse_tegrastats(line)
    assert m["gpu_util"] == 12.0
    assert m["temp"] == 40.0
    assert m["power"] == 2.0


def test_parse_empty():
    assert parse_tegrastats("") == {"gpu_util": 0.0, "vram": 0.0,
                                    "temp": 0.0, "power": 0.0}
