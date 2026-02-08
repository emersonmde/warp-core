.PHONY: test test_cond_sub_q test_barrett_reduce clean
.PHONY: waves_cond_sub_q waves_barrett_reduce

test: test_cond_sub_q test_barrett_reduce

test_cond_sub_q:
	$(MAKE) -C tb/cond_sub_q

test_barrett_reduce:
	$(MAKE) -C tb/barrett_reduce

# Waveform dumps — produces FST files viewable in GTKWave
waves_cond_sub_q:
	$(MAKE) -C tb/cond_sub_q WAVES=1

waves_barrett_reduce:
	$(MAKE) -C tb/barrett_reduce WAVES=1

clean:
	$(MAKE) -C tb/cond_sub_q clean
	$(MAKE) -C tb/barrett_reduce clean
