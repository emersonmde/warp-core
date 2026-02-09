"""Python reference implementations for Kyber modular arithmetic.

Used as test oracles by cocotb testbenches. Every hardware output is
cross-checked against these functions.
"""

KYBER_Q = 3329
KYBER_N = 256
KYBER_N_INV = 3303     # 128^-1 mod 3329 (INTT scaling factor)
BARRETT_V = 20158      # floor(2^26 / 3329)
BARRETT_SHIFT = 26
ZETA = 17              # Primitive 256th root of unity mod 3329


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


def intt_butterfly(even: int, odd: int, zeta: int) -> tuple:
    """Inverse NTT Gentleman-Sande butterfly.

    Computes:
        even_out = (even + odd) mod q
        diff     = (odd - even) mod q
        odd_out  = (zeta * diff) mod q   (via Barrett reduction)

    Note: the difference is (odd - even), matching the pq-crystals reference
    where the INTT loop computes zeta * (f[j+len] - f[j]).

    All inputs/outputs in [0, q-1].
    Returns (even_out, odd_out) tuple.
    """
    assert 0 <= even < KYBER_Q, f"intt_butterfly even={even} out of range"
    assert 0 <= odd < KYBER_Q, f"intt_butterfly odd={odd} out of range"
    assert 0 <= zeta < KYBER_Q, f"intt_butterfly zeta={zeta} out of range"
    even_out = mod_add(even, odd)
    diff = mod_sub(odd, even)
    odd_out = barrett_reduce(zeta * diff)
    return (even_out, odd_out)


def bitrev7(x: int) -> int:
    """Reverse bottom 7 bits of x."""
    result = 0
    for i in range(7):
        result |= ((x >> i) & 1) << (6 - i)
    return result


# Twiddle factors: zetas[k] = pow(17, bitrev7(k), 3329) for k = 0..127
ZETAS = [pow(ZETA, bitrev7(k), KYBER_Q) for k in range(128)]


def ntt_forward(coeffs: list) -> list:
    """Forward NTT (Cooley-Tukey, in-place).

    Transforms a 256-element polynomial from coefficient domain to NTT domain.
    Uses 7 layers of 128 butterflies each.
    """
    assert len(coeffs) == KYBER_N
    f = [c % KYBER_Q for c in coeffs]

    k = 1
    length = 128
    while length >= 2:
        start = 0
        while start < KYBER_N:
            zeta = ZETAS[k]
            k += 1
            for j in range(start, start + length):
                t = barrett_reduce(zeta * f[j + length])
                f[j + length] = mod_sub(f[j], t)
                f[j] = mod_add(f[j], t)
            start += 2 * length
        length >>= 1

    return f


def ntt_inverse(coeffs: list) -> list:
    """Inverse NTT (Gentleman-Sande, in-place).

    Transforms a 256-element polynomial from NTT domain back to coefficient domain.
    Uses 7 layers of 128 butterflies each, followed by scaling by 128^-1 mod q.
    """
    assert len(coeffs) == KYBER_N
    f = [c % KYBER_Q for c in coeffs]

    k = 127
    length = 2
    while length <= 128:
        start = 0
        while start < KYBER_N:
            zeta = ZETAS[k]
            k -= 1
            for j in range(start, start + length):
                t = f[j]
                f[j] = mod_add(t, f[j + length])
                f[j + length] = barrett_reduce(zeta * mod_sub(f[j + length], t))
            start += 2 * length
        length <<= 1

    # Scale all coefficients by 128^-1 mod q
    for i in range(KYBER_N):
        f[i] = barrett_reduce(f[i] * KYBER_N_INV)

    return f
