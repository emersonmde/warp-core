"""Testbench for intt_butterfly module.

Tests the Gentleman-Sande inverse NTT butterfly:
    even_out = (even + odd) mod q
    diff     = (odd - even) mod q
    odd_out  = (zeta * diff) mod q

Covers boundary values, algebraic properties, and random triples.
"""

import sys
import os
import random

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, intt_butterfly


async def drive_and_check(dut, even, odd, zeta):
    """Drive inputs, wait, return (even_out, odd_out, exp_even, exp_odd, match)."""
    exp_even, exp_odd = intt_butterfly(even, odd, zeta)
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
        (0, 0, 0),                          # all zeros
        (0, 0, 1),                          # zeta=1, both zero
        (0, 1, 0),                          # zeta=0 → odd_out=0
        (0, 1, 1),                          # zeta=1, diff=1
        (1, 0, 1),                          # diff = q-1
        (1, 1, 1),                          # even=odd, diff=0
        (KYBER_Q - 1, 0, 0),               # max even, zero odd
        (KYBER_Q - 1, KYBER_Q - 1, 1),     # max even+odd, zeta=1
        (0, KYBER_Q - 1, KYBER_Q - 1),     # max product
        (KYBER_Q - 1, KYBER_Q - 1, KYBER_Q - 1),  # all max
        (KYBER_Q // 2, KYBER_Q // 2, 2),   # midpoint values
        (1, 1, KYBER_Q - 1),               # zeta = q-1
    ]
    errors = 0
    for even, odd, zeta in cases:
        re, ro, ee, eo, match = await drive_and_check(dut, even, odd, zeta)
        if not match:
            dut._log.error(
                f"FAIL: intt_bf({even}, {odd}, {zeta}) = ({re}, {ro}), "
                f"expected ({ee}, {eo})"
            )
            errors += 1
        else:
            dut._log.info(
                f"  intt_bf({even}, {odd}, {zeta}) = ({ee}, {eo})"
            )

    assert errors == 0, f"Boundary test: {errors} errors out of {len(cases)}"
    dut._log.info(f"PASS: All {len(cases)} boundary cases verified")


@cocotb.test()
async def test_algebraic_properties(dut):
    """Test algebraic properties of the GS butterfly."""
    rng = random.Random(123)
    errors = 0

    # Property 1: zeta=0 → odd_out=0 always (product is zero)
    for _ in range(100):
        even = rng.randint(0, KYBER_Q - 1)
        odd = rng.randint(0, KYBER_Q - 1)
        re, ro, ee, eo, match = await drive_and_check(dut, even, odd, 0)
        if not match or ro != 0:
            dut._log.error(
                f"FAIL zeta=0: intt_bf({even}, {odd}, 0) = ({re}, {ro}), "
                f"expected odd_out=0"
            )
            errors += 1

    # Property 2: even == odd → diff=0 → odd_out=0
    for _ in range(100):
        val = rng.randint(0, KYBER_Q - 1)
        zeta = rng.randint(0, KYBER_Q - 1)
        re, ro, ee, eo, match = await drive_and_check(dut, val, val, zeta)
        if not match or ro != 0:
            dut._log.error(
                f"FAIL even==odd: intt_bf({val}, {val}, {zeta}) = ({re}, {ro}), "
                f"expected odd_out=0"
            )
            errors += 1

    # Property 3: even_out = (even + odd) mod q (always true regardless of zeta)
    for _ in range(1000):
        even = rng.randint(0, KYBER_Q - 1)
        odd = rng.randint(0, KYBER_Q - 1)
        zeta = rng.randint(0, KYBER_Q - 1)
        re, ro, ee, eo, match = await drive_and_check(dut, even, odd, zeta)
        expected_sum = (even + odd) % KYBER_Q
        if re != expected_sum:
            dut._log.error(
                f"FAIL sum: intt_bf({even}, {odd}, {zeta}): "
                f"even_out={re}, expected {expected_sum}"
            )
            errors += 1

    assert errors == 0, f"Algebraic property test: {errors} errors"
    dut._log.info("PASS: All algebraic property tests verified")


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
                f"FAIL: intt_bf({even}, {odd}, {zeta}) = ({re}, {ro}), "
                f"expected ({ee}, {eo})"
            )
            errors += 1

    assert errors == 0, f"Random test: {errors} errors out of {n_samples}"
    dut._log.info(f"PASS: {n_samples} random triples verified")
