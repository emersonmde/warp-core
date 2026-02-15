"""
cocotb testbench for keccak_sponge — multi-mode FIPS 202 sponge controller

Tests SHA3-256, SHA3-512, SHAKE-128, SHAKE-256 against Python hashlib oracle.
11 tests covering all modes, edge cases (empty, multiblock, block boundary).
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, ClockCycles
import hashlib

# Mode constants
SHA3_256  = 0
SHA3_512  = 1
SHAKE_128 = 2
SHAKE_256 = 3


async def reset_dut(dut):
    """Reset the DUT and initialize all inputs."""
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.mode.value = 0
    dut.absorb_valid.value = 0
    dut.absorb_data.value = 0
    dut.absorb_last.value = 0
    dut.squeeze_ready.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def start_hash(dut, mode):
    """Pulse start to begin a new hash with the given mode."""
    dut.mode.value = mode
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0


async def absorb_message(dut, data):
    """Feed message bytes into the sponge via valid/ready handshake."""
    if len(data) == 0:
        # Empty message — pulse absorb_last without valid
        dut.absorb_last.value = 1
        await RisingEdge(dut.clk)
        dut.absorb_last.value = 0
        return

    for i, byte_val in enumerate(data):
        is_last = (i == len(data) - 1)
        dut.absorb_valid.value = 1
        dut.absorb_data.value = byte_val
        dut.absorb_last.value = int(is_last)

        # Wait for absorb_ready at FallingEdge, then transfer on next RisingEdge
        while True:
            await FallingEdge(dut.clk)
            if dut.absorb_ready.value == 1:
                await RisingEdge(dut.clk)
                break
            await RisingEdge(dut.clk)

    dut.absorb_valid.value = 0
    dut.absorb_last.value = 0


async def squeeze_bytes(dut, n):
    """Read n output bytes from the sponge via valid/ready handshake."""
    result = []
    dut.squeeze_ready.value = 1

    for _ in range(n):
        while True:
            await FallingEdge(dut.clk)
            if dut.squeeze_valid.value == 1:
                result.append(dut.squeeze_data.value.to_unsigned())
                await RisingEdge(dut.clk)
                break
            await RisingEdge(dut.clk)

    dut.squeeze_ready.value = 0
    return bytes(result)


async def hash_message(dut, mode, data, squeeze_len):
    """Full hash: start -> absorb -> squeeze."""
    await start_hash(dut, mode)
    await absorb_message(dut, data)
    return await squeeze_bytes(dut, squeeze_len)


def compare_result(name, got, expected):
    """Assert and format mismatch details."""
    assert got == expected, (
        f"{name} mismatch:\n"
        f"  got:    {got.hex()}\n"
        f"  expect: {expected.hex()}"
    )


# =========================================================================
# Tests
# =========================================================================

@cocotb.test()
async def test_sha3_256_empty(dut):
    """SHA3-256 of empty message."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    result = await hash_message(dut, SHA3_256, b"", 32)
    expected = hashlib.sha3_256(b"").digest()
    compare_result("SHA3-256('')", result, expected)
    dut._log.info("SHA3-256 empty: PASS")


@cocotb.test()
async def test_sha3_256_short(dut):
    """SHA3-256 of 'abc' — NIST FIPS 202 example."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    result = await hash_message(dut, SHA3_256, b"abc", 32)
    expected = hashlib.sha3_256(b"abc").digest()
    compare_result("SHA3-256('abc')", result, expected)
    dut._log.info("SHA3-256 'abc': PASS")


@cocotb.test()
async def test_sha3_256_multiblock(dut):
    """SHA3-256 of message > 136 bytes (multi-block absorb)."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    msg = bytes(range(256)) * 2  # 512 bytes
    result = await hash_message(dut, SHA3_256, msg, 32)
    expected = hashlib.sha3_256(msg).digest()
    compare_result("SHA3-256 multiblock", result, expected)
    dut._log.info("SHA3-256 multiblock (512 bytes): PASS")


@cocotb.test()
async def test_sha3_512_short(dut):
    """SHA3-512 of 'abc'."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    result = await hash_message(dut, SHA3_512, b"abc", 64)
    expected = hashlib.sha3_512(b"abc").digest()
    compare_result("SHA3-512('abc')", result, expected)
    dut._log.info("SHA3-512 'abc': PASS")


@cocotb.test()
async def test_sha3_512_multiblock(dut):
    """SHA3-512 of message > 72 bytes (multi-block absorb)."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    msg = bytes(range(256))  # 256 bytes, well over rate=72
    result = await hash_message(dut, SHA3_512, msg, 64)
    expected = hashlib.sha3_512(msg).digest()
    compare_result("SHA3-512 multiblock", result, expected)
    dut._log.info("SHA3-512 multiblock (256 bytes): PASS")


@cocotb.test()
async def test_shake128_short(dut):
    """SHAKE-128 of short message, squeeze 32 bytes."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    msg = b"test message"
    result = await hash_message(dut, SHAKE_128, msg, 32)
    expected = hashlib.shake_128(msg).digest(32)
    compare_result("SHAKE-128 short", result, expected)
    dut._log.info("SHAKE-128 short: PASS")


@cocotb.test()
async def test_shake128_long_squeeze(dut):
    """SHAKE-128 squeeze 256 bytes (multi-block squeeze)."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    msg = b"long squeeze test"
    result = await hash_message(dut, SHAKE_128, msg, 256)
    expected = hashlib.shake_128(msg).digest(256)
    compare_result("SHAKE-128 long squeeze", result, expected)
    dut._log.info("SHAKE-128 long squeeze (256 bytes): PASS")


@cocotb.test()
async def test_shake256_short(dut):
    """SHAKE-256 of short message, squeeze 32 bytes."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    msg = b"shake256 test"
    result = await hash_message(dut, SHAKE_256, msg, 32)
    expected = hashlib.shake_256(msg).digest(32)
    compare_result("SHAKE-256 short", result, expected)
    dut._log.info("SHAKE-256 short: PASS")


@cocotb.test()
async def test_shake256_prf(dut):
    """SHAKE-256 PRF pattern: key||nonce -> 128 bytes (ML-KEM PRF)."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    # ML-KEM PRF: key (32 bytes) || nonce (1 byte) -> 128 bytes
    key = bytes(range(32))
    nonce = bytes([42])
    msg = key + nonce

    result = await hash_message(dut, SHAKE_256, msg, 128)
    expected = hashlib.shake_256(msg).digest(128)
    compare_result("SHAKE-256 PRF", result, expected)
    dut._log.info("SHAKE-256 PRF (33->128 bytes): PASS")


@cocotb.test()
async def test_block_boundary_pad(dut):
    """Message exactly fills rate block (padding goes into next block)."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    # SHA3-256 rate = 136. Message of exactly 136 bytes fills one block.
    # Padding must go into the next block.
    msg = bytes(range(136))
    result = await hash_message(dut, SHA3_256, msg, 32)
    expected = hashlib.sha3_256(msg).digest()
    compare_result("Block boundary pad", result, expected)
    dut._log.info("Block boundary padding (136 bytes): PASS")


@cocotb.test()
async def test_back_to_back(dut):
    """Two consecutive hashes using start reset."""
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    await reset_dut(dut)

    # First hash: SHA3-256
    msg1 = b"first message"
    result1 = await hash_message(dut, SHA3_256, msg1, 32)
    expected1 = hashlib.sha3_256(msg1).digest()
    compare_result("Back-to-back hash 1", result1, expected1)

    # Second hash: SHA3-512 (different mode)
    msg2 = b"second"
    result2 = await hash_message(dut, SHA3_512, msg2, 64)
    expected2 = hashlib.sha3_512(msg2).digest()
    compare_result("Back-to-back hash 2", result2, expected2)

    dut._log.info("Back-to-back hashing: PASS")
