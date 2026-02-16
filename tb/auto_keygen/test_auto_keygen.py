"""Autonomous KeyGen ACVP compliance test.

Tests auto_keygen_top which performs the full K-PKE.KeyGen autonomously:
the host only provides 32 seed bytes and reads back results. All hashing
(G, expand_a, PRF) is done in hardware via the integrated Keccak sponge.

Per ACVP vector:
  1. Feed 32 bytes of d via seed interface.
  2. Assert start, wait for done.
  3. Read t_hat from slots 0, 3, 6.
  4. Read s_hat from slots 9-11.
  5. Read rho via rho_byte_out (32 bytes).
  6. Python: ek = ByteEncode(12, t_hat) || rho.
     dk = ByteEncode(12, s_hat) || ek || H(ek) || z.
  7. Compare ek, dk against ACVP expected.
"""

import json
import os
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ref'))
from kyber_math import KYBER_Q, KYBER_N
from kyber_acvp import K, byte_encode, g_hash, h_hash

CLK_PERIOD_NS = 10
PARAM_SET = "ML-KEM-768"
VECTORS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'ref', 'acvp_vectors')


def load_vectors():
    """Load cached ACVP keyGen vectors (must run test_acvp_oracle.py first)."""
    prompt_path = os.path.join(VECTORS_DIR, "keygen_prompt.json")
    results_path = os.path.join(VECTORS_DIR, "keygen_results.json")

    assert os.path.exists(prompt_path), \
        f"ACVP vectors not cached. Run: python ref/test_acvp_oracle.py"

    with open(prompt_path) as f:
        prompt = json.load(f)
    with open(results_path) as f:
        results = json.load(f)

    prompt_groups = {g["tgId"]: g for g in prompt["testGroups"]
                     if g.get("parameterSet") == PARAM_SET}
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
                "d": bytes.fromhex(tc["d"]),
                "z": bytes.fromhex(tc["z"]),
                "expected_ek": bytes.fromhex(exp["ek"]),
                "expected_dk": bytes.fromhex(exp["dk"]),
            })
    return vectors


async def init(dut):
    cocotb.start_soon(Clock(dut.clk, CLK_PERIOD_NS, unit='ns').start())
    dut.rst_n.value = 0
    dut.start.value = 0
    dut.seed_valid.value = 0
    dut.seed_data.value = 0
    dut.host_we.value = 0
    dut.host_slot.value = 0
    dut.host_addr.value = 0
    dut.host_din.value = 0
    dut.rho_byte_idx.value = 0
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


async def read_rho(dut):
    """Read 32 rho bytes via rho_byte_out port."""
    rho_bytes = bytearray(32)
    for i in range(32):
        dut.rho_byte_idx.value = i
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        rho_bytes[i] = dut.rho_byte_out.value.to_unsigned()
    return bytes(rho_bytes)


async def feed_seed_background(dut, seed_bytes):
    """Feed 32 seed bytes via valid/ready handshake in background."""
    byte_idx = 0
    total = len(seed_bytes)
    while byte_idx < total:
        dut.seed_valid.value = 1
        dut.seed_data.value = seed_bytes[byte_idx]
        await FallingEdge(dut.clk)
        if dut.seed_ready.value == 1:
            byte_idx += 1
        await RisingEdge(dut.clk)
    dut.seed_valid.value = 0


async def run_auto_keygen(dut, seed_bytes, timeout=200000):
    """Start autonomous keygen and feed seed bytes in background."""
    cocotb.start_soon(feed_seed_background(dut, seed_bytes))

    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    for cycle in range(timeout):
        await RisingEdge(dut.clk)
        await FallingEdge(dut.clk)
        if dut.done.value == 1:
            return cycle + 1
    raise TimeoutError(f"Auto KeyGen did not complete within {timeout} cycles")


@cocotb.test()
async def test_single_vector(dut):
    """Quick sanity check: one ACVP vector."""
    vectors = load_vectors()
    vec = vectors[0]
    dut._log.info(f"Single vector test: tcId={vec['tcId']}")

    await init(dut)

    cycles = await run_auto_keygen(dut, vec["d"])
    dut._log.info(f"  Completed in {cycles} cycles")

    # Read results
    t_hat_hw = [await read_poly(dut, s) for s in [0, 3, 6]]
    s_hat_hw = [await read_poly(dut, 9 + i) for i in range(K)]
    rho_hw = await read_rho(dut)

    # Python-side encode
    rho_py, _ = g_hash(vec["d"] + bytes([K]))
    assert rho_hw == rho_py, f"rho mismatch"

    ek = b''
    for i in range(K):
        ek += byte_encode(12, t_hat_hw[i])
    ek += rho_hw

    dk_pke = b''
    for i in range(K):
        dk_pke += byte_encode(12, s_hat_hw[i])
    dk = dk_pke + ek + h_hash(ek) + vec["z"]

    assert ek == vec["expected_ek"], "ek mismatch"
    assert dk == vec["expected_dk"], "dk mismatch"
    dut._log.info(f"  PASS tcId={vec['tcId']}")


@cocotb.test()
async def test_rho_readback(dut):
    """Verify rho matches Python-computed G(d||K) output."""
    vectors = load_vectors()
    vec = vectors[1]

    await init(dut)
    await run_auto_keygen(dut, vec["d"])

    rho_hw = await read_rho(dut)
    rho_py, _ = g_hash(vec["d"] + bytes([K]))
    assert rho_hw == rho_py, f"rho readback mismatch"
    dut._log.info("PASS: rho readback matches Python oracle")


@cocotb.test()
async def test_back_to_back(dut):
    """Two consecutive keygens without reset."""
    vectors = load_vectors()
    await init(dut)

    for vi in range(2):
        vec = vectors[vi]
        cycles = await run_auto_keygen(dut, vec["d"])
        dut._log.info(f"  Run {vi}: tcId={vec['tcId']} in {cycles} cycles")

        t_hat_hw = [await read_poly(dut, s) for s in [0, 3, 6]]
        s_hat_hw = [await read_poly(dut, 9 + i) for i in range(K)]
        rho_hw = await read_rho(dut)

        ek = b''
        for i in range(K):
            ek += byte_encode(12, t_hat_hw[i])
        ek += rho_hw

        dk_pke = b''
        for i in range(K):
            dk_pke += byte_encode(12, s_hat_hw[i])
        dk = dk_pke + ek + h_hash(ek) + vec["z"]

        assert ek == vec["expected_ek"], f"Run {vi}: ek mismatch"
        assert dk == vec["expected_dk"], f"Run {vi}: dk mismatch"
        dut._log.info(f"  PASS run {vi}")

    dut._log.info("PASS: back-to-back keygens")


@cocotb.test()
async def test_cycle_count(dut):
    """Verify completion within expected cycle budget."""
    vectors = load_vectors()
    vec = vectors[0]

    await init(dut)
    cycles = await run_auto_keygen(dut, vec["d"])
    dut._log.info(f"Cycle count: {cycles}")

    # Budget: ~34k estimate + margin -> 50k max
    assert cycles < 50000, f"KeyGen took {cycles} cycles (budget: 50k)"
    dut._log.info(f"PASS: {cycles} cycles within 50k budget")


@cocotb.test()
async def test_acvp_keygen_vectors(dut):
    """Run all ML-KEM-768 ACVP keyGen vectors through autonomous hardware."""
    vectors = load_vectors()
    dut._log.info(f"Loaded {len(vectors)} ACVP keyGen vectors")

    await init(dut)

    total_errors = 0

    for vec in vectors:
        tc_id = vec["tcId"]

        cycles = await run_auto_keygen(dut, vec["d"])

        # Read back t_hat from slots 0, 3, 6
        t_hat_hw = [await read_poly(dut, s) for s in [0, 3, 6]]
        s_hat_hw = [await read_poly(dut, 9 + i) for i in range(K)]
        rho_hw = await read_rho(dut)

        # Encode ek = ByteEncode(12, t_hat) || rho
        ek = b''
        for i in range(K):
            ek += byte_encode(12, t_hat_hw[i])
        ek += rho_hw

        # Encode dk = ByteEncode(12, s_hat) || ek || H(ek) || z
        dk_pke = b''
        for i in range(K):
            dk_pke += byte_encode(12, s_hat_hw[i])
        dk = dk_pke + ek + h_hash(ek) + vec["z"]

        if ek != vec["expected_ek"]:
            dut._log.error(f"  FAIL tcId={tc_id}: ek mismatch")
            for i in range(min(len(ek), len(vec["expected_ek"]))):
                if ek[i] != vec["expected_ek"][i]:
                    dut._log.error(
                        f"    first diff at byte {i}: got 0x{ek[i]:02x}, "
                        f"expected 0x{vec['expected_ek'][i]:02x}")
                    break
            total_errors += 1
        elif dk != vec["expected_dk"]:
            dut._log.error(f"  FAIL tcId={tc_id}: dk mismatch")
            total_errors += 1
        else:
            dut._log.info(f"  PASS tcId={tc_id} ({cycles} cyc)")

    assert total_errors == 0, \
        f"ACVP KeyGen: {total_errors}/{len(vectors)} vectors failed"
    dut._log.info(
        f"PASS: All {len(vectors)} ACVP autonomous keyGen vectors match")
