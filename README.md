# Warp Core

CRYSTALS-Kyber (ML-KEM / FIPS 203) hardware accelerator targeting Xilinx Artix-7 FPGAs.

All arithmetic operates in Z_q where q = 3329.

## Status

**24 modules, 96 hardware tests + 60 ACVP oracle tests, all passing.** Milestones 1-6 complete.

| Milestone | Modules | Description |
|-----------|---------|-------------|
| 1. Modular Arithmetic | `cond_sub_q`, `barrett_reduce` | Conditional subtraction, Barrett reduction mod 3329 |
| 2. NTT Butterfly | `cond_add_q`, `mod_add`, `mod_sub`, `ntt_butterfly` | Cooley-Tukey butterfly datapath |
| 3. NTT/INTT Engine | `intt_butterfly`, `ntt_rom`, `poly_ram`, `ntt_engine` | 7-layer sequential NTT/INTT FSM (1800/2313 cycles) |
| 4. Kyber Operations | `basemul_unit`, `poly_basemul`, `compress`, `decompress` | Pointwise multiply, bit compression (FIPS 203 §4.2.1) |
| 5. Poly-level Ops | `poly_addsub`, `cbd_sampler`, `kyber_top`, `encaps_ctrl/top`, `keygen_ctrl/top`, `decaps_ctrl/top` | 20-slot RAM bank, micro-op FSM, ML-KEM-768 KeyGen/Encaps/Decrypt controllers |
| 6. ACVP Compliance | `kyber_acvp.py`, ACVP testbenches | 60 NIST ACVP vectors (25 keyGen + 25 encaps + 10 decaps), bit-exact FIPS 203 compliance |

Next: Milestone 8 targets performance optimizations — pipelined butterfly, overlapped read/write, and Vivado synthesis on XC7A35T. See [docs/architecture.md](docs/architecture.md) for the full roadmap and block diagrams.

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
make test_cond_sub_q         # Run one module's tests
make test_barrett_reduce
make test_acvp_oracle        # Run Python ACVP oracle tests only
make test_acvp_keygen        # Run hardware keygen against 25 ACVP vectors
make test_acvp_encaps        # Run hardware encaps against 25 ACVP vectors
make test_acvp_decaps        # Run hardware decaps against 10 ACVP vectors
```

## Waveforms

```bash
make waves_cond_sub_q        # Run tests + dump FST waveforms
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
- 2-cycle read/write butterfly pattern for synchronous RAM
- Separate CT/GS butterfly instantiation (mux outside critical path)
- Direct operations on RAM bank (saves 2-3 BRAMs)
- CBD dual-port write trick (halves sampling time)
- Unsigned-only datapath philosophy
- Flat sequencer ROM for algorithm controllers
- ACVP compliance testing strategy

## Design Notes

**Barrett constant V = 20158** (not 20159 from the C reference). The C implementation uses `ceil(2^26 / 3329) = 20159` with signed arithmetic. Hardware uses `floor(2^26 / 3329) = 20158` to guarantee non-negative remainders in unsigned datapath. Exhaustively verified correct for all 16-bit inputs.

See [CLAUDE.md](CLAUDE.md) for full conventions and constant provenance.

## License

Apache-2.0. See [LICENSE](LICENSE).
