# Warp Core

CRYSTALS-Kyber (ML-KEM / FIPS 203) hardware accelerator targeting Xilinx Artix-7 FPGAs.

All arithmetic operates in Z_q where q = 3329.

## Status

**14 modules, 50 tests, all passing.** Milestones 1–4 complete.

| Milestone | Modules | Description |
|-----------|---------|-------------|
| 1. Modular Arithmetic | `cond_sub_q`, `barrett_reduce` | Conditional subtraction, Barrett reduction mod 3329 |
| 2. NTT Butterfly | `cond_add_q`, `mod_add`, `mod_sub`, `ntt_butterfly` | Cooley-Tukey butterfly datapath |
| 3. NTT/INTT Engine | `intt_butterfly`, `ntt_rom`, `poly_ram`, `ntt_engine` | 7-layer sequential NTT/INTT FSM (1800/2313 cycles) |
| 4. Kyber Operations | `basemul_unit`, `poly_basemul`, `compress`, `decompress` | Pointwise multiply, bit compression (FIPS 203 §4.2.1) |

Next: Milestones 5–6 build toward `kyber_top` (ML-KEM-768 KeyGen/Encaps/Decaps). See [docs/architecture.md](docs/architecture.md) for the full roadmap, block diagrams, and [docs/kyber_top_plan.md](docs/kyber_top_plan.md) for the detailed implementation plan.

## Prerequisites

- [Icarus Verilog](http://iverilog.icarus.com/) 12.0+ — `brew install icarus-verilog` on macOS
- Python 3.9+
- [cocotb](https://www.cocotb.org/) 2.x — installed via pip

```bash
pip install -r requirements-test.txt
```

## Build & Test

```bash
make test                    # Run all testbenches
make test_cond_sub_q         # Run one module's tests
make test_barrett_reduce
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
docs/              Architecture diagrams and design notes
```

## Design Notes

**Barrett constant V = 20158** (not 20159 from the C reference). The C implementation uses `ceil(2^26 / 3329) = 20159` with signed arithmetic. Hardware uses `floor(2^26 / 3329) = 20158` to guarantee non-negative remainders in unsigned datapath. Exhaustively verified correct for all 16-bit inputs.

See [CLAUDE.md](CLAUDE.md) for full conventions and constant provenance.

## License

Apache-2.0. See [LICENSE](LICENSE).
