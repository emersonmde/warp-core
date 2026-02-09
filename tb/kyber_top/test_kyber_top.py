"""Testbench for kyber_top module.

Tests 20-slot polynomial RAM bank with host I/O:
  1. Write/read single slot — random poly to slot 0, verify readback
  2. All slots independent — distinct poly per slot, verify isolation
  3. Slot isolation — write to slot 5, verify slots 4 and 6 untouched
  4. Boundary coefficients — boundary values at boundary addresses
  5. Overwrite — write A then B to same slot, verify B replaced A
  6. Out-of-range slot — reads from slots 20 and 31 return 0
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge


KYBER_Q = 3329
KYBER_N = 256
NUM_SLOTS = 20
CLK_PERIOD_NS = 10


async def init(dut):
    """Start clock, assert reset, release."""
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst_n.value = 0
    dut.host_we.value = 0
    dut.host_slot.value = 0
    dut.host_addr.value = 0
    dut.host_din.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def write_poly(dut, slot, coeffs):
    """Write 256 coefficients to a slot."""
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


@cocotb.test()
async def test_write_read_single_slot(dut):
    """Write random poly to slot 0, read back, verify."""
    await init(dut)

    rng = random.Random(42)
    poly = [rng.randint(0, KYBER_Q - 1) for _ in range(KYBER_N)]

    await write_poly(dut, 0, poly)
    result = await read_poly(dut, 0)

    errors = 0
    for i in range(KYBER_N):
        if result[i] != poly[i]:
            dut._log.error(f"FAIL: slot 0 coeff[{i}] = {result[i]}, expected {poly[i]}")
            errors += 1
            if errors >= 10:
                dut._log.error("(stopping after 10 errors)")
                break

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
