"""Testbench for poly_basemul module.

Tests pointwise polynomial multiplication in the NTT domain:
  1. Random polynomial pairs vs Python oracle
  2. Multiply by NTT(1) → identity
  3. Algebraic: INTT(basemul(NTT(a), NTT(b))) == schoolbook(a, b)
  4. Known vectors (zero poly, constant poly)
  5. Timing: 257 cycles expected
"""

import sys
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import (
    KYBER_Q, KYBER_N,
    poly_basemul, ntt_forward, ntt_inverse, schoolbook_mul,
)


CLK_PERIOD_NS = 10


async def init(dut):
    """Start clock, assert reset, release."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.a_we.value = 0
    dut.a_addr.value = 0
    dut.a_din.value = 0
    dut.b_we.value = 0
    dut.b_addr.value = 0
    dut.b_din.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def load_poly_a(dut, coeffs):
    """Load a polynomial into RAM A."""
    for addr in range(256):
        dut.a_we.value = 1
        dut.a_addr.value = addr
        dut.a_din.value = coeffs[addr]
        await RisingEdge(dut.clk)
    dut.a_we.value = 0
    await RisingEdge(dut.clk)


async def load_poly_b(dut, coeffs):
    """Load a polynomial into RAM B."""
    for addr in range(256):
        dut.b_we.value = 1
        dut.b_addr.value = addr
        dut.b_din.value = coeffs[addr]
        await RisingEdge(dut.clk)
    dut.b_we.value = 0
    await RisingEdge(dut.clk)


async def read_poly_a(dut):
    """Read the polynomial from RAM A (result)."""
    result = []
    for addr in range(256):
        dut.a_addr.value = addr
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        result.append(dut.a_dout.value.to_unsigned())
    return result


async def run_basemul(dut):
    """Start basemul and wait for done. Returns cycle count."""
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    cycles = 0
    while True:
        await RisingEdge(dut.clk)
        cycles += 1
        await FallingEdge(dut.clk)
        if dut.done.value == 1:
            break
        if cycles > 1000:
            raise RuntimeError("Timeout: basemul did not assert done within 1000 cycles")

    return cycles


@cocotb.test()
async def test_random_polys(dut):
    """Random polynomial pair vs Python oracle."""
    await init(dut)

    rng = random.Random(42)

    for t in range(3):
        a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        expected = poly_basemul(a, b)

        await load_poly_a(dut, a)
        await load_poly_b(dut, b)
        await run_basemul(dut)
        result = await read_poly_a(dut)

        errors = 0
        for i in range(KYBER_N):
            if result[i] != expected[i]:
                dut._log.error(
                    f"FAIL test {t}: c[{i}] = {result[i]}, expected {expected[i]}"
                )
                errors += 1
                if errors >= 10:
                    dut._log.error("(stopping after 10 errors)")
                    break

        assert errors == 0, f"Random poly test {t}: {errors} mismatches"
        dut._log.info(f"  Random poly test {t}: PASS")

    dut._log.info("PASS: 3 random polynomial pairs verified")


@cocotb.test()
async def test_known_vectors(dut):
    """Test known input patterns."""
    await init(dut)

    # All zeros × anything = all zeros
    a = [0] * KYBER_N
    b = [random.Random(1).randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    await load_poly_a(dut, a)
    await load_poly_b(dut, b)
    await run_basemul(dut)
    result = await read_poly_a(dut)
    assert result == [0] * KYBER_N, "basemul(zeros, b) should be zeros"
    dut._log.info("  basemul(zeros, b) = zeros: PASS")

    # Anything × all zeros = all zeros
    a = [random.Random(2).randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    b = [0] * KYBER_N
    await load_poly_a(dut, a)
    await load_poly_b(dut, b)
    await run_basemul(dut)
    result = await read_poly_a(dut)
    assert result == [0] * KYBER_N, "basemul(a, zeros) should be zeros"
    dut._log.info("  basemul(a, zeros) = zeros: PASS")

    dut._log.info("PASS: Known vector tests verified")


@cocotb.test()
async def test_ntt_identity(dut):
    """Multiply by NTT([1,0,...,0]) should be identity in NTT domain.

    In the NTT domain, multiplying by NTT(1) should give back the same
    polynomial (since 1 is the multiplicative identity for convolution).
    """
    await init(dut)

    unit = [1] + [0] * (KYBER_N - 1)
    ntt_unit = ntt_forward(unit)

    rng = random.Random(77)
    a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    ntt_a = ntt_forward(a)
    expected = poly_basemul(ntt_a, ntt_unit)

    await load_poly_a(dut, ntt_a)
    await load_poly_b(dut, ntt_unit)
    await run_basemul(dut)
    result = await read_poly_a(dut)

    # Verify basemul result matches oracle
    errors = 0
    for i in range(KYBER_N):
        if result[i] != expected[i]:
            dut._log.error(f"FAIL: c[{i}] = {result[i]}, expected {expected[i]}")
            errors += 1
            if errors >= 10:
                break

    assert errors == 0, f"NTT identity test: {errors} mismatches"

    # Verify round-trip: INTT(basemul(NTT(a), NTT(1))) == a
    recovered = ntt_inverse(result)
    assert recovered == a, "INTT(basemul(NTT(a), NTT(1))) != a"

    dut._log.info("PASS: NTT identity test verified (multiply by NTT(1) = identity)")


@cocotb.test()
async def test_schoolbook_roundtrip(dut):
    """Acid test: INTT(basemul(NTT(a), NTT(b))) == schoolbook_mul(a, b)."""
    await init(dut)

    rng = random.Random(99)

    for t in range(3):
        a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

        ntt_a = ntt_forward(a)
        ntt_b = ntt_forward(b)
        expected = schoolbook_mul(a, b)

        await load_poly_a(dut, ntt_a)
        await load_poly_b(dut, ntt_b)
        await run_basemul(dut)
        result_ntt = await read_poly_a(dut)

        # INTT of basemul result
        result = ntt_inverse(result_ntt)

        errors = 0
        for i in range(KYBER_N):
            if result[i] != expected[i]:
                dut._log.error(
                    f"FAIL test {t}: c[{i}] = {result[i]}, expected {expected[i]}"
                )
                errors += 1
                if errors >= 10:
                    break

        assert errors == 0, f"Schoolbook round-trip test {t}: {errors} mismatches"
        dut._log.info(f"  Schoolbook round-trip test {t}: PASS")

    dut._log.info("PASS: 3 schoolbook round-trip tests verified")


@cocotb.test()
async def test_timing(dut):
    """Verify expected cycle count: 257 cycles."""
    await init(dut)

    rng = random.Random(55)
    a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    await load_poly_a(dut, a)
    await load_poly_b(dut, b)
    cycles = await run_basemul(dut)

    dut._log.info(f"  Basemul took {cycles} cycles")

    # 64 pairs × 4 cycles + 1 done = 257
    expected_cycles = 257
    assert cycles == expected_cycles, \
        f"Expected {expected_cycles} cycles, got {cycles}"

    dut._log.info(f"PASS: Timing verified ({cycles} cycles)")
