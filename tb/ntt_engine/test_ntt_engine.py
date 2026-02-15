"""Testbench for ntt_engine module.

Tests the complete 7-layer NTT/INTT engine:
  1. Forward NTT vs Python oracle
  2. Inverse NTT vs Python oracle
  3. Round-trip: INTT(NTT(poly)) == poly
  4. Known vectors: zero, unit, constant
  5. Timing: cycle count verification
"""

import sys
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, KYBER_N, ntt_forward, ntt_inverse


CLK_PERIOD_NS = 10
MODE_NTT  = 0
MODE_INTT = 1


async def init(dut):
    """Start clock, assert reset, release."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.mode.value = 0
    dut.ext_we.value = 0
    dut.ext_addr.value = 0
    dut.ext_din.value = 0
    # Hold reset for 2 cycles
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def load_poly(dut, coeffs):
    """Load a 256-element polynomial into the engine's RAM via ext port."""
    for addr in range(256):
        dut.ext_we.value = 1
        dut.ext_addr.value = addr
        dut.ext_din.value = coeffs[addr]
        await RisingEdge(dut.clk)
    dut.ext_we.value = 0
    await RisingEdge(dut.clk)


async def read_poly(dut):
    """Read the 256-element polynomial from the engine's RAM."""
    result = []
    for addr in range(256):
        dut.ext_addr.value = addr
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        result.append(dut.ext_dout.value.to_unsigned())
    return result


async def run_transform(dut, mode):
    """Start NTT or INTT and wait for done. Returns cycle count."""
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
        if cycles > 5000:
            raise RuntimeError("Timeout: engine did not assert done within 5000 cycles")

    return cycles


@cocotb.test()
async def test_forward_ntt(dut):
    """Forward NTT of a random polynomial, compare against Python oracle."""
    await init(dut)

    rng = random.Random(42)
    poly = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    expected = ntt_forward(poly)

    await load_poly(dut, poly)
    cycles = await run_transform(dut, MODE_NTT)
    result = await read_poly(dut)

    errors = 0
    for i in range(KYBER_N):
        if result[i] != expected[i]:
            dut._log.error(
                f"FAIL: NTT[{i}] = {result[i]}, expected {expected[i]}"
            )
            errors += 1
            if errors >= 10:
                dut._log.error("(stopping after 10 errors)")
                break

    assert errors == 0, f"Forward NTT: {errors} mismatches"
    dut._log.info(f"PASS: Forward NTT verified ({cycles} cycles)")


@cocotb.test()
async def test_inverse_ntt(dut):
    """Inverse NTT of a random NTT-domain polynomial, compare against Python oracle."""
    await init(dut)

    rng = random.Random(99)
    # Generate a polynomial in NTT domain (just random values in [0, q-1])
    ntt_poly = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    expected = ntt_inverse(ntt_poly)

    await load_poly(dut, ntt_poly)
    cycles = await run_transform(dut, MODE_INTT)
    result = await read_poly(dut)

    errors = 0
    for i in range(KYBER_N):
        if result[i] != expected[i]:
            dut._log.error(
                f"FAIL: INTT[{i}] = {result[i]}, expected {expected[i]}"
            )
            errors += 1
            if errors >= 10:
                dut._log.error("(stopping after 10 errors)")
                break

    assert errors == 0, f"Inverse NTT: {errors} mismatches"
    dut._log.info(f"PASS: Inverse NTT verified ({cycles} cycles)")


@cocotb.test()
async def test_round_trip(dut):
    """Acid test: INTT(NTT(poly)) == poly for multiple random polynomials."""
    await init(dut)

    rng = random.Random(77)
    n_tests = 5
    errors = 0

    for t in range(n_tests):
        poly = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

        await load_poly(dut, poly)
        await run_transform(dut, MODE_NTT)
        await run_transform(dut, MODE_INTT)
        result = await read_poly(dut)

        if result != poly:
            mismatches = sum(1 for i in range(KYBER_N) if result[i] != poly[i])
            dut._log.error(
                f"FAIL: Round-trip test {t}: {mismatches}/256 mismatches"
            )
            for i in range(KYBER_N):
                if result[i] != poly[i]:
                    dut._log.error(f"  [{i}]: got {result[i]}, expected {poly[i]}")
                    break
            errors += 1
        else:
            dut._log.info(f"  Round-trip test {t}: PASS")

    assert errors == 0, f"Round-trip: {errors}/{n_tests} failures"
    dut._log.info(f"PASS: {n_tests} round-trip tests verified")


@cocotb.test()
async def test_known_vectors(dut):
    """Test known input patterns."""
    await init(dut)

    # All zeros → should stay all zeros
    poly = [0] * KYBER_N
    await load_poly(dut, poly)
    await run_transform(dut, MODE_NTT)
    result = await read_poly(dut)
    assert result == poly, "NTT of zero polynomial should be zero"
    dut._log.info("  NTT(zeros) = zeros: PASS")

    # INTT of zero → zero
    await run_transform(dut, MODE_INTT)
    result = await read_poly(dut)
    assert result == poly, "INTT of zero polynomial should be zero"
    dut._log.info("  INTT(zeros) = zeros: PASS")

    # Unit polynomial [1, 0, 0, ...]
    poly = [1] + [0] * (KYBER_N - 1)
    expected_ntt = ntt_forward(poly)
    await load_poly(dut, poly)
    await run_transform(dut, MODE_NTT)
    result = await read_poly(dut)
    assert result == expected_ntt, "NTT of unit polynomial mismatch"
    dut._log.info("  NTT([1,0,...]) verified: PASS")

    # Round-trip the unit polynomial
    await run_transform(dut, MODE_INTT)
    result = await read_poly(dut)
    assert result == poly, "Round-trip of unit polynomial failed"
    dut._log.info("  INTT(NTT([1,0,...])) = [1,0,...]: PASS")

    # Constant polynomial [42, 42, ...]
    poly = [42] * KYBER_N
    await load_poly(dut, poly)
    await run_transform(dut, MODE_NTT)
    await run_transform(dut, MODE_INTT)
    result = await read_poly(dut)
    assert result == poly, "Round-trip of constant polynomial failed"
    dut._log.info("  Round-trip([42]*256) = [42]*256: PASS")

    dut._log.info("PASS: All known vector tests verified")


@cocotb.test()
async def test_timing(dut):
    """Verify expected cycle counts for ping-pong NTT engine."""
    await init(dut)

    # Load a simple polynomial
    poly = [i % KYBER_Q for i in range(KYBER_N)]
    await load_poly(dut, poly)

    # Forward NTT timing (ping-pong: 1 butterfly/cycle after prime)
    # 7 layers × (1 INIT + 1 PRIME + 127 OVERLAP + 1 FLUSH) + 1 DONE
    # = 7 × 130 + 1 = 911
    EXPECTED_NTT = 911
    ntt_cycles = await run_transform(dut, MODE_NTT)
    dut._log.info(f"  Forward NTT: {ntt_cycles} cycles (expected {EXPECTED_NTT})")
    assert ntt_cycles == EXPECTED_NTT, \
        f"NTT cycle count {ntt_cycles} != expected {EXPECTED_NTT}"

    # Reload for INTT
    await load_poly(dut, poly)

    # Inverse NTT timing (butterfly layers + dual-port scaling)
    # 7 × 130 + SCALE_INIT(1) + 128×(READ+WRITE) + DONE(1)
    # = 910 + 1 + 256 + 1 = 1168
    EXPECTED_INTT = 1168
    intt_cycles = await run_transform(dut, MODE_INTT)
    dut._log.info(f"  Inverse NTT: {intt_cycles} cycles (expected {EXPECTED_INTT})")
    assert intt_cycles == EXPECTED_INTT, \
        f"INTT cycle count {intt_cycles} != expected {EXPECTED_INTT}"

    dut._log.info(f"PASS: Timing verified (NTT={ntt_cycles}, INTT={intt_cycles})")
