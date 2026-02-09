"""Testbench for poly_addsub module.

Tests coefficient-wise polynomial add/sub with mode selection:
  1. Random add: 3 random poly pairs, mode=0 vs poly_add oracle
  2. Random sub: 3 random poly pairs, mode=1 vs poly_sub oracle
  3. Known vectors: zero identity, self-subtraction, boundary (q-1 + q-1)
  4. Roundtrip: add(sub(a, b), b) == a (verifies in-place write correctness)
  5. Timing: verify exactly 258 cycles for both modes
  6. Commutativity: add(a, b) == add(b, a) (swap RAMs, same result)
"""

import sys
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, KYBER_N, poly_add, poly_sub


CLK_PERIOD_NS = 10


async def init(dut):
    """Start clock, assert reset, release."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.mode.value = 0
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


async def run_addsub(dut, mode):
    """Start add/sub operation and wait for done. Returns cycle count."""
    dut.mode.value = mode
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
            raise RuntimeError("Timeout: poly_addsub did not assert done within 1000 cycles")

    return cycles


@cocotb.test()
async def test_random_add(dut):
    """Random polynomial pairs, add mode vs poly_add oracle."""
    await init(dut)

    rng = random.Random(42)

    for t in range(3):
        a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        expected = poly_add(a, b)

        await load_poly_a(dut, a)
        await load_poly_b(dut, b)
        await run_addsub(dut, mode=0)
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

        assert errors == 0, f"Random add test {t}: {errors} mismatches"
        dut._log.info(f"  Random add test {t}: PASS")

    dut._log.info("PASS: 3 random polynomial add tests verified")


@cocotb.test()
async def test_random_sub(dut):
    """Random polynomial pairs, sub mode vs poly_sub oracle."""
    await init(dut)

    rng = random.Random(77)

    for t in range(3):
        a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        expected = poly_sub(a, b)

        await load_poly_a(dut, a)
        await load_poly_b(dut, b)
        await run_addsub(dut, mode=1)
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

        assert errors == 0, f"Random sub test {t}: {errors} mismatches"
        dut._log.info(f"  Random sub test {t}: PASS")

    dut._log.info("PASS: 3 random polynomial sub tests verified")


@cocotb.test()
async def test_known_vectors(dut):
    """Test known input patterns."""
    await init(dut)

    # a + 0 = a (additive identity)
    rng = random.Random(1)
    a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    b = [0] * KYBER_N
    await load_poly_a(dut, a)
    await load_poly_b(dut, b)
    await run_addsub(dut, mode=0)
    result = await read_poly_a(dut)
    assert result == a, "a + 0 should equal a"
    dut._log.info("  a + 0 = a: PASS")

    # a - a = 0 (self-subtraction)
    a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    await load_poly_a(dut, a)
    await load_poly_b(dut, a)
    await run_addsub(dut, mode=1)
    result = await read_poly_a(dut)
    assert result == [0] * KYBER_N, "a - a should be all zeros"
    dut._log.info("  a - a = 0: PASS")

    # Boundary: (q-1) + (q-1) = 2q-2 mod q = q-2 = 3327
    a = [KYBER_Q - 1] * KYBER_N
    b = [KYBER_Q - 1] * KYBER_N
    expected = poly_add(a, b)
    await load_poly_a(dut, a)
    await load_poly_b(dut, b)
    await run_addsub(dut, mode=0)
    result = await read_poly_a(dut)
    assert result == expected, f"(q-1)+(q-1) should be {expected[0]} for all coeffs"
    dut._log.info(f"  (q-1)+(q-1) = {expected[0]}: PASS")

    dut._log.info("PASS: Known vector tests verified")


@cocotb.test()
async def test_roundtrip(dut):
    """add(sub(a, b), b) == a — exercises module twice, verifies in-place correctness."""
    await init(dut)

    rng = random.Random(99)
    a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    # Step 1: sub(a, b) → result in RAM A
    await load_poly_a(dut, a)
    await load_poly_b(dut, b)
    await run_addsub(dut, mode=1)

    # Step 2: add(result, b) → should recover a
    # RAM A already has sub result; reload b into RAM B
    await load_poly_b(dut, b)
    await run_addsub(dut, mode=0)
    result = await read_poly_a(dut)

    errors = 0
    for i in range(KYBER_N):
        if result[i] != a[i]:
            dut._log.error(f"FAIL: recovered[{i}] = {result[i]}, expected {a[i]}")
            errors += 1
            if errors >= 10:
                break

    assert errors == 0, f"Round-trip test: {errors} mismatches"
    dut._log.info("PASS: Round-trip add(sub(a, b), b) == a verified")


@cocotb.test()
async def test_timing(dut):
    """Verify expected cycle count: 258 cycles for both modes."""
    await init(dut)

    rng = random.Random(55)
    a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    # Test add mode timing
    await load_poly_a(dut, a)
    await load_poly_b(dut, b)
    cycles_add = await run_addsub(dut, mode=0)
    dut._log.info(f"  Add mode: {cycles_add} cycles")

    # Test sub mode timing
    await load_poly_a(dut, a)
    await load_poly_b(dut, b)
    cycles_sub = await run_addsub(dut, mode=1)
    dut._log.info(f"  Sub mode: {cycles_sub} cycles")

    # 1 PRIME + 256 RUN + 1 DONE = 258
    expected_cycles = 258
    assert cycles_add == expected_cycles, \
        f"Add: expected {expected_cycles} cycles, got {cycles_add}"
    assert cycles_sub == expected_cycles, \
        f"Sub: expected {expected_cycles} cycles, got {cycles_sub}"

    dut._log.info(f"PASS: Timing verified ({expected_cycles} cycles for both modes)")


@cocotb.test()
async def test_commutativity(dut):
    """add(a, b) == add(b, a) — swap RAM contents, same result."""
    await init(dut)

    rng = random.Random(123)
    a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    # Compute add(a, b)
    await load_poly_a(dut, a)
    await load_poly_b(dut, b)
    await run_addsub(dut, mode=0)
    result_ab = await read_poly_a(dut)

    # Compute add(b, a) — swap
    await load_poly_a(dut, b)
    await load_poly_b(dut, a)
    await run_addsub(dut, mode=0)
    result_ba = await read_poly_a(dut)

    assert result_ab == result_ba, "add(a, b) should equal add(b, a)"
    dut._log.info("PASS: Commutativity add(a, b) == add(b, a) verified")
