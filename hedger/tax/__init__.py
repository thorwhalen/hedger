"""Tax: pluggable jurisdiction policies as Decision middleware."""

from hedger.tax.policies import (
    CryptoLIFOPolicy,
    FrenchPFUPolicy,
    NoTaxPolicy,
    USWashSalePolicy,
    get_policy,
    register_policy,
)

__all__ = [
    "NoTaxPolicy",
    "USWashSalePolicy",
    "CryptoLIFOPolicy",
    "FrenchPFUPolicy",
    "get_policy",
    "register_policy",
]
