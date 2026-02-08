"""Testbench for mod_sub module.

Tests modular subtraction (a - b) mod 3329 with exhaustive slices,
boundary values, and random sampling.
"""

import sys
import os
import random

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, mod_q


async def drive_and_check(dut, a, b):
    """Drive inputs, wait for combinational propagation, return (result, expected, match)."""
    expected = mod_q(a - b)
    dut.a.value = a
    dut.b.value = b
    await Timer(1, unit='ns')
    result = dut.result.value.to_unsigned()
    return result, expected, result == expected


@cocotb.test()
async def test_exhaustive_slices(dut):
    """Exhaustive sweep along axes and diagonal.

    - a varies [0, q-1] with b=0
    - b varies [0, q-1] with a=0
    - diagonal a=b for all [0, q-1]
    """
    errors = 0

    # a varies, b=0
    for a in range(KYBER_Q):
        result, expected, match = await drive_and_check(dut, a, 0)
        if not match:
            dut._log.error(f"FAIL: mod_sub({a}, 0) = {result}, expected {expected}")
            errors += 1

    # b varies, a=0
    for b in range(KYBER_Q):
        result, expected, match = await drive_and_check(dut, 0, b)
        if not match:
            dut._log.error(f"FAIL: mod_sub(0, {b}) = {result}, expected {expected}")
            errors += 1

    # diagonal a=b (should always be 0)
    for a in range(KYBER_Q):
        result, expected, match = await drive_and_check(dut, a, a)
        if not match:
            dut._log.error(f"FAIL: mod_sub({a}, {a}) = {result}, expected {expected}")
            errors += 1

    total = 3 * KYBER_Q
    assert errors == 0, f"Exhaustive slices: {errors} errors out of {total}"
    dut._log.info(f"PASS: All {total} exhaustive slice tests verified")


@cocotb.test()
async def test_boundary_values(dut):
    """Test specific boundary and corner-case pairs."""
    cases = [
        (0, 0),
        (1, 0),
        (0, 1),                         # underflow: 0 - 1 = 3328
        (KYBER_Q - 1, 0),               # 3328 - 0 = 3328
        (0, KYBER_Q - 1),               # underflow: 0 - 3328 = 1
        (KYBER_Q - 1, KYBER_Q - 1),     # 0
        (KYBER_Q - 1, 1),               # 3327
        (1, KYBER_Q - 1),               # underflow: 1 - 3328 = 2
        (KYBER_Q // 2, KYBER_Q // 2),   # 0
        (KYBER_Q // 2 + 1, KYBER_Q // 2),  # 1
    ]
    errors = 0
    for a, b in cases:
        result, expected, match = await drive_and_check(dut, a, b)
        if not match:
            dut._log.error(f"FAIL: mod_sub({a}, {b}) = {result}, expected {expected}")
            errors += 1
        else:
            dut._log.info(f"  mod_sub({a}, {b}) = {expected}")

    assert errors == 0, f"Boundary test: {errors} errors out of {len(cases)}"
    dut._log.info(f"PASS: All {len(cases)} boundary cases verified")


@cocotb.test()
async def test_random_pairs(dut):
    """100k random (a, b) pairs in [0, q-1]."""
    rng = random.Random(42)
    n_samples = 100_000
    errors = 0

    for _ in range(n_samples):
        a = rng.randint(0, KYBER_Q - 1)
        b = rng.randint(0, KYBER_Q - 1)
        result, expected, match = await drive_and_check(dut, a, b)
        if not match:
            dut._log.error(f"FAIL: mod_sub({a}, {b}) = {result}, expected {expected}")
            errors += 1

    assert errors == 0, f"Random test: {errors} errors out of {n_samples}"
    dut._log.info(f"PASS: {n_samples} random pairs verified")
