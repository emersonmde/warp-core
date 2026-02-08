"""Python reference implementations for Kyber modular arithmetic.

Used as test oracles by cocotb testbenches. Every hardware output is
cross-checked against these functions.
"""

KYBER_Q = 3329
BARRETT_V = 20158      # floor(2^26 / 3329)
BARRETT_SHIFT = 26


def mod_q(a: int) -> int:
    """Canonical modular reduction: a mod 3329, result in [0, 3328]."""
    return a % KYBER_Q


def cond_sub_q(a: int) -> int:
    """Conditional subtraction: reduce [0, 2q-1] to [0, q-1].

    Mirrors the hardware cond_sub_q module.
    """
    assert 0 <= a < 2 * KYBER_Q, f"cond_sub_q input {a} out of range [0, {2*KYBER_Q - 1}]"
    return a - KYBER_Q if a >= KYBER_Q else a


def barrett_reduce(a: int) -> int:
    """Barrett reduction matching the hardware implementation.

    Computes a mod 3329 using:
        t = (a * V) >> 26      # quotient estimate (never overestimates)
        r = a - t * Q           # remainder in [0, 2q-1]
        result = cond_sub_q(r)  # final reduction to [0, q-1]

    Safe for a < 77,517,490.
    """
    assert 0 <= a < 77_517_490, f"barrett_reduce input {a} out of safe range"
    t = (a * BARRETT_V) >> BARRETT_SHIFT
    r = a - t * KYBER_Q
    assert 0 <= r < 2 * KYBER_Q, f"remainder {r} out of range after Barrett step"
    return cond_sub_q(r)
