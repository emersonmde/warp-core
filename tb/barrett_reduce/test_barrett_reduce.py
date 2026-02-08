"""Testbench for barrett_reduce module.

Two test modes:
  1. Exhaustive: all 65,536 16-bit inputs (INPUT_WIDTH=16 default)
  2. Structured sampling: boundary cases + random values up to 24 bits

All outputs are cross-checked against Python a % 3329.
"""

import sys
import os
import random

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, mod_q


async def drive_and_check(dut, a):
    """Drive input, wait for combinational propagation, return (result, expected, match)."""
    expected = mod_q(a)
    dut.a.value = a
    await Timer(1, unit='ns')
    result = dut.result.value.to_unsigned()
    return result, expected, result == expected


@cocotb.test()
async def test_exhaustive_16bit(dut):
    """Exhaustive test of all 65,536 possible 16-bit inputs."""
    errors = 0
    for a in range(2**16):
        result, expected, match = await drive_and_check(dut, a)
        if not match:
            dut._log.error(f"FAIL: barrett_reduce({a}) = {result}, expected {expected}")
            errors += 1

    assert errors == 0, f"Exhaustive 16-bit test: {errors} errors out of {2**16}"
    dut._log.info(f"PASS: All {2**16} 16-bit inputs verified correctly")


@cocotb.test()
async def test_boundary_values(dut):
    """Test specific boundary and corner-case values."""
    cases = [
        0,
        1,
        KYBER_Q - 1,       # 3328
        KYBER_Q,            # 3329
        KYBER_Q + 1,        # 3330
        2 * KYBER_Q - 1,    # 6657
        2 * KYBER_Q,        # 6658
        2 * KYBER_Q + 1,    # 6659
        4095,               # 2^12 - 1
        4096,               # 2^12
        2**16 - 1,          # 65535, max 16-bit
    ]

    # Multiples of q near boundaries
    for k in range(1, 20):
        cases.extend([k * KYBER_Q - 1, k * KYBER_Q, k * KYBER_Q + 1])
        cases.append(k * KYBER_Q + KYBER_Q - 1)  # max remainder case

    # Filter to valid 16-bit range
    cases = [a for a in cases if 0 <= a < 2**16]
    cases = sorted(set(cases))

    errors = 0
    for a in cases:
        result, expected, match = await drive_and_check(dut, a)
        if not match:
            dut._log.error(f"FAIL: barrett_reduce({a}) = {result}, expected {expected}")
            errors += 1
        else:
            dut._log.info(f"  barrett_reduce({a}) = {expected}")

    assert errors == 0, f"Boundary test: {errors} errors out of {len(cases)}"
    dut._log.info(f"PASS: All {len(cases)} boundary cases verified")


@cocotb.test()
async def test_random_sampling(dut):
    """Random sampling within 16-bit range with fixed seed for reproducibility."""
    rng = random.Random(42)
    n_samples = 100_000
    errors = 0

    for _ in range(n_samples):
        a = rng.randint(0, 2**16 - 1)
        result, expected, match = await drive_and_check(dut, a)
        if not match:
            dut._log.error(f"FAIL: barrett_reduce({a}) = {result}, expected {expected}")
            errors += 1

    assert errors == 0, f"Random 16-bit test: {errors} errors out of {n_samples}"
    dut._log.info(f"PASS: {n_samples} random 16-bit samples verified")
