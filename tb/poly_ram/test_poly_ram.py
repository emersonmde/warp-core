"""Testbench for poly_ram module.

First clocked testbench in the project. Tests:
  1. Write-then-read via port A
  2. Write-then-read via port B
  3. Dual-port simultaneous access
  4. All-address sweep
  5. Read-first behavior (output shows old value on write cycle)

Synchronous RAM timing (10ns clock period):
  - Drive inputs before rising edge (in the "active" phase)
  - On rising edge: RAM latches addr, performs read/write
  - Read outputs at falling edge (5ns after posedge, NBA settled)
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge


async def init(dut):
    """Start clock and initialize all inputs."""
    cocotb.start_soon(Clock(dut.clk, 10, unit='ns').start())
    dut.we_a.value = 0
    dut.addr_a.value = 0
    dut.din_a.value = 0
    dut.we_b.value = 0
    dut.addr_b.value = 0
    dut.din_b.value = 0
    await RisingEdge(dut.clk)


@cocotb.test()
async def test_write_read_port_a(dut):
    """Write values via port A, then read them back."""
    await init(dut)

    values = {}
    for addr in range(16):
        val = (addr * 137 + 42) % 4096
        values[addr] = val
        dut.we_a.value = 1
        dut.addr_a.value = addr
        dut.din_a.value = val
        await RisingEdge(dut.clk)

    dut.we_a.value = 0

    # Read back: set addr, wait for rising edge + falling edge to read output
    errors = 0
    for addr in range(16):
        dut.addr_a.value = addr
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        result = dut.dout_a.value.to_unsigned()
        if result != values[addr]:
            dut._log.error(
                f"FAIL port A: addr={addr}, got={result}, expected={values[addr]}"
            )
            errors += 1

    assert errors == 0, f"Port A write/read: {errors} errors"
    dut._log.info("PASS: Port A write-then-read (16 addresses)")


@cocotb.test()
async def test_write_read_port_b(dut):
    """Write values via port B, then read them back."""
    await init(dut)

    values = {}
    for addr in range(16):
        val = (addr * 251 + 99) % 4096
        values[addr] = val
        dut.we_b.value = 1
        dut.addr_b.value = addr
        dut.din_b.value = val
        await RisingEdge(dut.clk)

    dut.we_b.value = 0

    errors = 0
    for addr in range(16):
        dut.addr_b.value = addr
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        result = dut.dout_b.value.to_unsigned()
        if result != values[addr]:
            dut._log.error(
                f"FAIL port B: addr={addr}, got={result}, expected={values[addr]}"
            )
            errors += 1

    assert errors == 0, f"Port B write/read: {errors} errors"
    dut._log.info("PASS: Port B write-then-read (16 addresses)")


@cocotb.test()
async def test_dual_port_simultaneous(dut):
    """Write via port A, read same data via port B simultaneously."""
    await init(dut)

    # Fill addresses 0..7 via port A
    for addr in range(8):
        dut.we_a.value = 1
        dut.addr_a.value = addr
        dut.din_a.value = addr * 100
        await RisingEdge(dut.clk)

    dut.we_a.value = 0
    await RisingEdge(dut.clk)  # let last write complete

    # Read via port B while writing new values via port A to different addresses
    errors = 0
    for addr in range(8):
        dut.addr_b.value = addr
        dut.we_a.value = 1
        dut.addr_a.value = addr + 8
        dut.din_a.value = (addr + 8) * 100
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)

        result = dut.dout_b.value.to_unsigned()
        expected = addr * 100
        if result != expected:
            dut._log.error(
                f"FAIL dual-port: read addr={addr}, got={result}, expected={expected}"
            )
            errors += 1

    dut.we_a.value = 0

    assert errors == 0, f"Dual-port simultaneous: {errors} errors"
    dut._log.info("PASS: Dual-port simultaneous access")


@cocotb.test()
async def test_all_address_sweep(dut):
    """Write unique values to all 256 addresses, read them all back."""
    await init(dut)

    rng = random.Random(42)
    values = [rng.randint(0, 4095) for _ in range(256)]

    # Write all via port A
    for addr in range(256):
        dut.we_a.value = 1
        dut.addr_a.value = addr
        dut.din_a.value = values[addr]
        await RisingEdge(dut.clk)

    dut.we_a.value = 0

    # Read all back via port A
    errors = 0
    for addr in range(256):
        dut.addr_a.value = addr
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        result = dut.dout_a.value.to_unsigned()
        if result != values[addr]:
            dut._log.error(
                f"FAIL sweep: addr={addr}, got={result}, expected={values[addr]}"
            )
            errors += 1

    assert errors == 0, f"All-address sweep: {errors}/256 errors"
    dut._log.info("PASS: All 256 addresses verified")


@cocotb.test()
async def test_read_first_behavior(dut):
    """Verify read-first: output on write cycle shows old value."""
    await init(dut)

    # Write value 100 to address 0
    dut.we_a.value = 1
    dut.addr_a.value = 0
    dut.din_a.value = 100
    await RisingEdge(dut.clk)

    # Overwrite address 0 with 200 (addr_a still 0 from above).
    # Read-first: dout_a should latch the OLD value (100) on this edge.
    dut.din_a.value = 200
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)

    old_val = dut.dout_a.value.to_unsigned()
    assert old_val == 100, \
        f"Read-first violated: got {old_val}, expected 100 (old value)"

    # Read address 0 again without writing — should now show 200
    dut.we_a.value = 0
    dut.addr_a.value = 0
    await RisingEdge(dut.clk)
    await FallingEdge(dut.clk)
    new_val = dut.dout_a.value.to_unsigned()
    assert new_val == 200, \
        f"Updated value not visible: got {new_val}, expected 200"

    dut._log.info("PASS: Read-first behavior verified")
