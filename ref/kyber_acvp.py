"""FIPS 203 encoding, hashing, and full algorithm wrappers for ML-KEM-768.

Builds on kyber_math.py (arithmetic oracle) to provide:
- ByteEncode/ByteDecode (FIPS 203 Algorithms 4-5)
- Hash primitives: G, H, J, PRF, XOF (FIPS 203 Section 4.1)
- SampleNTT rejection sampling (FIPS 203 Algorithm 6)
- Full ML-KEM algorithms: KeyGen, Encaps, Decaps (Algorithms 13-18)

Used for ACVP compliance testing: validates the oracle against NIST vectors
before trusting it for hardware verification.
"""

import hashlib

from kyber_math import (
    KYBER_Q, KYBER_N,
    ntt_forward, ntt_inverse,
    poly_basemul, poly_add, poly_sub,
    compress_q, decompress_q,
    cbd_sample_eta2,
)

# ML-KEM-768 parameters (FIPS 203, Table 1)
K = 3          # Module rank
ETA1 = 2       # CBD parameter for secret/noise
ETA2 = 2       # CBD parameter for encryption noise
DU = 10        # Ciphertext compression parameter for u
DV = 4         # Ciphertext compression parameter for v

# Derived sizes
EK_LEN = 384 * K + 32   # 1184 bytes (encapsulation key)
DK_PKE_LEN = 384 * K    # 1152 bytes (decryption key, K-PKE)
CT_LEN = 32 * DU * K + 32 * DV  # 1088 bytes (ciphertext)


# ═══════════════════════════════════════════════════════════════════
# Byte Encoding / Decoding (FIPS 203, Algorithms 4-5)
# ═══════════════════════════════════════════════════════════════════

def byte_encode(d, coeffs):
    """ByteEncode_d: 256 d-bit coefficients -> 32*d bytes.

    FIPS 203 Algorithm 4. LSB-first bit packing.
    """
    assert len(coeffs) == 256
    bits = []
    for c in coeffs:
        for j in range(d):
            bits.append((c >> j) & 1)
    out = bytearray(32 * d)
    for i, b in enumerate(bits):
        out[i >> 3] |= b << (i & 7)
    return bytes(out)


def byte_decode(d, data):
    """ByteDecode_d: 32*d bytes -> 256 d-bit coefficients.

    FIPS 203 Algorithm 5. For d=12, reduce mod q.
    """
    assert len(data) == 32 * d
    bits = []
    for byte_val in data:
        for j in range(8):
            bits.append((byte_val >> j) & 1)
    coeffs = []
    for i in range(256):
        val = 0
        for j in range(d):
            val |= bits[i * d + j] << j
        if d == 12:
            val = val % KYBER_Q
        coeffs.append(val)
    return coeffs


# ═══════════════════════════════════════════════════════════════════
# Hash Primitives (FIPS 203, Section 4.1)
# ═══════════════════════════════════════════════════════════════════

def g_hash(data):
    """G: SHA3-512 -> (rho, sigma), each 32 bytes."""
    h = hashlib.sha3_512(data).digest()
    return h[:32], h[32:]


def h_hash(data):
    """H: SHA3-256 -> 32 bytes."""
    return hashlib.sha3_256(data).digest()


def j_hash(data, length=32):
    """J: SHAKE-256 -> variable length output."""
    return hashlib.shake_256(data).digest(length)


def prf(eta, seed, nonce):
    """PRF_eta: SHAKE-256(seed || nonce) -> 64*eta bytes."""
    return hashlib.shake_256(seed + bytes([nonce])).digest(64 * eta)


def xof(seed, length):
    """XOF: SHAKE-128 -> variable length output. Used by SampleNTT."""
    return hashlib.shake_128(seed).digest(length)


# ═══════════════════════════════════════════════════════════════════
# SampleNTT (FIPS 203, Algorithm 6)
# ═══════════════════════════════════════════════════════════════════

def sample_ntt(rho, j, i):
    """Rejection sampling from SHAKE-128(rho || j || i).

    FIPS 203 Algorithm 6. Seed order is rho || j || i (column index first).
    Returns 256 NTT-domain coefficients in [0, q-1].
    """
    seed = rho + bytes([j, i])
    buf = xof(seed, 4096)
    coeffs = []
    pos = 0
    while len(coeffs) < 256:
        b0, b1, b2 = buf[pos], buf[pos + 1], buf[pos + 2]
        pos += 3
        d1 = b0 + 256 * (b1 & 0x0F)
        d2 = (b1 >> 4) + 16 * b2
        if d1 < KYBER_Q:
            coeffs.append(d1)
        if len(coeffs) < 256 and d2 < KYBER_Q:
            coeffs.append(d2)
    return coeffs


def expand_a(rho):
    """Expand rho into K x K NTT-domain matrix A_hat.

    A_hat[i][j] = SampleNTT(rho, j, i) -- note the (j, i) XOF seed order.
    """
    A_hat = [[None] * K for _ in range(K)]
    for i in range(K):
        for j in range(K):
            A_hat[i][j] = sample_ntt(rho, j, i)
    return A_hat


# ═══════════════════════════════════════════════════════════════════
# K-PKE Algorithms (FIPS 203, Algorithms 13-15)
# ═══════════════════════════════════════════════════════════════════

def k_pke_keygen(d):
    """K-PKE.KeyGen (FIPS 203, Algorithm 13).

    Input: d (32 bytes) -- seed
    Returns: (ek_pke, dk_pke) as bytes
    """
    rho, sigma = g_hash(d + bytes([K]))
    A_hat = expand_a(rho)

    s = []
    for i in range(K):
        s.append(cbd_sample_eta2(list(prf(ETA1, sigma, i))))

    e = []
    for i in range(K):
        e.append(cbd_sample_eta2(list(prf(ETA1, sigma, K + i))))

    s_hat = [ntt_forward(s[i]) for i in range(K)]
    e_hat = [ntt_forward(e[i]) for i in range(K)]

    # t_hat = A_hat * s_hat + e_hat
    t_hat = []
    for i in range(K):
        acc = poly_basemul(A_hat[i][0], s_hat[0])
        for j in range(1, K):
            temp = poly_basemul(A_hat[i][j], s_hat[j])
            acc = poly_add(acc, temp)
        acc = poly_add(acc, e_hat[i])
        t_hat.append(acc)

    ek_pke = b''
    for i in range(K):
        ek_pke += byte_encode(12, t_hat[i])
    ek_pke += rho

    dk_pke = b''
    for i in range(K):
        dk_pke += byte_encode(12, s_hat[i])

    return ek_pke, dk_pke


def k_pke_encrypt(ek, m_bytes, r_seed):
    """K-PKE.Encrypt (FIPS 203, Algorithm 14).

    Input: ek (1184 bytes), m_bytes (32 bytes), r_seed (32 bytes)
    Returns: c (1088 bytes)
    """
    # Parse ek
    t_hat = []
    for i in range(K):
        t_hat.append(byte_decode(12, ek[384 * i : 384 * (i + 1)]))
    rho = ek[384 * K:]

    A_hat = expand_a(rho)

    y = []
    for i in range(K):
        y.append(cbd_sample_eta2(list(prf(ETA1, r_seed, i))))

    e1 = []
    for i in range(K):
        e1.append(cbd_sample_eta2(list(prf(ETA2, r_seed, K + i))))

    e2 = cbd_sample_eta2(list(prf(ETA2, r_seed, 2 * K)))

    r_hat = [ntt_forward(y[i]) for i in range(K)]

    # u = INTT(A_hat^T * r_hat) + e1
    u = []
    for i in range(K):
        acc = poly_basemul(A_hat[0][i], r_hat[0])
        for j in range(1, K):
            temp = poly_basemul(A_hat[j][i], r_hat[j])
            acc = poly_add(acc, temp)
        acc = ntt_inverse(acc)
        acc = poly_add(acc, e1[i])
        u.append(acc)

    # mu = Decompress(1, ByteDecode(1, m))
    mu_coeffs = byte_decode(1, m_bytes)
    mu = [decompress_q(c, 1) for c in mu_coeffs]

    # v = INTT(t_hat^T * r_hat) + e2 + mu
    v_acc = poly_basemul(t_hat[0], r_hat[0])
    for j in range(1, K):
        temp = poly_basemul(t_hat[j], r_hat[j])
        v_acc = poly_add(v_acc, temp)
    v = ntt_inverse(v_acc)
    v = poly_add(v, e2)
    v = poly_add(v, mu)

    # Encode ciphertext
    c1 = b''
    for i in range(K):
        c1 += byte_encode(DU, [compress_q(c, DU) for c in u[i]])
    c2 = byte_encode(DV, [compress_q(c, DV) for c in v])

    return c1 + c2


def k_pke_decrypt(dk_pke, c):
    """K-PKE.Decrypt (FIPS 203, Algorithm 15).

    Input: dk_pke (1152 bytes), c (1088 bytes)
    Returns: m (32 bytes)
    """
    # Parse ciphertext
    c1_len = 32 * DU * K  # 960
    c1 = c[:c1_len]
    c2 = c[c1_len:]

    u = []
    for i in range(K):
        u_comp = byte_decode(DU, c1[32 * DU * i : 32 * DU * (i + 1)])
        u.append([decompress_q(coeff, DU) for coeff in u_comp])

    v_comp = byte_decode(DV, c2)
    v = [decompress_q(coeff, DV) for coeff in v_comp]

    # Parse secret key
    s_hat = []
    for i in range(K):
        s_hat.append(byte_decode(12, dk_pke[384 * i : 384 * (i + 1)]))

    # w = INTT(s_hat^T * NTT(u))
    u_hat = [ntt_forward(u[i]) for i in range(K)]
    acc = poly_basemul(s_hat[0], u_hat[0])
    for j in range(1, K):
        temp = poly_basemul(s_hat[j], u_hat[j])
        acc = poly_add(acc, temp)
    w = ntt_inverse(acc)

    # m = ByteEncode(1, Compress(1, v - w))
    diff = poly_sub(v, w)
    m = byte_encode(1, [compress_q(c, 1) for c in diff])

    return m


# ═══════════════════════════════════════════════════════════════════
# ML-KEM Algorithms (FIPS 203, Algorithms 16-18)
# ═══════════════════════════════════════════════════════════════════

def keygen_full(d, z):
    """ML-KEM.KeyGen (FIPS 203, Algorithm 16).

    Input: d (32 bytes), z (32 bytes)
    Returns: (ek, dk) as bytes
    """
    ek_pke, dk_pke = k_pke_keygen(d)
    ek = ek_pke
    dk = dk_pke + ek + h_hash(ek) + z
    return ek, dk


def encaps_full(ek, m):
    """ML-KEM.Encaps_internal (FIPS 203, Algorithm 17).

    Input: ek (1184 bytes), m (32 bytes)
    Returns: (K, c) -- shared secret (32 bytes) and ciphertext (1088 bytes)
    """
    h = h_hash(ek)
    K_val, r = g_hash(m + h)
    c = k_pke_encrypt(ek, m, r)
    return K_val, c


def decaps_full(dk, c):
    """ML-KEM.Decaps_internal (FIPS 203, Algorithm 18).

    Input: dk (2400 bytes), c (1088 bytes)
    Returns: K (32 bytes) -- shared secret
    """
    dk_pke = dk[:DK_PKE_LEN]
    ek = dk[DK_PKE_LEN : DK_PKE_LEN + EK_LEN]
    h = dk[DK_PKE_LEN + EK_LEN : DK_PKE_LEN + EK_LEN + 32]
    z = dk[DK_PKE_LEN + EK_LEN + 32:]

    m_prime = k_pke_decrypt(dk_pke, c)
    K_prime, r_prime = g_hash(m_prime + h)
    K_bar = j_hash(z + c)
    c_prime = k_pke_encrypt(ek, m_prime, r_prime)

    if c == c_prime:
        return K_prime
    else:
        return K_bar
