.PHONY: test clean
.PHONY: test_cond_sub_q test_barrett_reduce test_cond_add_q test_mod_add test_mod_sub test_ntt_butterfly
.PHONY: test_intt_butterfly test_ntt_rom test_poly_ram test_ntt_engine test_basemul_unit test_poly_basemul
.PHONY: test_compress test_decompress test_poly_addsub test_cbd_sampler test_kyber_top test_encaps_top
.PHONY: test_keygen_top test_decaps_top test_keccak_sponge test_auto_keygen
.PHONY: test_acvp_oracle test_acvp_keygen test_acvp_encaps test_acvp_decaps test_acvp
.PHONY: waves_cond_sub_q waves_barrett_reduce waves_cond_add_q waves_mod_add waves_mod_sub waves_ntt_butterfly
.PHONY: waves_intt_butterfly waves_ntt_rom waves_poly_ram waves_ntt_engine waves_basemul_unit waves_poly_basemul
.PHONY: waves_compress waves_decompress waves_poly_addsub waves_cbd_sampler waves_kyber_top waves_encaps_top
.PHONY: waves_keygen_top waves_decaps_top waves_keccak_sponge waves_auto_keygen

test: test_cond_sub_q test_barrett_reduce test_cond_add_q test_mod_add test_mod_sub test_ntt_butterfly \
      test_intt_butterfly test_ntt_rom test_poly_ram test_ntt_engine test_basemul_unit test_poly_basemul \
      test_compress test_decompress test_poly_addsub test_cbd_sampler test_kyber_top test_encaps_top \
      test_keygen_top test_decaps_top test_keccak_sponge test_auto_keygen \
      test_acvp_oracle test_acvp_keygen test_acvp_encaps test_acvp_decaps

# ACVP compliance tests (NIST FIPS 203 test vectors)
test_acvp: test_acvp_oracle test_acvp_keygen test_acvp_encaps test_acvp_decaps

test_acvp_oracle:
	python ref/test_acvp_oracle.py

test_acvp_keygen:
	$(MAKE) -C tb/acvp_keygen

test_acvp_encaps:
	$(MAKE) -C tb/acvp_encaps

test_acvp_decaps:
	$(MAKE) -C tb/acvp_decaps

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

test_basemul_unit:
	$(MAKE) -C tb/basemul_unit

test_poly_basemul:
	$(MAKE) -C tb/poly_basemul

test_compress:
	$(MAKE) -C tb/compress

test_decompress:
	$(MAKE) -C tb/decompress

test_poly_addsub:
	$(MAKE) -C tb/poly_addsub

test_cbd_sampler:
	$(MAKE) -C tb/cbd_sampler

test_kyber_top:
	$(MAKE) -C tb/kyber_top

test_encaps_top:
	$(MAKE) -C tb/encaps_top

test_keygen_top:
	$(MAKE) -C tb/keygen_top

test_decaps_top:
	$(MAKE) -C tb/decaps_top

test_keccak_sponge:
	$(MAKE) -C tb/keccak_sponge

test_auto_keygen:
	$(MAKE) -C tb/auto_keygen

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

waves_basemul_unit:
	$(MAKE) -C tb/basemul_unit WAVES=1

waves_poly_basemul:
	$(MAKE) -C tb/poly_basemul WAVES=1

waves_compress:
	$(MAKE) -C tb/compress WAVES=1

waves_decompress:
	$(MAKE) -C tb/decompress WAVES=1

waves_poly_addsub:
	$(MAKE) -C tb/poly_addsub WAVES=1

waves_cbd_sampler:
	$(MAKE) -C tb/cbd_sampler WAVES=1

waves_kyber_top:
	$(MAKE) -C tb/kyber_top WAVES=1

waves_encaps_top:
	$(MAKE) -C tb/encaps_top WAVES=1

waves_keygen_top:
	$(MAKE) -C tb/keygen_top WAVES=1

waves_decaps_top:
	$(MAKE) -C tb/decaps_top WAVES=1

waves_keccak_sponge:
	$(MAKE) -C tb/keccak_sponge WAVES=1

waves_auto_keygen:
	$(MAKE) -C tb/auto_keygen WAVES=1

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
	$(MAKE) -C tb/basemul_unit clean
	$(MAKE) -C tb/poly_basemul clean
	$(MAKE) -C tb/compress clean
	$(MAKE) -C tb/decompress clean
	$(MAKE) -C tb/poly_addsub clean
	$(MAKE) -C tb/cbd_sampler clean
	$(MAKE) -C tb/kyber_top clean
	$(MAKE) -C tb/encaps_top clean
	$(MAKE) -C tb/keygen_top clean
	$(MAKE) -C tb/decaps_top clean
	$(MAKE) -C tb/keccak_sponge clean
	$(MAKE) -C tb/auto_keygen clean
	$(MAKE) -C tb/acvp_keygen clean
	$(MAKE) -C tb/acvp_encaps clean
	$(MAKE) -C tb/acvp_decaps clean
