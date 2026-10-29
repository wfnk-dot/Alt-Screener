"""Which Binance perps are in scope: name matching to Hyperliquid and exclusions."""
import re

# Binance prefixes contracts with a multiplier for low-priced coins, e.g. 1000PEPE, 1000000MOG, 1MBABYDOGE
_MULTIPLIER = re.compile(r"^(1000000|100000|10000|1000|100|1M)(?=[A-Z])")
# Hyperliquid prefixes the same coins with "k" (thousand), e.g. kPEPE
_HL_K = re.compile(r"^k(?=[A-Z0-9])")


def base_from_symbol(symbol, quote="USDT"):
    """'1000PEPEUSDT' -> '1000PEPE'. None if not quoted in `quote` (e.g. USDC pairs, dated futures)."""
    if not symbol.endswith(quote) or "_" in symbol:
        return None
    base = symbol[: -len(quote)]
    return base or None


def normalise_binance(base):
    """'1000PEPE' -> 'PEPE'."""
    return _MULTIPLIER.sub("", base.upper())


def normalise_hl(name):
    """'kPEPE' -> 'PEPE'."""
    return _HL_K.sub("", name).upper()


def match_hyperliquid(binance_bases, hl_names, aliases=None):
    """Map each Binance base to its Hyperliquid coin name, or leave it out if not listed there.

    Manual aliases (Binance base -> HL name) win; otherwise names are compared after
    stripping Binance's 1000/1000000 prefix and Hyperliquid's 'k' prefix.
    """
    aliases = {str(k).upper(): str(v) for k, v in (aliases or {}).items()}
    hl_set = set(hl_names)
    by_norm = {}
    for name in hl_names:
        by_norm.setdefault(normalise_hl(name), name)
    out = {}
    for base in binance_bases:
        alias = aliases.get(base.upper())
        if alias and alias in hl_set:
            out[base] = alias
        elif base in hl_set:
            out[base] = base
        else:
            hit = by_norm.get(normalise_binance(base))
            if hit:
                out[base] = hit
    return out


def excluded_names(cfg):
    names = set()
    for group in (cfg["universe"].get("exclude") or {}).values():
        names.update(str(n).upper() for n in (group or []))
    return names


def is_excluded(base, excluded):
    """True if the coin is a stablecoin, core coin, gold, index or tradfi contract."""
    return base.upper() in excluded or normalise_binance(base) in excluded
