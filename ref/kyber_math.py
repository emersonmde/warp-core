"""Python reference implementations for Kyber modular arithmetic.

Used as test oracles by cocotb testbenches. Every hardware output is
cross-checked against these functions.
"""

KYBER_Q = 3329
KYBER_N = 256
KYBER_N_INV = 3303     # 128^-1 mod 3329 (INTT scaling factor)
BARRETT_V = 20158      # floor(2^26 / 3329)
BARRETT_SHIFT = 26
HALF_Q = 1664          # (q-1)/2, rounding constant for compress
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


def poly_add(a: list, b: list) -> list:
    """Coefficient-wise polynomial addition in Z_q.

    Computes a[i] + b[i] mod q for all 256 coefficients.
    """
    assert len(a) == KYBER_N and len(b) == KYBER_N
    return [mod_add(a[i], b[i]) for i in range(KYBER_N)]


def poly_sub(a: list, b: list) -> list:
    """Coefficient-wise polynomial subtraction in Z_q.

    Computes a[i] - b[i] mod q for all 256 coefficients.
    """
    assert len(a) == KYBER_N and len(b) == KYBER_N
    return [mod_sub(a[i], b[i]) for i in range(KYBER_N)]


def compress_q(x: int, d: int) -> int:
    """Compress: round(2^d * x / q) mod 2^d.

    Integer form: floor(((x << d) + 1664) / q) mod 2^d
    FIPS 203, Section 4.2.1.
    """
    assert 0 <= x < KYBER_Q, f"compress_q input x={x} out of range [0, {KYBER_Q-1}]"
    assert d in (1, 4, 5, 10, 11), f"compress_q d={d} not a valid Kyber D value"
    return (((x << d) + HALF_Q) // KYBER_Q) % (1 << d)


def decompress_q(y: int, d: int) -> int:
    """Decompress: round(q * y / 2^d).

    Integer form: (q * y + (1 << (d-1))) >> d
    FIPS 203, Section 4.2.1.
    """
    assert 0 <= y < (1 << d), f"decompress_q input y={y} out of range [0, {(1 << d) - 1}]"
    assert d in (1, 4, 5, 10, 11), f"decompress_q d={d} not a valid Kyber D value"
    return (KYBER_Q * y + (1 << (d - 1))) >> d


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


def basemul(a0: int, a1: int, b0: int, b1: int, zeta: int) -> tuple:
    """Single 2x2 basemul in Z_q[X]/(X^2 - zeta).

    Computes (a0 + a1*X) * (b0 + b1*X) mod (X^2 - zeta):
        c0 = a0*b0 + a1*b1*zeta   (mod q)
        c1 = a0*b1 + a1*b0        (mod q)

    Uses same Barrett reduction strategy as hardware:
        t1   = barrett_reduce(a1 * b1)
        acc0 = (a0 * b0) + (t1 * zeta)   → barrett_reduce
        acc1 = (a0 * b1) + (a1 * b0)     → barrett_reduce
    """
    assert all(0 <= v < KYBER_Q for v in [a0, a1, b0, b1, zeta])
    t1   = barrett_reduce(a1 * b1)
    acc0 = (a0 * b0) + (t1 * zeta)
    c0   = barrett_reduce(acc0)
    acc1 = (a0 * b1) + (a1 * b0)
    c1   = barrett_reduce(acc1)
    return (c0, c1)


def poly_basemul(a: list, b: list) -> list:
    """Pointwise polynomial multiplication in the NTT domain.

    Each pair of coefficients [2i, 2i+1] represents a degree-1 polynomial
    in Z_q[X]/(X^2 - gamma_i). There are 128 such pairs, processed as
    64 groups of 2 basemuls (one with +zeta, one with -zeta).

    Matches the pq-crystals C reference (poly.c: poly_basemul_montgomery).
    """
    assert len(a) == KYBER_N and len(b) == KYBER_N
    r = [0] * KYBER_N

    for i in range(64):
        zeta = ZETAS[64 + i]
        neg_zeta = KYBER_Q - zeta  # -zeta mod q

        # +zeta basemul on coefficients [4i, 4i+1]
        r[4*i], r[4*i+1] = basemul(a[4*i], a[4*i+1], b[4*i], b[4*i+1], zeta)

        # -zeta basemul on coefficients [4i+2, 4i+3]
        r[4*i+2], r[4*i+3] = basemul(a[4*i+2], a[4*i+3], b[4*i+2], b[4*i+3], neg_zeta)

    return r


def schoolbook_mul(a: list, b: list) -> list:
    """Schoolbook polynomial multiplication mod (X^256 + 1).

    Used for end-to-end verification: INTT(basemul(NTT(a), NTT(b))) should
    equal schoolbook_mul(a, b).
    """
    assert len(a) == KYBER_N and len(b) == KYBER_N
    c = [0] * (2 * KYBER_N)
    for i in range(KYBER_N):
        for j in range(KYBER_N):
            c[i + j] += a[i] * b[j]

    # Reduce mod X^256 + 1: c[i+256] wraps with negation
    r = [0] * KYBER_N
    for i in range(KYBER_N):
        r[i] = (c[i] - c[i + KYBER_N]) % KYBER_Q

    return r


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
