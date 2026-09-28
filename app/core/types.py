"""Type aliases shared by more than one module.

Each module keeps its own domain-specific literals; only the vocabulary that
genuinely crosses module boundaries lives here.
"""

from typing import Literal

#: Which bucket a provider (or an API key belonging to it) may be used for.
#: ``any`` means the key works for both free and paid routing.
Tier = Literal["free", "paid", "any"]
