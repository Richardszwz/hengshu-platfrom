from app import netinfo


def test_parse_nics():
    text = 'some noise\n[{"name":"eth0","ip":"192.168.66.50","speed":10000,"up":true},{"name":"eth1","ip":"10.0.0.5","speed":25000,"up":true}]\n'
    nics = netinfo.parse_nics(text)
    assert len(nics) == 2 and nics[0]["name"] == "eth0" and nics[1]["speed"] == 25000


def test_parse_nics_garbage():
    assert netinfo.parse_nics("docker error output") == []
    assert netinfo.parse_nics("") == []
