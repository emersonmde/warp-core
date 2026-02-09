#!/usr/bin/env python3
"""Standalone NTT round-trip verification.

Verifies that ntt_inverse(ntt_forward(poly)) == poly for random polynomials,
and cross-checks against the kyber-py reference package.
"""

import random
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from kyber_math import (
    KYBER_Q, KYBER_N, KYBER_N_INV, ZETAS,
    ntt_forward, ntt_inverse, bitrev7, mod_q,
)


def test_constants():
    """Verify fundamental constants."""
    # 17 is a primitive 256th root of unity
    assert pow(17, 256, KYBER_Q) == 1, "17^256 != 1 mod q"
    assert pow(17, 128, KYBER_Q) != 1, "17^128 == 1 mod q (not primitive)"

    # 128^-1 mod q = 3303
    assert (128 * KYBER_N_INV) % KYBER_Q == 1, "128 * 3303 != 1 mod q"

    # Zetas table spot checks
    assert ZETAS[0] == 1, f"zetas[0] = {ZETAS[0]}, expected 1"
    assert ZETAS[1] == 1729, f"zetas[1] = {ZETAS[1]}, expected 1729"
    assert len(ZETAS) == 128, f"len(ZETAS) = {len(ZETAS)}, expected 128"

    print("PASS: Constants verified")


def test_bitrev7():
    """Verify bitrev7 is self-inverse and correct at known values."""
    for x in range(128):
        assert bitrev7(bitrev7(x)) == x, f"bitrev7 not self-inverse at {x}"

    assert bitrev7(0) == 0
    assert bitrev7(1) == 64
    assert bitrev7(64) == 1
    assert bitrev7(127) == 127

    print("PASS: bitrev7 verified")


def test_round_trip():
    """ntt_inverse(ntt_forward(poly)) == poly for random polynomials."""
    rng = random.Random(42)
    n_tests = 1000
    errors = 0

    for i in range(n_tests):
        poly = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        ntt_poly = ntt_forward(poly)
        recovered = ntt_inverse(ntt_poly)
        if recovered != poly:
            print(f"FAIL round-trip test {i}: recovered != original")
            errors += 1

    assert errors == 0, f"Round-trip: {errors}/{n_tests} failures"
    print(f"PASS: {n_tests} round-trip tests verified")


def test_known_vectors():
    """Test with known input patterns."""
    # All zeros
    poly = [0] * KYBER_N
    assert ntt_forward(poly) == poly, "NTT of zero poly should be zero"
    assert ntt_inverse(poly) == poly, "INTT of zero poly should be zero"

    # Unit polynomial: [1, 0, 0, ..., 0]
    poly = [1] + [0] * (KYBER_N - 1)
    ntt_poly = ntt_forward(poly)
    recovered = ntt_inverse(ntt_poly)
    assert recovered == poly, "Round-trip failed for unit polynomial"

    # Constant polynomial: [c, c, c, ..., c]
    for c in [1, 100, KYBER_Q - 1]:
        poly = [c] * KYBER_N
        recovered = ntt_inverse(ntt_forward(poly))
        assert recovered == poly, f"Round-trip failed for constant {c}"

    print("PASS: Known vectors verified")


def test_linearity():
    """NTT is linear: NTT(a + b) == NTT(a) + NTT(b) (mod q)."""
    rng = random.Random(99)

    for _ in range(100):
        a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        ab = [(a[i] + b[i]) % KYBER_Q for i in range(KYBER_N)]

        ntt_a = ntt_forward(a)
        ntt_b = ntt_forward(b)
        ntt_ab = ntt_forward(ab)

        ntt_sum = [(ntt_a[i] + ntt_b[i]) % KYBER_Q for i in range(KYBER_N)]
        assert ntt_ab == ntt_sum, "NTT linearity violated"

    print("PASS: NTT linearity verified (100 tests)")


def test_cross_check_kyber_py():
    """Cross-check against kyber-py reference package."""
    try:
        from kyber_py.polynomials.polynomials_generic import PolynomialRing
    except ImportError:
        print("SKIP: kyber-py not installed, skipping cross-check")
        return

    rng = random.Random(77)
    R = PolynomialRing(KYBER_Q, KYBER_N)

    for _ in range(100):
        coeffs = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        poly = R(coeffs)
        ref_ntt = poly.to_ntt().coeffs
        our_ntt = ntt_forward(coeffs)

        if list(ref_ntt) != our_ntt:
            print(f"FAIL: NTT output mismatch with kyber-py")
            print(f"  First diff at index {next(i for i in range(256) if ref_ntt[i] != our_ntt[i])}")
            sys.exit(1)

    print("PASS: Cross-check against kyber-py (100 polynomials)")


if __name__ == "__main__":
    test_constants()
    test_bitrev7()
    test_known_vectors()
    test_round_trip()
    test_linearity()
    test_cross_check_kyber_py()
    print("\nAll NTT verification tests passed!")
