CROSS_COMPILE ?= riscv64-linux-gnu-
CFLAGS = -O3 -funroll-loops -static -march=rv64gc -mabi=lp64d -Wall -Wextra -I../../../src
simple_dma: simple_dma.c ../../../src/dev/simple_dma_regs.h Makefile
	$(CROSS_COMPILE)gcc $(CFLAGS) $< -o $@
clean:
	rm -f simple_dma
.PHONY: clean
