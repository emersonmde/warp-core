# tb/common.mk — Shared cocotb testbench boilerplate
#
# Per-test Makefiles set these variables, then include this file:
#   VERILOG_SOURCES      — RTL source files (absolute paths)
#   TOPLEVEL             — Top-level module name
#   COCOTB_TEST_MODULES  — Python test module name
#
# Built-in VCD/FST support:
#   make WAVES=1    — Dumps <toplevel>.fst in the build directory (Icarus FST format)
#   Viewable with GTKWave: gtkwave sim_build/<toplevel>.fst

SIM ?= icarus
TOPLEVEL_LANG ?= verilog

VERILOG_INCLUDE_DIRS = $(PWD)/../../rtl

COCOTB_RESULTS_FILE = results.xml

include $(shell cocotb-config --makefiles)/Makefile.sim
