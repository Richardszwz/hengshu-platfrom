from app import netinfo


def test_parse_nics():
    text = ('noise\n[{"name":"eth0","ip":"192.168.66.50","speed":10000,"up":true,"virtual":false},'
            '{"name":"eth1","ip":"","speed":25000,"up":false,"virtual":false}]\n')
    nics = netinfo.parse_nics(text)
    assert len(nics) == 2
    assert nics[0]["name"] == "eth0" and nics[1]["ip"] == "" and not nics[1]["up"]


def test_parse_nics_garbage():
    assert netinfo.parse_nics("docker error output") == []
    assert netinfo.parse_nics("") == []
