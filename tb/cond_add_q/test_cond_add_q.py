"""Exhaustive testbench for cond_add_q module.

Tests all 8192 possible 13-bit inputs against the Python oracle.
Also verifies specific (a, b) subtraction pairs and random pairs.
"""

import sys
import os
import random

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, cond_add_q, mod_q


async def drive_and_check(dut, a, expected):
    """Drive input a, wait for combinational propagation, check result."""
    dut.a.value = a
    await Timer(1, unit='ns')
    result = dut.result.value.to_unsigned()
    assert result == expected, (
        f"FAIL: cond_add_q({a}) = {result}, expected {expected}"
    )


@cocotb.test()
async def test_exhaustive_all_13bit(dut):
    """Test all 8192 possible 13-bit inputs."""
    errors = 0
    for a in range(2**13):
        expected = cond_add_q(a)
        dut.a.value = a
        await Timer(1, unit='ns')
        result = dut.result.value.to_unsigned()
        if result != expected:
            dut._log.error(f"FAIL: cond_add_q({a}) = {result}, expected {expected}")
            errors += 1

    assert errors == 0, f"Exhaustive test failed with {errors} errors out of {2**13} inputs"
    dut._log.info(f"PASS: All {2**13} inputs verified correctly")


@cocotb.test()
async def test_subtraction_boundary_pairs(dut):
    """Test specific (a, b) pairs that exercise boundary conditions.

    We compute diff = {1'b0,a} - {1'b0,b} and verify the hardware
    produces (a - b) mod q.
    """
    pairs = [
        (0, 0),                     # 0 - 0 = 0
        (0, 1),                     # underflow: should give q-1
        (1, 0),                     # no underflow: 1
        (KYBER_Q - 1, 0),           # 3328 - 0 = 3328
        (0, KYBER_Q - 1),           # underflow: should give 1
        (KYBER_Q - 1, KYBER_Q - 1), # 0
        (1, KYBER_Q - 1),           # underflow: should give 2
        (KYBER_Q - 1, 1),           # 3327
        (KYBER_Q // 2, KYBER_Q // 2),         # 0
        (KYBER_Q // 2, KYBER_Q // 2 + 1),     # underflow: q-1
    ]
    for a, b in pairs:
        # Simulate 13-bit unsigned subtraction
        diff = ((1 << 13) + a - b) if a < b else (a - b)
        diff &= 0x1FFF
        expected = (a - b) % KYBER_Q
        await drive_and_check(dut, diff, expected)
        dut._log.info(f"  ({a} - {b}): diff=0x{diff:04x}, result={expected}")

    dut._log.info(f"PASS: All {len(pairs)} boundary pairs verified")


@cocotb.test()
async def test_random_subtraction_pairs(dut):
    """100k random (a, b) pairs, checking (a - b) mod q."""
    rng = random.Random(42)
    n_samples = 100_000
    errors = 0

    for _ in range(n_samples):
        a = rng.randint(0, KYBER_Q - 1)
        b = rng.randint(0, KYBER_Q - 1)
        diff = ((1 << 13) + a - b) if a < b else (a - b)
        diff &= 0x1FFF
        expected = (a - b) % KYBER_Q
        dut.a.value = diff
        await Timer(1, unit='ns')
        result = dut.result.value.to_unsigned()
        if result != expected:
            dut._log.error(
                f"FAIL: ({a} - {b}): cond_add_q(0x{diff:04x}) = {result}, expected {expected}"
            )
            errors += 1

    assert errors == 0, f"Random test: {errors} errors out of {n_samples}"
    dut._log.info(f"PASS: {n_samples} random subtraction pairs verified")
