.PHONY: test clean
.PHONY: test_cond_sub_q test_barrett_reduce test_cond_add_q test_mod_add test_mod_sub test_ntt_butterfly
.PHONY: test_intt_butterfly test_ntt_rom test_poly_ram test_ntt_engine
.PHONY: waves_cond_sub_q waves_barrett_reduce waves_cond_add_q waves_mod_add waves_mod_sub waves_ntt_butterfly
.PHONY: waves_intt_butterfly waves_ntt_rom waves_poly_ram waves_ntt_engine

test: test_cond_sub_q test_barrett_reduce test_cond_add_q test_mod_add test_mod_sub test_ntt_butterfly \
      test_intt_butterfly test_ntt_rom test_poly_ram test_ntt_engine

test_cond_sub_q:
	$(MAKE) -C tb/cond_sub_q

test_barrett_reduce:
	$(MAKE) -C tb/barrett_reduce

test_cond_add_q:
	$(MAKE) -C tb/cond_add_q

test_mod_add:
	$(MAKE) -C tb/mod_add

test_mod_sub:
	$(MAKE) -C tb/mod_sub

test_ntt_butterfly:
	$(MAKE) -C tb/ntt_butterfly

test_intt_butterfly:
	$(MAKE) -C tb/intt_butterfly

test_ntt_rom:
	$(MAKE) -C tb/ntt_rom

test_poly_ram:
	$(MAKE) -C tb/poly_ram

test_ntt_engine:
	$(MAKE) -C tb/ntt_engine

# Waveform dumps — produces FST files viewable in GTKWave
waves_cond_sub_q:
	$(MAKE) -C tb/cond_sub_q WAVES=1

waves_barrett_reduce:
	$(MAKE) -C tb/barrett_reduce WAVES=1

waves_cond_add_q:
	$(MAKE) -C tb/cond_add_q WAVES=1

waves_mod_add:
	$(MAKE) -C tb/mod_add WAVES=1

waves_mod_sub:
	$(MAKE) -C tb/mod_sub WAVES=1

waves_ntt_butterfly:
	$(MAKE) -C tb/ntt_butterfly WAVES=1

waves_intt_butterfly:
	$(MAKE) -C tb/intt_butterfly WAVES=1

waves_ntt_rom:
	$(MAKE) -C tb/ntt_rom WAVES=1

waves_poly_ram:
	$(MAKE) -C tb/poly_ram WAVES=1

waves_ntt_engine:
	$(MAKE) -C tb/ntt_engine WAVES=1

clean:
	$(MAKE) -C tb/cond_sub_q clean
	$(MAKE) -C tb/barrett_reduce clean
	$(MAKE) -C tb/cond_add_q clean
	$(MAKE) -C tb/mod_add clean
	$(MAKE) -C tb/mod_sub clean
	$(MAKE) -C tb/ntt_butterfly clean
	$(MAKE) -C tb/intt_butterfly clean
	$(MAKE) -C tb/ntt_rom clean
	$(MAKE) -C tb/poly_ram clean
	$(MAKE) -C tb/ntt_engine clean
