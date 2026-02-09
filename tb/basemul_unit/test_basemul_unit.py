"""Testbench for basemul_unit module.

Tests the 2x2 basemul in Z_q[X]/(X^2 - zeta):
    c0 = (a0*b0 + a1*b1*zeta) mod q
    c1 = (a0*b1 + a1*b0)      mod q

Covers boundary values, algebraic properties, and random quintuplets.
"""

import sys
import os
import random

import cocotb
from cocotb.triggers import Timer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, basemul


async def drive_and_check(dut, a0, a1, b0, b1, zeta):
    """Drive inputs, wait for combinational settle, check outputs."""
    exp_c0, exp_c1 = basemul(a0, a1, b0, b1, zeta)
    dut.a0.value = a0
    dut.a1.value = a1
    dut.b0.value = b0
    dut.b1.value = b1
    dut.zeta.value = zeta
    await Timer(1, unit='ns')
    result_c0 = dut.c0.value.to_unsigned()
    result_c1 = dut.c1.value.to_unsigned()
    match = (result_c0 == exp_c0) and (result_c1 == exp_c1)
    return result_c0, result_c1, exp_c0, exp_c1, match


@cocotb.test()
async def test_boundary_values(dut):
    """Test specific boundary and corner-case quintuplets."""
    Q = KYBER_Q
    cases = [
        # (a0, a1, b0, b1, zeta) — description
        (0, 0, 0, 0, 0),                          # all zeros
        (1, 0, 1, 0, 0),                          # 1*1 = 1, a1=b1=0
        (0, 0, 0, 0, Q - 1),                      # zeros with max zeta
        (1, 1, 1, 1, 1),                          # simple: c0=1+1=2, c1=1+1=2
        (Q - 1, Q - 1, Q - 1, Q - 1, Q - 1),     # all max
        (Q - 1, 0, Q - 1, 0, 0),                  # c0 = (Q-1)^2 mod q
        (0, Q - 1, 0, Q - 1, Q - 1),              # c0 = (Q-1)^2 * (Q-1) mod q
        (1, 0, 0, 1, 0),                          # c0=0, c1=1
        (0, 1, 1, 0, 0),                          # c0=0, c1=1
        (Q // 2, Q // 2, Q // 2, Q // 2, 2),      # midpoints
        (1, 1, 1, 0, 0),                          # c0=1, c1=1
        (0, 1, 0, 1, 1),                          # c0 = 0+1*1=1, c1=0+0=0
    ]
    errors = 0
    for a0, a1, b0, b1, zeta in cases:
        rc0, rc1, ec0, ec1, match = await drive_and_check(dut, a0, a1, b0, b1, zeta)
        if not match:
            dut._log.error(
                f"FAIL: basemul({a0},{a1},{b0},{b1},{zeta}) = ({rc0},{rc1}), "
                f"expected ({ec0},{ec1})"
            )
            errors += 1
        else:
            dut._log.info(
                f"  basemul({a0},{a1},{b0},{b1},{zeta}) = ({ec0},{ec1})"
            )

    assert errors == 0, f"Boundary test: {errors} errors out of {len(cases)}"
    dut._log.info(f"PASS: All {len(cases)} boundary cases verified")


@cocotb.test()
async def test_algebraic_properties(dut):
    """Test algebraic properties that must hold for correctness."""
    rng = random.Random(123)
    errors = 0

    # Property 1: zeta=0 → c0 = a0*b0 mod q, c1 = a0*b1 + a1*b0 mod q
    # (The a1*b1*zeta term vanishes)
    for _ in range(200):
        a0 = rng.randint(0, KYBER_Q - 1)
        a1 = rng.randint(0, KYBER_Q - 1)
        b0 = rng.randint(0, KYBER_Q - 1)
        b1 = rng.randint(0, KYBER_Q - 1)
        rc0, rc1, ec0, ec1, match = await drive_and_check(dut, a0, a1, b0, b1, 0)
        expected_c0 = (a0 * b0) % KYBER_Q
        expected_c1 = (a0 * b1 + a1 * b0) % KYBER_Q
        if rc0 != expected_c0 or rc1 != expected_c1:
            dut._log.error(
                f"FAIL zeta=0: basemul({a0},{a1},{b0},{b1},0) = ({rc0},{rc1}), "
                f"expected ({expected_c0},{expected_c1})"
            )
            errors += 1

    # Property 2: multiply by (1, 0) → identity
    # (1 + 0*X) * (b0 + b1*X) = (b0 + b1*X)
    for _ in range(200):
        b0 = rng.randint(0, KYBER_Q - 1)
        b1 = rng.randint(0, KYBER_Q - 1)
        zeta = rng.randint(0, KYBER_Q - 1)
        rc0, rc1, ec0, ec1, match = await drive_and_check(dut, 1, 0, b0, b1, zeta)
        if rc0 != b0 or rc1 != b1:
            dut._log.error(
                f"FAIL identity: basemul(1,0,{b0},{b1},{zeta}) = ({rc0},{rc1}), "
                f"expected ({b0},{b1})"
            )
            errors += 1

    # Property 3: multiply by (0, 0) → zero
    for _ in range(100):
        a0 = rng.randint(0, KYBER_Q - 1)
        a1 = rng.randint(0, KYBER_Q - 1)
        zeta = rng.randint(0, KYBER_Q - 1)
        rc0, rc1, ec0, ec1, match = await drive_and_check(dut, a0, a1, 0, 0, zeta)
        if rc0 != 0 or rc1 != 0:
            dut._log.error(
                f"FAIL zero: basemul({a0},{a1},0,0,{zeta}) = ({rc0},{rc1}), "
                f"expected (0,0)"
            )
            errors += 1

    assert errors == 0, f"Algebraic property test: {errors} errors"
    dut._log.info("PASS: All algebraic property tests verified (zeta=0, identity, zero)")


@cocotb.test()
async def test_random_quintuplets(dut):
    """100k random (a0, a1, b0, b1, zeta) quintuplets."""
    rng = random.Random(42)
    n_samples = 100_000
    errors = 0

    for _ in range(n_samples):
        a0 = rng.randint(0, KYBER_Q - 1)
        a1 = rng.randint(0, KYBER_Q - 1)
        b0 = rng.randint(0, KYBER_Q - 1)
        b1 = rng.randint(0, KYBER_Q - 1)
        zeta = rng.randint(0, KYBER_Q - 1)
        rc0, rc1, ec0, ec1, match = await drive_and_check(dut, a0, a1, b0, b1, zeta)
        if not match:
            dut._log.error(
                f"FAIL: basemul({a0},{a1},{b0},{b1},{zeta}) = ({rc0},{rc1}), "
                f"expected ({ec0},{ec1})"
            )
            errors += 1

    assert errors == 0, f"Random test: {errors} errors out of {n_samples}"
    dut._log.info(f"PASS: {n_samples} random quintuplets verified")
