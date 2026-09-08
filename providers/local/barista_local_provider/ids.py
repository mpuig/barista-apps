"""Node identities: the Contract A ULID, independent of Host API handles."""

import secrets
import time


def node_id() -> str:
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    value = ((time.time_ns() // 1_000_000) << 80) | secrets.randbits(80)
    return "".join(alphabet[(value >> shift) & 31] for shift in range(125, -1, -5))
