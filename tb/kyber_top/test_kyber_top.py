"""Testbench for kyber_top module.

Tests 20-slot polynomial RAM bank with host I/O (6 existing tests)
plus micro-op command interface (10 new tests):
  7.  NTT forward — verify against ntt_forward() oracle
  8.  NTT/INTT round-trip — recover original polynomial
  9.  Basemul — verify against poly_basemul() oracle
  10. Poly add — verify against poly_add() oracle
  11. Poly sub — verify against poly_sub() oracle
  12. Compress D=1 round-trip
  13. Compress D=4 round-trip
  14. Compress D=10 round-trip
  15. CBD sample — verify against cbd_sample_eta2() oracle
  16. Multi-op: NTT + basemul + INTT (schoolbook round-trip)
"""

import sys
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge

# Add ref/ to path for oracle functions
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import (
    KYBER_Q, KYBER_N,
    ntt_forward, ntt_inverse, poly_basemul as oracle_basemul,
    poly_add as oracle_add, poly_sub as oracle_sub,
    compress_q, decompress_q, cbd_sample_eta2, schoolbook_mul,
)

NUM_SLOTS = 20
CLK_PERIOD_NS = 10

# Opcodes (must match kyber_top.v localparams)
OP_NOP           = 0
OP_COPY_TO_NTT   = 1
OP_COPY_FROM_NTT = 2
OP_RUN_NTT       = 3
OP_COPY_TO_BM_A  = 4
OP_COPY_TO_BM_B  = 5
OP_COPY_FROM_BM  = 6
OP_RUN_BASEMUL   = 7
OP_POLY_ADD      = 8
OP_POLY_SUB      = 9
OP_COMPRESS      = 10
OP_DECOMPRESS    = 11
OP_CBD_SAMPLE    = 12


async def init(dut):
    """Start clock, assert reset, release."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst_n.value = 0
    dut.host_we.value = 0
    dut.host_slot.value = 0
    dut.host_addr.value = 0
    dut.host_din.value = 0
    dut.cmd_op.value = 0
    dut.cmd_slot_a.value = 0
    dut.cmd_slot_b.value = 0
    dut.cmd_param.value = 0
    dut.start.value = 0
    dut.cbd_byte_valid.value = 0
    dut.cbd_byte_data.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def write_poly(dut, slot, coeffs):
    """Write 256 coefficients to a slot via host interface."""
    for addr in range(KYBER_N):
        dut.host_we.value = 1
        dut.host_slot.value = slot
        dut.host_addr.value = addr
        dut.host_din.value = coeffs[addr]
        await RisingEdge(dut.clk)
    dut.host_we.value = 0
    await RisingEdge(dut.clk)


async def read_poly(dut, slot):
    """Read 256 coefficients from a slot (synchronous RAM: sample on FallingEdge)."""
    result = []
    for addr in range(KYBER_N):
        dut.host_slot.value = slot
        dut.host_addr.value = addr
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        result.append(dut.host_dout.value.to_unsigned())
    return result


async def write_coeff(dut, slot, addr, val):
    """Write a single coefficient."""
    dut.host_we.value = 1
    dut.host_slot.value = slot
    dut.host_addr.value = addr
    dut.host_din.value = val
    await RisingEdge(dut.clk)
    dut.host_we.value = 0
    await RisingEdge(dut.clk)


async def read_coeff(dut, slot, addr):
    """Read a single coefficient."""
    dut.host_slot.value = slot
    dut.host_addr.value = addr
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    return dut.host_dout.value.to_unsigned()


async def run_cmd(dut, op, slot_a=0, slot_b=0, param=0, timeout=50000):
    """Issue a micro-op command and wait for done."""
    dut.cmd_op.value = op
    dut.cmd_slot_a.value = slot_a
    dut.cmd_slot_b.value = slot_b
    dut.cmd_param.value = param
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    for _ in range(timeout):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        if dut.done.value == 1:
            return
    raise TimeoutError(f"Micro-op {op} did not complete within {timeout} cycles")


async def run_cbd_cmd(dut, slot, input_bytes, timeout=5000):
    """Issue CBD_SAMPLE command, feeding bytes via handshake."""
    dut.cmd_op.value = OP_CBD_SAMPLE
    dut.cmd_slot_a.value = slot
    dut.cmd_slot_b.value = 0
    dut.cmd_param.value = 0
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    # Feed 128 bytes via valid/ready handshake.
    # We check byte_ready at FallingEdge (pre-posedge setup): if ready=1 when
    # valid=1 and data is stable, the byte WILL be accepted on the next posedge.
    # This avoids the last-byte issue where the sampler transitions to S_DONE
    # on the accepting posedge, dropping byte_ready before we can sample it.
    byte_idx = 0
    cycle_count = 0
    while byte_idx < 128:
        dut.cbd_byte_valid.value = 1
        dut.cbd_byte_data.value = input_bytes[byte_idx]
        await FallingEdge(dut.clk)
        if dut.cbd_byte_ready.value == 1:
            # Byte will be accepted on next posedge
            byte_idx += 1
        await RisingEdge(dut.clk)
        cycle_count += 1
        if cycle_count > timeout:
            raise TimeoutError(f"CBD byte feed timed out at byte {byte_idx}")
    dut.cbd_byte_valid.value = 0

    # Wait for done (copy phase)
    for _ in range(timeout):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        if dut.done.value == 1:
            return
    raise TimeoutError("CBD_SAMPLE did not complete")


def compare_polys(got, expected, label, log, max_errors=10):
    """Compare two polynomials, return error count."""
    errors = 0
    for i in range(KYBER_N):
        if got[i] != expected[i]:
            log.error(f"FAIL {label}: coeff[{i}] = {got[i]}, expected {expected[i]}")
            errors += 1
            if errors >= max_errors:
                log.error("(stopping after 10 errors)")
                break
    return errors


# ═══════════════════════════════════════════════════════════════════
# Existing host I/O tests (6 tests, unchanged)
# ═══════════════════════════════════════════════════════════════════

@cocotb.test()
async def test_write_read_single_slot(dut):
    """Write random poly to slot 0, read back, verify."""
    await init(dut)

    rng = random.Random(42)
    poly = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    await write_poly(dut, 0, poly)
    result = await read_poly(dut, 0)

    errors = compare_polys(result, poly, "single_slot", dut._log)
    assert errors == 0, f"Single slot read/write: {errors} mismatches"
    dut._log.info("PASS: Write/read single slot verified (256 coefficients)")


@cocotb.test()
async def test_all_slots_independent(dut):
    """Write distinct random poly to each of 20 slots, read all back."""
    await init(dut)

    rng = random.Random(100)
    polys = []
    for s in range(NUM_SLOTS):
        p = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
        polys.append(p)
        await write_poly(dut, s, p)

    total_errors = 0
    for s in range(NUM_SLOTS):
        result = await read_poly(dut, s)
        for i in range(KYBER_N):
            if result[i] != polys[s][i]:
                dut._log.error(
                    f"FAIL: slot {s} coeff[{i}] = {result[i]}, expected {polys[s][i]}"
                )
                total_errors += 1
                if total_errors >= 10:
                    break
        if total_errors >= 10:
            dut._log.error("(stopping after 10 errors)")
            break

    assert total_errors == 0, f"All slots independent: {total_errors} mismatches"
    dut._log.info("PASS: All 20 slots written and read back independently")


@cocotb.test()
async def test_slot_isolation(dut):
    """Write to slot 5, verify slots 4 and 6 are unaffected."""
    await init(dut)

    rng = random.Random(200)

    # Write known values to slots 4, 5, 6
    poly4 = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    poly5 = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    poly6 = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    await write_poly(dut, 4, poly4)
    await write_poly(dut, 5, poly5)
    await write_poly(dut, 6, poly6)

    # Overwrite slot 5 with new data
    poly5_new = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    await write_poly(dut, 5, poly5_new)

    # Verify slots 4 and 6 are untouched
    result4 = await read_poly(dut, 4)
    result6 = await read_poly(dut, 6)
    result5 = await read_poly(dut, 5)

    assert result4 == poly4, "Slot 4 was corrupted by write to slot 5"
    assert result6 == poly6, "Slot 6 was corrupted by write to slot 5"
    assert result5 == poly5_new, "Slot 5 does not contain new data"

    dut._log.info("PASS: Slot isolation verified (slots 4, 6 unaffected by write to 5)")


@cocotb.test()
async def test_boundary_coefficients(dut):
    """Boundary values (0, 1, q-2, q-1) at boundary addresses (0, 1, 127, 128, 254, 255)."""
    await init(dut)

    values = [0, 1, KYBER_Q - 2, KYBER_Q - 1]
    addrs = [0, 1, 127, 128, 254, 255]

    errors = 0
    for val in values:
        for addr in addrs:
            await write_coeff(dut, 0, addr, val)
            got = await read_coeff(dut, 0, addr)
            if got != val:
                dut._log.error(
                    f"FAIL: addr={addr} val={val} readback={got}"
                )
                errors += 1

    assert errors == 0, f"Boundary test: {errors} mismatches"
    dut._log.info(
        f"PASS: Boundary coefficients verified "
        f"({len(values)} values x {len(addrs)} addresses = {len(values)*len(addrs)} checks)"
    )


@cocotb.test()
async def test_overwrite(dut):
    """Write poly A to slot 3, verify, write poly B, verify B replaced A."""
    await init(dut)

    rng = random.Random(300)
    poly_a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    poly_b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    # Write and verify A
    await write_poly(dut, 3, poly_a)
    result_a = await read_poly(dut, 3)
    assert result_a == poly_a, "Initial write A failed"

    # Overwrite with B and verify
    await write_poly(dut, 3, poly_b)
    result_b = await read_poly(dut, 3)
    assert result_b == poly_b, "Overwrite with B failed"

    # Verify it's really B, not A
    mismatches = sum(1 for i in range(KYBER_N) if poly_a[i] != poly_b[i])
    assert mismatches > 0, "Test is trivial: A and B are identical"

    dut._log.info(f"PASS: Overwrite verified (A and B differed in {mismatches} coefficients)")


@cocotb.test()
async def test_out_of_range_slot(dut):
    """Read from slots 20 and 31 — should return 0 (defensive bounds guard)."""
    await init(dut)

    # Write something to slot 0 so we know RAM isn't all zeros
    rng = random.Random(400)
    poly = [rng.randint(1, KYBER_Q - 1) for _ in range(KYBER_N)]
    await write_poly(dut, 0, poly)

    # Read from out-of-range slots
    for oor_slot in [20, 31]:
        for addr in [0, 128, 255]:
            got = await read_coeff(dut, oor_slot, addr)
            assert got == 0, (
                f"Out-of-range slot {oor_slot} addr {addr}: expected 0, got {got}"
            )

    dut._log.info("PASS: Out-of-range slots 20 and 31 return 0")


# ═══════════════════════════════════════════════════════════════════
# Micro-op tests (10 new tests)
# ═══════════════════════════════════════════════════════════════════

@cocotb.test()
async def test_ntt_forward(dut):
    """Load poly to slot 0, copy to NTT, run forward NTT, copy back, verify."""
    await init(dut)

    rng = random.Random(1000)
    poly = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    # Load poly into slot 0
    await write_poly(dut, 0, poly)

    # COPY_TO_NTT from slot 0
    await run_cmd(dut, OP_COPY_TO_NTT, slot_a=0)
    # RUN_NTT forward (mode=0)
    await run_cmd(dut, OP_RUN_NTT, param=0)
    # COPY_FROM_NTT to slot 1
    await run_cmd(dut, OP_COPY_FROM_NTT, slot_a=1)

    # Read result from slot 1
    result = await read_poly(dut, 1)

    # Compare with oracle
    expected = ntt_forward(poly)
    errors = compare_polys(result, expected, "ntt_forward", dut._log)
    assert errors == 0, f"NTT forward: {errors} mismatches"
    dut._log.info("PASS: NTT forward verified against oracle")


@cocotb.test()
async def test_ntt_intt_roundtrip(dut):
    """NTT then INTT should recover original polynomial."""
    await init(dut)

    rng = random.Random(1001)
    poly = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    await write_poly(dut, 0, poly)

    # Forward NTT
    await run_cmd(dut, OP_COPY_TO_NTT, slot_a=0)
    await run_cmd(dut, OP_RUN_NTT, param=0)
    # Inverse NTT (reuse NTT engine in-place)
    await run_cmd(dut, OP_RUN_NTT, param=1)
    # Copy result back to slot 2
    await run_cmd(dut, OP_COPY_FROM_NTT, slot_a=2)

    result = await read_poly(dut, 2)
    errors = compare_polys(result, poly, "ntt_roundtrip", dut._log)
    assert errors == 0, f"NTT round-trip: {errors} mismatches"
    dut._log.info("PASS: NTT/INTT round-trip recovered original polynomial")


@cocotb.test()
async def test_basemul(dut):
    """Load NTT-domain polys, run basemul, verify against oracle."""
    await init(dut)

    rng = random.Random(1002)
    # Generate two random polys and NTT them (oracle)
    a_time = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    b_time = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    a_ntt = ntt_forward(a_time)
    b_ntt = ntt_forward(b_time)

    # Load NTT-domain polys into slots 0 and 1
    await write_poly(dut, 0, a_ntt)
    await write_poly(dut, 1, b_ntt)

    # Copy to basemul A and B
    await run_cmd(dut, OP_COPY_TO_BM_A, slot_a=0)
    await run_cmd(dut, OP_COPY_TO_BM_B, slot_a=1)
    # Run basemul
    await run_cmd(dut, OP_RUN_BASEMUL)
    # Copy result back to slot 2
    await run_cmd(dut, OP_COPY_FROM_BM, slot_a=2)

    result = await read_poly(dut, 2)

    expected = oracle_basemul(a_ntt, b_ntt)
    errors = compare_polys(result, expected, "basemul", dut._log)
    assert errors == 0, f"Basemul: {errors} mismatches"
    dut._log.info("PASS: Basemul verified against oracle")


@cocotb.test()
async def test_poly_add(dut):
    """Load two polys, POLY_ADD, verify against oracle."""
    await init(dut)

    rng = random.Random(1003)
    a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    await write_poly(dut, 0, a)
    await write_poly(dut, 1, b)

    # POLY_ADD: slot_a=0 (dst, a+b), slot_b=1 (src)
    await run_cmd(dut, OP_POLY_ADD, slot_a=0, slot_b=1)

    result = await read_poly(dut, 0)
    expected = oracle_add(a, b)
    errors = compare_polys(result, expected, "poly_add", dut._log)
    assert errors == 0, f"Poly add: {errors} mismatches"
    dut._log.info("PASS: Polynomial addition verified against oracle")


@cocotb.test()
async def test_poly_sub(dut):
    """Load two polys, POLY_SUB, verify against oracle."""
    await init(dut)

    rng = random.Random(1004)
    a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    await write_poly(dut, 0, a)
    await write_poly(dut, 1, b)

    # POLY_SUB: slot_a=0 (dst, a-b), slot_b=1 (src)
    await run_cmd(dut, OP_POLY_SUB, slot_a=0, slot_b=1)

    result = await read_poly(dut, 0)
    expected = oracle_sub(a, b)
    errors = compare_polys(result, expected, "poly_sub", dut._log)
    assert errors == 0, f"Poly sub: {errors} mismatches"
    dut._log.info("PASS: Polynomial subtraction verified against oracle")


@cocotb.test()
async def test_compress_d1(dut):
    """Compress D=1, decompress D=1, verify round-trip."""
    await init(dut)

    rng = random.Random(1005)
    poly = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    await write_poly(dut, 0, poly)

    # Compress slot 0 → slot 1 (D=1)
    await run_cmd(dut, OP_COMPRESS, slot_a=0, slot_b=1, param=1)

    # Read compressed values
    compressed = await read_poly(dut, 1)

    # Verify compressed values against oracle
    expected_compressed = [compress_q(c, 1) for c in poly]
    errors = compare_polys(compressed, expected_compressed, "compress_d1", dut._log)
    assert errors == 0, f"Compress D=1: {errors} mismatches"

    # Decompress slot 1 → slot 2 (D=1)
    await run_cmd(dut, OP_DECOMPRESS, slot_a=1, slot_b=2, param=1)
    decompressed = await read_poly(dut, 2)

    expected_decompressed = [decompress_q(compress_q(c, 1), 1) for c in poly]
    errors = compare_polys(decompressed, expected_decompressed, "decompress_d1", dut._log)
    assert errors == 0, f"Decompress D=1: {errors} mismatches"
    dut._log.info("PASS: Compress/decompress D=1 round-trip verified")


@cocotb.test()
async def test_compress_d4(dut):
    """Compress D=4, decompress D=4, verify round-trip."""
    await init(dut)

    rng = random.Random(1006)
    poly = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    await write_poly(dut, 0, poly)

    # Compress → slot 1
    await run_cmd(dut, OP_COMPRESS, slot_a=0, slot_b=1, param=4)
    compressed = await read_poly(dut, 1)

    expected_compressed = [compress_q(c, 4) for c in poly]
    errors = compare_polys(compressed, expected_compressed, "compress_d4", dut._log)
    assert errors == 0, f"Compress D=4: {errors} mismatches"

    # Decompress → slot 2
    await run_cmd(dut, OP_DECOMPRESS, slot_a=1, slot_b=2, param=4)
    decompressed = await read_poly(dut, 2)

    expected_decompressed = [decompress_q(compress_q(c, 4), 4) for c in poly]
    errors = compare_polys(decompressed, expected_decompressed, "decompress_d4", dut._log)
    assert errors == 0, f"Decompress D=4: {errors} mismatches"
    dut._log.info("PASS: Compress/decompress D=4 round-trip verified")


@cocotb.test()
async def test_compress_d10(dut):
    """Compress D=10, decompress D=10, verify round-trip."""
    await init(dut)

    rng = random.Random(1007)
    poly = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    await write_poly(dut, 0, poly)

    # Compress → slot 1
    await run_cmd(dut, OP_COMPRESS, slot_a=0, slot_b=1, param=10)
    compressed = await read_poly(dut, 1)

    expected_compressed = [compress_q(c, 10) for c in poly]
    errors = compare_polys(compressed, expected_compressed, "compress_d10", dut._log)
    assert errors == 0, f"Compress D=10: {errors} mismatches"

    # Decompress → slot 2
    await run_cmd(dut, OP_DECOMPRESS, slot_a=1, slot_b=2, param=10)
    decompressed = await read_poly(dut, 2)

    expected_decompressed = [decompress_q(compress_q(c, 10), 10) for c in poly]
    errors = compare_polys(decompressed, expected_decompressed, "decompress_d10", dut._log)
    assert errors == 0, f"Decompress D=10: {errors} mismatches"
    dut._log.info("PASS: Compress/decompress D=10 round-trip verified")


@cocotb.test()
async def test_cbd_sample(dut):
    """Feed known bytes via CBD_SAMPLE, verify against oracle."""
    await init(dut)

    rng = random.Random(1008)
    input_bytes = [rng.randint(0, 255) for _ in range(128)]

    await run_cbd_cmd(dut, slot=3, input_bytes=input_bytes)

    result = await read_poly(dut, 3)

    expected = cbd_sample_eta2(input_bytes)
    errors = compare_polys(result, expected, "cbd_sample", dut._log)
    assert errors == 0, f"CBD sample: {errors} mismatches"
    dut._log.info("PASS: CBD sample verified against oracle (128 bytes → 256 coefficients)")


@cocotb.test()
async def test_multi_op_sequence(dut):
    """Chain NTT + basemul + INTT, verify against schoolbook_mul."""
    await init(dut)

    rng = random.Random(1009)
    a = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]
    b = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    # Load time-domain polys
    await write_poly(dut, 0, a)
    await write_poly(dut, 1, b)

    # NTT(a) → slot 0 via NTT engine
    await run_cmd(dut, OP_COPY_TO_NTT, slot_a=0)
    await run_cmd(dut, OP_RUN_NTT, param=0)
    await run_cmd(dut, OP_COPY_FROM_NTT, slot_a=2)  # NTT(a) in slot 2

    # NTT(b) → slot 1 via NTT engine
    await run_cmd(dut, OP_COPY_TO_NTT, slot_a=1)
    await run_cmd(dut, OP_RUN_NTT, param=0)
    await run_cmd(dut, OP_COPY_FROM_NTT, slot_a=3)  # NTT(b) in slot 3

    # Basemul: NTT(a) * NTT(b)
    await run_cmd(dut, OP_COPY_TO_BM_A, slot_a=2)
    await run_cmd(dut, OP_COPY_TO_BM_B, slot_a=3)
    await run_cmd(dut, OP_RUN_BASEMUL)
    await run_cmd(dut, OP_COPY_FROM_BM, slot_a=4)  # product in NTT domain in slot 4

    # INTT(product) → slot 5
    await run_cmd(dut, OP_COPY_TO_NTT, slot_a=4)
    await run_cmd(dut, OP_RUN_NTT, param=1)  # inverse NTT
    await run_cmd(dut, OP_COPY_FROM_NTT, slot_a=5)

    result = await read_poly(dut, 5)

    expected = schoolbook_mul(a, b)
    errors = compare_polys(result, expected, "multi_op_schoolbook", dut._log)
    assert errors == 0, f"Multi-op schoolbook: {errors} mismatches"
    dut._log.info("PASS: NTT + basemul + INTT matches schoolbook multiplication")
