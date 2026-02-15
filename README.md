# Warp Core

CRYSTALS-Kyber (ML-KEM / FIPS 203) hardware accelerator targeting Xilinx Artix-7 FPGAs.

All arithmetic operates in Z_q where q = 3329.

## Features

- **Complete ML-KEM-768 operations:** KeyGen, Encapsulate, and Decrypt controllers with micro-op sequencing
- **Ping-pong NTT engine:** Dual-RAM architecture achieves 1-butterfly-per-cycle throughput (911 cycles forward, 1168 cycles inverse at 100 MHz)
- **20-slot polynomial RAM bank** with 13 micro-op opcodes (NTT, basemul, add/sub, compress/decompress, CBD sampling)
- **NIST ACVP compliant:** Bit-exact against 60 official ML-KEM-768 test vectors (25 keyGen + 25 encaps + 10 decaps)
- **Unsigned-only datapath:** Barrett reduction with V=20158 (floor), no signed arithmetic anywhere
- **24 Verilog modules,** 96 hardware tests + 60 ACVP oracle tests

## Architecture

| Layer | Modules | Description |
|-------|---------|-------------|
| Arithmetic | `barrett_reduce`, `cond_sub_q`, `cond_add_q`, `mod_add`, `mod_sub` | Modular arithmetic primitives in Z_q |
| NTT Datapath | `ntt_butterfly`, `intt_butterfly`, `ntt_rom`, `poly_ram` | Cooley-Tukey / Gentleman-Sande butterflies, twiddle ROM, dual-port RAM |
| NTT Engine | `ntt_engine` | 7-layer NTT/INTT FSM with ping-pong dual-RAM overlap and dual-port INTT scaling |
| Polynomial Ops | `basemul_unit`, `poly_basemul`, `compress`, `decompress`, `poly_addsub`, `cbd_sampler` | Pointwise multiply, bit compression (FIPS 203 §4.2.1), CBD noise sampling |
| Top-level | `kyber_top` | 20-slot RAM bank + micro-op FSM (13 opcodes) |
| Controllers | `keygen_ctrl/top`, `encaps_ctrl/top`, `decaps_ctrl/top` | ML-KEM-768 algorithm sequencers |

**Resource budget (XC7A35T):** 25 BRAM18E1 out of 50. See [docs/architecture.md](docs/architecture.md) for block diagrams and detailed design notes.

## Prerequisites

- [Icarus Verilog](http://iverilog.icarus.com/) 12.0+ — `brew install icarus-verilog` on macOS
- Python 3.9+
- [cocotb](https://www.cocotb.org/) 2.x — installed via pip

```bash
pip install -r requirements-test.txt
```

## Build & Test

```bash
make test                    # Run all testbenches (96 hardware + 60 ACVP oracle)
make test_ntt_engine         # Run one module's tests
make test_acvp_oracle        # Run Python ACVP oracle tests only
make test_acvp_keygen        # Run hardware keygen against 25 ACVP vectors
make test_acvp_encaps        # Run hardware encaps against 25 ACVP vectors
make test_acvp_decaps        # Run hardware decaps against 10 ACVP vectors
```

## Waveforms

```bash
make waves_ntt_engine        # Run tests + dump FST waveforms
make waves_barrett_reduce
```

FST files are written to `tb/<module>/sim_build/<module>.fst` and can be opened with [GTKWave](http://gtkwave.sourceforge.net/) or [Surfer](https://surfer-project.org/).

## Directory Layout

```
rtl/               Synthesizable Verilog modules
rtl/kyber_pkg.vh   Shared parameters (Q, N, Barrett constants)
tb/                cocotb testbenches (one directory per module)
tb/common.mk       Shared cocotb Makefile boilerplate
ref/               Python reference implementations (test oracles)
ref/kyber_acvp.py  FIPS 203 encoding/hashing layer for ACVP testing
docs/              Architecture diagrams and design notes
```

## Design Decisions

Key hardware design decisions are documented in [docs/design_decisions.md](docs/design_decisions.md), covering:

- Barrett constant V=20158 (floor) vs. the C reference's V=20159 (ceiling)
- Basemul optimization from 5 to 3 Barrett reductions
- Compress via Barrett quotient extraction (no hardware divider)
- Shift-based NTT address generation (zero DSP usage)
- Ping-pong dual-RAM NTT for 1-butterfly-per-cycle throughput
- Separate CT/GS butterfly instantiation (mux outside critical path)
- Direct operations on RAM bank (saves 2-3 BRAMs)
- CBD dual-port write trick (halves sampling time)
- Unsigned-only datapath philosophy
- Flat sequencer ROM for algorithm controllers
- ACVP compliance testing strategy

## License

Apache-2.0. See [LICENSE](LICENSE).
