from screener.main import allowed_from_exchange_info
from screener.universe import base_from_symbol, excluded_names, is_excluded, match_hyperliquid

HL = ["BTC", "ETH", "kPEPE", "kBONK", "DOGE", "MOG", "WIF", "kNEIRO", "TAO", "1INCH"]


def test_base_from_symbol():
    assert base_from_symbol("1000PEPEUSDT") == "1000PEPE"
    assert base_from_symbol("BTCUSDC") is None
    assert base_from_symbol("BTCUSDT_251226") is None


def test_auto_matching():
    m = match_hyperliquid(["1000PEPE", "1000BONK", "DOGE", "1000000MOG", "NEIRO", "1INCH", "XYZ"], HL)
    assert m == {"1000PEPE": "kPEPE", "1000BONK": "kBONK", "DOGE": "DOGE", "1000000MOG": "MOG",
                 "NEIRO": "kNEIRO", "1INCH": "1INCH"}


def test_alias_wins():
    m = match_hyperliquid(["BEAMX", "DOGE"], ["BEAM", "DOGE", "BEAMX2"], {"BEAMX": "BEAM"})
    assert m == {"BEAMX": "BEAM", "DOGE": "DOGE"}


def test_alias_to_missing_coin_falls_back():
    assert match_hyperliquid(["WIF"], ["WIF"], {"WIF": "NOPE"}) == {"WIF": "WIF"}


def test_config_aliases_resolve(cfg):
    m = match_hyperliquid(["1000PEPE", "1MBABYDOGE"], ["kPEPE", "kBABYDOGE"], cfg["universe"]["hyperliquid_aliases"])
    assert m == {"1000PEPE": "kPEPE", "1MBABYDOGE": "kBABYDOGE"}


def test_exclusions(cfg):
    ex = excluded_names(cfg)
    for base in ["BTC", "ETH", "SOL", "HYPE", "XAU", "PAXG", "USDC", "FDUSD", "BTCDOM", "DEFI", "TSLA", "USDE"]:
        assert is_excluded(base, ex), base
    for base in ["DOGE", "1000PEPE", "WIF", "SUI"]:
        assert not is_excluded(base, ex), base


def test_exchange_info_filter():
    far = 4133404800000
    info = {"symbols": [
        {"symbol": "DOGEUSDT", "contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT", "underlyingType": "COIN", "deliveryDate": far},
        {"symbol": "OLDUSDT", "contractType": "PERPETUAL", "status": "SETTLING", "quoteAsset": "USDT", "underlyingType": "COIN", "deliveryDate": far},
        {"symbol": "BYEUSDT", "contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT", "underlyingType": "COIN", "deliveryDate": 1793059200000},
        {"symbol": "BTCDOMUSDT", "contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT", "underlyingType": "INDEX", "deliveryDate": far},
        {"symbol": "TSLAUSDT", "contractType": "PERPETUAL", "status": "TRADING", "quoteAsset": "USDT", "underlyingType": "EQUITY", "deliveryDate": far},
        {"symbol": "BTCUSDT_261225", "contractType": "CURRENT_QUARTER", "status": "TRADING", "quoteAsset": "USDT", "underlyingType": "COIN", "deliveryDate": far},
    ]}
    assert allowed_from_exchange_info(info, "USDT") == {"DOGEUSDT"}
