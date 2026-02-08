"""Testbench for ntt_butterfly module.

Tests the Cooley-Tukey NTT butterfly:
    t        = (zeta * odd) mod q
    even_out = (even + t) mod q
    odd_out  = (even - t) mod q

Covers boundary values, algebraic properties, and random triples.
"""

import sys
import os
import random

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, ntt_butterfly, mod_q


async def drive_and_check(dut, even, odd, zeta):
    """Drive inputs, wait, return (even_out, odd_out, exp_even, exp_odd, match)."""
    exp_even, exp_odd = ntt_butterfly(even, odd, zeta)
    dut.even.value = even
    dut.odd.value = odd
    dut.zeta.value = zeta
    await Timer(1, unit='ns')
    result_even = dut.even_out.value.to_unsigned()
    result_odd = dut.odd_out.value.to_unsigned()
    match = (result_even == exp_even) and (result_odd == exp_odd)
    return result_even, result_odd, exp_even, exp_odd, match


@cocotb.test()
async def test_boundary_values(dut):
    """Test specific boundary and corner-case triples."""
    cases = [
        # (even, odd, zeta) — description
        (0, 0, 0),                          # all zeros
        (0, 0, 1),                          # zeta=1, odd=0 → t=0
        (0, 1, 0),                          # zeta=0 → t=0
        (0, 1, 1),                          # t=1
        (1, 0, 1),                          # odd=0 → passthrough
        (1, 1, 1),                          # t=1: even_out=2, odd_out=0
        (KYBER_Q - 1, 0, 0),               # max even, zero product
        (KYBER_Q - 1, KYBER_Q - 1, 1),     # max even+odd, zeta=1
        (0, KYBER_Q - 1, KYBER_Q - 1),     # max product: 3328^2
        (KYBER_Q - 1, KYBER_Q - 1, KYBER_Q - 1),  # all max
        (KYBER_Q // 2, KYBER_Q // 2, 2),   # midpoint values
        (1, 1, KYBER_Q - 1),               # zeta = q-1 ≡ -1 mod q → t = q-1
    ]
    errors = 0
    for even, odd, zeta in cases:
        re, ro, ee, eo, match = await drive_and_check(dut, even, odd, zeta)
        if not match:
            dut._log.error(
                f"FAIL: butterfly({even}, {odd}, {zeta}) = ({re}, {ro}), "
                f"expected ({ee}, {eo})"
            )
            errors += 1
        else:
            dut._log.info(
                f"  butterfly({even}, {odd}, {zeta}) = ({ee}, {eo})"
            )

    assert errors == 0, f"Boundary test: {errors} errors out of {len(cases)}"
    dut._log.info(f"PASS: All {len(cases)} boundary cases verified")


@cocotb.test()
async def test_algebraic_properties(dut):
    """Test algebraic properties that must hold for correctness."""
    rng = random.Random(123)
    errors = 0

    # Property 1: zeta=0 → even_out = even, odd_out = even
    # (t = 0*odd = 0, so even+0=even, even-0=even)
    for _ in range(100):
        even = rng.randint(0, KYBER_Q - 1)
        odd = rng.randint(0, KYBER_Q - 1)
        re, ro, ee, eo, match = await drive_and_check(dut, even, odd, 0)
        if not match or re != even or ro != even:
            dut._log.error(
                f"FAIL zeta=0: butterfly({even}, {odd}, 0) = ({re}, {ro}), "
                f"expected ({even}, {even})"
            )
            errors += 1

    # Property 2: odd=0 → even_out = even, odd_out = even
    # (t = zeta*0 = 0)
    for _ in range(100):
        even = rng.randint(0, KYBER_Q - 1)
        zeta = rng.randint(0, KYBER_Q - 1)
        re, ro, ee, eo, match = await drive_and_check(dut, even, 0, zeta)
        if not match or re != even or ro != even:
            dut._log.error(
                f"FAIL odd=0: butterfly({even}, 0, {zeta}) = ({re}, {ro}), "
                f"expected ({even}, {even})"
            )
            errors += 1

    # Property 3: even_out + odd_out ≡ 2*even (mod q)
    # Because (even+t) + (even-t) = 2*even
    for _ in range(1000):
        even = rng.randint(0, KYBER_Q - 1)
        odd = rng.randint(0, KYBER_Q - 1)
        zeta = rng.randint(0, KYBER_Q - 1)
        re, ro, ee, eo, match = await drive_and_check(dut, even, odd, zeta)
        if not match:
            errors += 1
            continue
        sum_out = (re + ro) % KYBER_Q
        expected_sum = (2 * even) % KYBER_Q
        if sum_out != expected_sum:
            dut._log.error(
                f"FAIL sum: butterfly({even}, {odd}, {zeta}): "
                f"even_out + odd_out = {sum_out}, expected 2*even = {expected_sum}"
            )
            errors += 1

    assert errors == 0, f"Algebraic property test: {errors} errors"
    dut._log.info("PASS: All algebraic property tests verified (zeta=0, odd=0, sum invariant)")


@cocotb.test()
async def test_random_triples(dut):
    """100k random (even, odd, zeta) triples."""
    rng = random.Random(42)
    n_samples = 100_000
    errors = 0

    for _ in range(n_samples):
        even = rng.randint(0, KYBER_Q - 1)
        odd = rng.randint(0, KYBER_Q - 1)
        zeta = rng.randint(0, KYBER_Q - 1)
        re, ro, ee, eo, match = await drive_and_check(dut, even, odd, zeta)
        if not match:
            dut._log.error(
                f"FAIL: butterfly({even}, {odd}, {zeta}) = ({re}, {ro}), "
                f"expected ({ee}, {eo})"
            )
            errors += 1

    assert errors == 0, f"Random test: {errors} errors out of {n_samples}"
    dut._log.info(f"PASS: {n_samples} random triples verified")
