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


def cond_add_q(a: int) -> int:
    """Conditional addition of Q for subtraction underflow correction.

    Input: 13-bit result of unsigned subtraction {1'b0,x} - {1'b0,y}.
    If bit[12] is set (borrow), the subtraction underflowed and we add Q.

    Mirrors the hardware cond_add_q module.
    """
    assert 0 <= a < 2**13, f"cond_add_q input {a} out of range [0, 8191]"
    if a & 0x1000:  # bit[12] set = borrow occurred
        return ((a & 0xFFF) + KYBER_Q) & 0xFFF  # 12-bit result, matching hardware sum[11:0]
    else:
        return a & 0xFFF


def mod_add(a: int, b: int) -> int:
    """Modular addition in Z_q: (a + b) mod 3329.

    Both inputs must be in [0, q-1]. Sum is at most 2q-2 < 2q-1,
    so cond_sub_q handles the reduction.
    """
    assert 0 <= a < KYBER_Q, f"mod_add input a={a} out of range [0, {KYBER_Q-1}]"
    assert 0 <= b < KYBER_Q, f"mod_add input b={b} out of range [0, {KYBER_Q-1}]"
    return cond_sub_q(a + b)


def mod_sub(a: int, b: int) -> int:
    """Modular subtraction in Z_q: (a - b) mod 3329.

    Both inputs must be in [0, q-1]. Uses 13-bit unsigned subtraction:
    diff = {1'b0,a} - {1'b0,b}. If a < b, bit[12] is set (borrow),
    and cond_add_q corrects by adding Q.
    """
    assert 0 <= a < KYBER_Q, f"mod_sub input a={a} out of range [0, {KYBER_Q-1}]"
    assert 0 <= b < KYBER_Q, f"mod_sub input b={b} out of range [0, {KYBER_Q-1}]"
    diff = ((1 << 13) + a - b) if a < b else (a - b)
    return cond_add_q(diff & 0x1FFF)


def ntt_butterfly(even: int, odd: int, zeta: int) -> tuple:
    """NTT Cooley-Tukey butterfly.

    Computes:
        t = (zeta * odd) mod q     (via Barrett reduction)
        even_out = (even + t) mod q
        odd_out  = (even - t) mod q

    All inputs/outputs in [0, q-1].
    Returns (even_out, odd_out) tuple.
    """
    assert 0 <= even < KYBER_Q, f"ntt_butterfly even={even} out of range"
    assert 0 <= odd < KYBER_Q, f"ntt_butterfly odd={odd} out of range"
    assert 0 <= zeta < KYBER_Q, f"ntt_butterfly zeta={zeta} out of range"
    t = barrett_reduce(zeta * odd)
    even_out = mod_add(even, t)
    odd_out = mod_sub(even, t)
    return (even_out, odd_out)
