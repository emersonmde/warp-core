"""Autonomous Encaps ACVP compliance test.

Tests auto_encaps_top which performs the full ML-KEM.Encaps_internal
autonomously: the host only provides 32 bytes of m and 1184 bytes of ek,
then reads back compressed ciphertext and shared secret K. All hashing
(H, G, expand_a, PRF) is done in hardware via the integrated Keccak sponge.

Per ACVP vector:
  1. Feed 32 bytes of m + 1184 bytes of ek via din interface.
  2. Assert start, wait for done.
  3. Read compressed u from slots 16-18 (D=10), v from slot 19 (D=4).
  4. Read K via k_byte_out (32 bytes).
  5. Python: c = ByteEncode(10, u[0..2]) || ByteEncode(4, v).
  6. Compare c and K against ACVP expected.
"""

import json
import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, KYBER_N
from kyber_acvp import K, DU, DV, byte_encode, g_hash, h_hash

CLK_PERIOD_NS = 10
PARAM_SET = "ML-KEM-768"
VECTORS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'ref', 'acvp_vectors')


def load_vectors():
    """Load cached ACVP encapsulation vectors."""
    prompt_path = os.path.join(VECTORS_DIR, "encapdecap_prompt.json")
    results_path = os.path.join(VECTORS_DIR, "encapdecap_results.json")

    assert os.path.exists(prompt_path), \
        f"ACVP vectors not cached. Run: python ref/test_acvp_oracle.py"

    with open(prompt_path) as f:
        prompt = json.load(f)
    with open(results_path) as f:
        results = json.load(f)

    prompt_groups = {g["tgId"]: g for g in prompt["testGroups"]
                     if g.get("parameterSet") == PARAM_SET
                     and g.get("function") == "encapsulation"}
    results_groups = {g["tgId"]: g for g in results["testGroups"]
                      if g["tgId"] in prompt_groups}

    vectors = []
    for tg_id in sorted(prompt_groups.keys()):
        pg = prompt_groups[tg_id]
        rmap = {tc["tcId"]: tc for tc in results_groups[tg_id]["tests"]}
        for tc in pg["tests"]:
            exp = rmap[tc["tcId"]]
            vectors.append({
                "tcId": tc["tcId"],
                "ek": bytes.fromhex(tc["ek"]),
                "m": bytes.fromhex(tc["m"]),
                "expected_c": bytes.fromhex(exp["c"]),
                "expected_k": bytes.fromhex(exp["k"]),
            })
    return vectors


async def init(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.din_valid.value = 0
    dut.din_data.value = 0
    dut.host_we.value = 0
    dut.host_slot.value = 0
    dut.host_addr.value = 0
    dut.host_din.value = 0
    dut.k_byte_idx.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def read_poly(dut, slot):
    """Read 256 coefficients from host bank slot."""
    result = []
    for addr in range(KYBER_N):
        dut.host_slot.value = slot
        dut.host_addr.value = addr
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        result.append(dut.host_dout.value.to_unsigned())
    return result


async def read_k(dut):
    """Read 32 K bytes via k_byte_out port."""
    k_bytes = bytearray(32)
    for i in range(32):
        dut.k_byte_idx.value = i
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        k_bytes[i] = dut.k_byte_out.value.to_unsigned()
    return bytes(k_bytes)


async def feed_din_background(dut, data_bytes):
    """Feed data bytes via valid/ready handshake in background."""
    byte_idx = 0
    total = len(data_bytes)
    while byte_idx < total:
        dut.din_valid.value = 1
        dut.din_data.value = data_bytes[byte_idx]
        await FallingEdge(dut.clk)
        if dut.din_ready.value == 1:
            byte_idx += 1
        await RisingEdge(dut.clk)
    dut.din_valid.value = 0


async def run_auto_encaps(dut, m_bytes, ek_bytes, timeout=200000):
    """Start autonomous encaps and feed m+ek bytes in background."""
    all_bytes = m_bytes + ek_bytes
    cocotb.start_soon(feed_din_background(dut, all_bytes))

    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    for cycle in range(timeout):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        if dut.done.value == 1:
            return cycle + 1
    raise TimeoutError(f"Auto Encaps did not complete within {timeout} cycles")


@cocotb.test()
async def test_single_vector(dut):
    """Quick sanity check: one ACVP vector."""
    vectors = load_vectors()
    vec = vectors[0]
    dut._log.info(f"Single vector test: tcId={vec['tcId']}")

    await init(dut)

    cycles = await run_auto_encaps(dut, vec["m"], vec["ek"])
    dut._log.info(f"  Completed in {cycles} cycles")

    # Read compressed u from slots 16-18 (D=10)
    u_compressed = []
    for i in range(K):
        u_compressed.append(await read_poly(dut, 16 + i))

    # Read compressed v from slot 19 (D=4)
    v_compressed = await read_poly(dut, 19)

    # Encode ciphertext
    c = b''
    for i in range(K):
        c += byte_encode(DU, u_compressed[i])
    c += byte_encode(DV, v_compressed)

    # Read K
    k_hw = await read_k(dut)

    assert c == vec["expected_c"], f"ciphertext mismatch"
    assert k_hw == vec["expected_k"], f"K mismatch"
    dut._log.info(f"  PASS tcId={vec['tcId']}")


@cocotb.test()
async def test_k_readback(dut):
    """Verify K matches Python G(m || H(ek)) computation."""
    vectors = load_vectors()
    vec = vectors[1]

    await init(dut)
    await run_auto_encaps(dut, vec["m"], vec["ek"])

    k_hw = await read_k(dut)
    h = h_hash(vec["ek"])
    k_py, _ = g_hash(vec["m"] + h)
    assert k_hw == k_py, f"K readback mismatch"
    dut._log.info("PASS: K readback matches Python oracle")


@cocotb.test()
async def test_back_to_back(dut):
    """Two consecutive encaps without reset."""
    vectors = load_vectors()
    await init(dut)

    for vi in range(2):
        vec = vectors[vi]
        cycles = await run_auto_encaps(dut, vec["m"], vec["ek"])
        dut._log.info(f"  Run {vi}: tcId={vec['tcId']} in {cycles} cycles")

        u_compressed = []
        for i in range(K):
            u_compressed.append(await read_poly(dut, 16 + i))
        v_compressed = await read_poly(dut, 19)

        c = b''
        for i in range(K):
            c += byte_encode(DU, u_compressed[i])
        c += byte_encode(DV, v_compressed)

        k_hw = await read_k(dut)

        assert c == vec["expected_c"], f"Run {vi}: ciphertext mismatch"
        assert k_hw == vec["expected_k"], f"Run {vi}: K mismatch"
        dut._log.info(f"  PASS run {vi}")

    dut._log.info("PASS: back-to-back encaps")


@cocotb.test()
async def test_cycle_count(dut):
    """Verify completion within expected cycle budget."""
    vectors = load_vectors()
    vec = vectors[0]

    await init(dut)
    cycles = await run_auto_encaps(dut, vec["m"], vec["ek"])
    dut._log.info(f"Cycle count: {cycles}")

    # Budget: ~39k estimate + margin -> 50k max
    assert cycles < 50000, f"Encaps took {cycles} cycles (budget: 50k)"
    dut._log.info(f"PASS: {cycles} cycles within 50k budget")


@cocotb.test()
async def test_acvp_encaps_vectors(dut):
    """Run all ML-KEM-768 ACVP encapsulation vectors through autonomous hardware."""
    vectors = load_vectors()
    dut._log.info(f"Loaded {len(vectors)} ACVP encapsulation vectors")

    await init(dut)

    total_errors = 0

    for vec in vectors:
        tc_id = vec["tcId"]

        cycles = await run_auto_encaps(dut, vec["m"], vec["ek"])

        u_compressed = []
        for i in range(K):
            u_compressed.append(await read_poly(dut, 16 + i))
        v_compressed = await read_poly(dut, 19)

        c = b''
        for i in range(K):
            c += byte_encode(DU, u_compressed[i])
        c += byte_encode(DV, v_compressed)

        k_hw = await read_k(dut)

        c_ok = (c == vec["expected_c"])
        k_ok = (k_hw == vec["expected_k"])

        if c_ok and k_ok:
            dut._log.info(f"  PASS tcId={tc_id} ({cycles} cyc)")
        else:
            total_errors += 1
            if not c_ok:
                dut._log.error(f"  FAIL tcId={tc_id}: ciphertext mismatch")
                for i in range(min(len(c), len(vec["expected_c"]))):
                    if c[i] != vec["expected_c"][i]:
                        dut._log.error(
                            f"    first diff at byte {i}: got 0x{c[i]:02x}, "
                            f"expected 0x{vec['expected_c'][i]:02x}")
                        break
            if not k_ok:
                dut._log.error(f"  FAIL tcId={tc_id}: K mismatch")

    assert total_errors == 0, \
        f"ACVP Encaps: {total_errors}/{len(vectors)} vectors failed"
    dut._log.info(
        f"PASS: All {len(vectors)} ACVP autonomous encaps vectors match")
