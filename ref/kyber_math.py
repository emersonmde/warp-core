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


def cbd_sample_eta2(input_bytes: list) -> list:
    """CBD η=2 sampling: convert 128 random bytes to 256 coefficients in [0, q-1].

    Each byte produces 2 coefficients from its low and high nibbles:
        nibble [b3 b2 b1 b0] → coeff = (b0+b1) - (b2+b3)
        Result in [-2, 2], mapped to [0, q-1] via mod q.

    FIPS 203, Section 4.2.2 (CBD_η with η=2).
    """
    assert len(input_bytes) == 128, f"CBD η=2 requires 128 bytes, got {len(input_bytes)}"
    coeffs = []
    for byte_val in input_bytes:
        for shift in (0, 4):  # low nibble, then high nibble
            nibble = (byte_val >> shift) & 0xF
            a = (nibble & 1) + ((nibble >> 1) & 1)      # b0 + b1
            b = ((nibble >> 2) & 1) + ((nibble >> 3) & 1)  # b2 + b3
            coeff = (a - b) % KYBER_Q
            coeffs.append(coeff)
    return coeffs


def keygen_inner(A_hat, s_noise, e_noise):
    """ML-KEM-768 key generation inner function (deterministic).

    Computes t_hat = A_hat * NTT(s) + NTT(e) in the NTT domain.

    Args:
        A_hat: 3x3 list of NTT-domain polynomials. A_hat[j][i] is row j, col i.
        s_noise: list of 3 time-domain noise polynomials (CBD sampled).
        e_noise: list of 3 time-domain noise polynomials (CBD sampled).

    Returns:
        (t_hat, s_hat): t_hat is list of 3 NTT-domain polynomials,
                        s_hat is list of 3 NTT-domain polynomials (secret key).

    Note: keygen computes A * s_hat (row access), while encaps computes
    A^T * r_hat (column access). Same A_hat layout, different traversal.
    """
    # NTT(s) and NTT(e)
    s_hat = [ntt_forward(s_noise[i]) for i in range(3)]
    e_hat = [ntt_forward(e_noise[i]) for i in range(3)]

    # t_hat[i] = sum_j A_hat[i][j] * s_hat[j] + e_hat[i]  (row i of A)
    t_hat = []
    for i in range(3):
        acc = poly_basemul(A_hat[i][0], s_hat[0])
        for j in range(1, 3):
            temp = poly_basemul(A_hat[i][j], s_hat[j])
            acc = poly_add(acc, temp)
        acc = poly_add(acc, e_hat[i])
        t_hat.append(acc)

    return t_hat, s_hat


def decrypt_inner(s_hat, u_compressed, v_compressed):
    """ML-KEM-768 decryption inner function (K-PKE.Decrypt).

    Decompresses ciphertext, computes inner product, subtracts, compresses
    to recover the message.

    Args:
        s_hat: list of 3 NTT-domain polynomials (secret key).
        u_compressed: list of 3 polynomials, each coefficient compressed with D=10.
        v_compressed: polynomial with coefficients compressed with D=4.

    Returns:
        m_prime: list of 256 values in {0, 1} — recovered message bits.
    """
    # Decompress u (D=10) and v (D=4)
    u = [
        [decompress_q(c, 10) for c in u_compressed[i]]
        for i in range(3)
    ]
    v = [decompress_q(c, 4) for c in v_compressed]

    # NTT(u)
    u_hat = [ntt_forward(u[i]) for i in range(3)]

    # Inner product: w = INTT(s_hat^T * u_hat)
    acc = poly_basemul(s_hat[0], u_hat[0])
    for j in range(1, 3):
        temp = poly_basemul(s_hat[j], u_hat[j])
        acc = poly_add(acc, temp)
    w = ntt_inverse(acc)

    # v - w
    diff = poly_sub(v, w)

    # Compress D=1 → message bits
    m_prime = [compress_q(c, 1) for c in diff]

    return m_prime


def encaps_inner(A_hat, t_hat, r, e1, e2, m):
    """ML-KEM-768 encapsulation inner function (deterministic).

    Performs the core encapsulation computation, matching the hardware
    sequencer's operation order in encaps_ctrl.v.

    Args:
        A_hat: 3x3 list of NTT-domain polynomials. A_hat[j][i] is row j, col i.
        t_hat: list of 3 NTT-domain polynomials (public key).
        r: list of 3 time-domain noise polynomials (CBD sampled).
        e1: list of 3 time-domain noise polynomials (CBD sampled).
        e2: time-domain noise polynomial (CBD sampled).
        m: time-domain message polynomial (decompressed from 32-byte message).

    Returns:
        (u, v): u is list of 3 uncompressed polynomials, v is uncompressed polynomial.
        Caller applies compress separately if needed.
    """
    # Phase 1: NTT(r)
    r_hat = [ntt_forward(r[i]) for i in range(3)]

    # Phase 2: u = INTT(A_hat^T * r_hat) + e1
    u = []
    for i in range(3):
        # Inner product: sum_j A_hat[j][i] * r_hat[j]
        acc = poly_basemul(A_hat[0][i], r_hat[0])
        for j in range(1, 3):
            temp = poly_basemul(A_hat[j][i], r_hat[j])
            acc = poly_add(acc, temp)
        acc = ntt_inverse(acc)
        acc = poly_add(acc, e1[i])
        u.append(acc)

    # Phase 3: v = INTT(t_hat^T * r_hat) + e2 + m
    v_acc = poly_basemul(t_hat[0], r_hat[0])
    for j in range(1, 3):
        temp = poly_basemul(t_hat[j], r_hat[j])
        v_acc = poly_add(v_acc, temp)
    v = ntt_inverse(v_acc)
    v = poly_add(v, e2)
    v = poly_add(v, m)

    return u, v


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
