#ifndef SIMPLE_DMA_REGS_H
#define SIMPLE_DMA_REGS_H
/* Shared guest C / device C++ ABI, little endian. */
#define SD_SRC 0x00
#define SD_DST 0x08
#define SD_LENGTH 0x10
#define SD_CONTROL 0x18
#define SD_STATUS 0x1c
#define SD_BYTES_DONE 0x20
#define SD_ERROR_CODE 0x28
#define SD_SCRATCH 0x30
#define SD_START 1u
#define SD_CLEAR 2u
#define SD_BUSY 1u
#define SD_DONE 2u
#define SD_ERROR 4u
#define SD_ERR_BUSY 1u
#define SD_ERR_OVERFLOW 2u
#define SD_ERR_OVERLAP 3u
#define SD_ERR_ADDRESS 4u
#define SD_ERR_ACCESS 5u
#define SD_ERR_DMA 6u
#define SD_MMIO_BASE 0x40000000ull
#define SD_BUFFER_VA 0x50000000ull
#define SD_BUFFER_PA 0x10000000ull
#define SD_BUFFER_SIZE 0x400000ull
#define SD_DEST_OFFSET 0x200000ull
#endif
