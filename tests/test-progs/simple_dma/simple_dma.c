#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "dev/simple_dma_regs.h"
static inline void fence(void) { __asm__ volatile("fence iorw,iorw" ::: "memory"); }
static inline uint64_t cycles(void) { uint64_t x; __asm__ volatile("rdcycle %0":"=r"(x)); return x; }
static void w64(unsigned o,uint64_t v) { *(volatile uint64_t *)(SD_MMIO_BASE+o)=v; }
static void w32(unsigned o,uint32_t v) { *(volatile uint32_t *)(SD_MMIO_BASE+o)=v; }
static uint64_t r64(unsigned o) { return *(volatile uint64_t *)(SD_MMIO_BASE+o); }
static uint32_t r32(unsigned o) { return *(volatile uint32_t *)(SD_MMIO_BASE+o); }
static uint8_t pattern(size_t i,unsigned seed) { return (i*37+(i>>8)*11+seed*73)^0x5b; }
static uint64_t timeout_count=0;
static unsigned poll_limit=2000000;
static int wait_done(uint64_t *elapsed,uint64_t start) {
    for(unsigned p=0;p<poll_limit;++p) {
        uint32_t s=r32(SD_STATUS);
        if(!(s&SD_BUSY)) {
            fence(); *elapsed=cycles()-start;
            if((s&SD_ERROR)||!(s&SD_DONE)) { printf("FAIL status=%u error=%u\n",s,r32(SD_ERROR_CODE)); return 1; }
            return 0;
        }
    }
    ++timeout_count; printf("FAIL timeout polls=%u timeouts=%llu\n",poll_limit,(unsigned long long)timeout_count); return 1;
}
static int copy(size_t n,unsigned so,unsigned doff,unsigned seed,uint64_t *elapsed) {
    volatile uint64_t *sp=(volatile uint64_t *)SD_BUFFER_VA;
    volatile uint64_t *dp=(volatile uint64_t *)(SD_BUFFER_VA+SD_DEST_OFFSET);
    size_t total=(n+(so>doff?so:doff)+128+7)&~(size_t)7;
    for(size_t i=0;i<total;i+=8) {
        uint64_t sw=0,dw=0;
        for(unsigned b=0;b<8;++b) {
            size_t j=i+b;
            uint8_t sv=j>=64+so && j<64+so+n ? pattern(j-64-so,seed):0x6d;
            sw|=(uint64_t)sv<<(8*b); dw|=(uint64_t)0xc9<<(8*b);
        }
        sp[i/8]=sw; dp[i/8]=dw;
    }
    if(n>=1048576) { printf("PROGRESS initialized length=%zu\n",n); fflush(stdout); }
    fence(); w32(SD_CONTROL,SD_CLEAR);
    w64(SD_SRC,SD_BUFFER_PA+64+so); w64(SD_DST,SD_BUFFER_PA+SD_DEST_OFFSET+64+doff);
    w64(SD_LENGTH,n); fence(); uint64_t start=cycles(); w32(SD_CONTROL,SD_START);
    if(wait_done(elapsed,start)) return 1;
    if(r64(SD_BYTES_DONE)!=n) { printf("FAIL bytes_done=%llu expected=%zu\n",(unsigned long long)r64(SD_BYTES_DONE),n); return 1; }
    if(n>=1048576) { printf("PROGRESS copied length=%zu cycles=%llu\n",n,(unsigned long long)*elapsed); fflush(stdout); }
    for(size_t i=0;i<total;i+=8) {
        uint64_t sw=sp[i/8],dw=dp[i/8];
        for(unsigned b=0;b<8;++b) {
            size_t j=i+b;
            uint8_t se=j>=64+so && j<64+so+n ? pattern(j-64-so,seed):0x6d;
            uint8_t de=j>=64+doff && j<64+doff+n ? pattern(j-64-doff,seed):0xc9;
            if(((sw>>(8*b))&255)!=se || ((dw>>(8*b))&255)!=de) {
                printf("FAIL length=%zu offset=%zu src=%u/%u dst=%u/%u\n",n,j,
                       (unsigned)((sw>>(8*b))&255),se,(unsigned)((dw>>(8*b))&255),de); return 1;
            }
        }
    }
    printf("PASS length=%zu src_offset=%u dst_offset=%u cycles=%llu\n",n,so,doff,(unsigned long long)*elapsed);
    return 0;
}
static int busy_test(void) {
    volatile uint64_t *sp=(volatile uint64_t *)SD_BUFFER_VA;
    volatile uint64_t *dp=(volatile uint64_t *)(SD_BUFFER_VA+SD_DEST_OFFSET);
    for(unsigned i=0;i<512;++i) { sp[i]=0x76543210abcdef00ull+i; dp[i]=0xc9c9c9c9c9c9c9c9ull; }
    fence(); w32(SD_CONTROL,SD_CLEAR); w64(SD_SRC,SD_BUFFER_PA);
    w64(SD_DST,SD_BUFFER_PA+SD_DEST_OFFSET); w64(SD_LENGTH,4096);
    fence(); w32(SD_CONTROL,SD_START);
    if(!(r32(SD_STATUS)&SD_BUSY)) return 1;
    w64(SD_SRC,0); w64(SD_DST,0); w64(SD_LENGTH,0);
    w32(SD_CONTROL,SD_CLEAR); w32(SD_CONTROL,SD_START);
    if(r32(SD_ERROR_CODE)!=SD_ERR_BUSY) return 1;
    unsigned polls=0;
    while(r32(SD_STATUS)&SD_BUSY) if(++polls==2000000) return 1;
    if(r64(SD_BYTES_DONE)!=4096 || r32(SD_STATUS)!=(SD_DONE|SD_ERROR)) return 1;
    fence();
    for(unsigned i=0;i<512;++i) if(dp[i]!=0x76543210abcdef00ull+i || sp[i]!=dp[i]) return 1;
    w32(SD_CONTROL,SD_CLEAR); w32(SD_CONTROL,SD_START);
    if(r32(SD_STATUS)!=SD_DONE || r64(SD_BYTES_DONE)) return 1;
    printf("PASS busy_start snapshot clear_while_busy reuse\n"); return 0;
}
static int error_tests(void) {
    w32(SD_CONTROL,SD_CLEAR); w64(SD_SRC,~0ull-2); w64(SD_DST,SD_BUFFER_PA); w64(SD_LENGTH,8); w32(SD_CONTROL,SD_START);
    if(r32(SD_STATUS)!=SD_ERROR || r32(SD_ERROR_CODE)!=SD_ERR_OVERFLOW) return 1;
    w32(SD_CONTROL,SD_CLEAR); w64(SD_SRC,SD_BUFFER_PA); w64(SD_DST,SD_BUFFER_PA+1); w64(SD_LENGTH,8); w32(SD_CONTROL,SD_START);
    if(r32(SD_ERROR_CODE)!=SD_ERR_OVERLAP) return 1;
    w32(SD_CONTROL,SD_CLEAR); w64(SD_DST,0); w32(SD_CONTROL,SD_START);
    if(r32(SD_ERROR_CODE)!=SD_ERR_ADDRESS) return 1;
    w32(SD_CONTROL,SD_CLEAR); w64(SD_LENGTH,0); w32(SD_CONTROL,SD_START);
    if(r32(SD_STATUS)!=SD_DONE || r64(SD_BYTES_DONE)) return 1;
    w32(SD_CONTROL,SD_CLEAR); w32(SD_SCRATCH,0x01234567); w32(SD_SCRATCH+4,0x89abcdef);
    if(r64(SD_SCRATCH)!=0x89abcdef01234567ull) return 1;
    printf("PASS overflow overlap address zero halfword\n"); return busy_test();
}
int main(int argc,char **argv) {
    // Exercise both compressed halfwords and a 32-bit instruction straddling
    // an aligned fetch word, on the actual CPU used by this acceptance test.
    unsigned decoded;
    __asm__ volatile(".balign 4\n.option push\n.option rvc\nc.nop\n"
                     ".option norvc\naddi %0,zero,123\n"
                     ".option rvc\nc.nop\n.option pop\n" : "=r"(decoded));
    if(decoded!=123) { printf("FAIL compressed/straddling instruction fetch\n"); return 1; }
    const char *mode=argc>1?argv[1]:"dma_copy";
    size_t n=argc>2?strtoull(argv[2],0,0):64;
    unsigned iterations=argc>3?strtoul(argv[3],0,0):1;
    if(argc>4) poll_limit=strtoul(argv[4],0,0);
    if(n>1048576 || !iterations || !poll_limit) return 2;
    uint64_t elapsed=0,sum=0;
    if(!strcmp(mode,"errors")) return error_tests();
    if(!strcmp(mode,"dma_error")) {
        w32(SD_CONTROL,SD_CLEAR); w64(SD_SRC,SD_BUFFER_PA);
        w64(SD_DST,SD_BUFFER_PA+SD_DEST_OFFSET); w64(SD_LENGTH,129);
        w32(SD_CONTROL,SD_START);
        unsigned polls=0;
        while(r32(SD_STATUS)&SD_BUSY) if(++polls==poll_limit) return 1;
        if(r32(SD_STATUS)!=SD_ERROR || r32(SD_ERROR_CODE)!=SD_ERR_DMA || r64(SD_BYTES_DONE)) return 1;
        printf("PASS dma_error drained failed_job\n"); return 0;
    }
    if(!strcmp(mode,"mmio")) {
        w64(SD_SCRATCH,0x89abcdef01234567ull); fence(); uint64_t start=cycles();
        for(unsigned i=0;i<iterations;++i) if(r64(SD_SCRATCH)!=0x89abcdef01234567ull) return 1;
        elapsed=cycles()-start;
        printf("BENCH,mmio_read,8,%u,%llu\n",iterations,(unsigned long long)elapsed);
        start=cycles();
        for(unsigned i=0;i<iterations;++i) { w64(SD_SCRATCH,i^0xabcdu); fence(); if(r64(SD_SCRATCH)!=(i^0xabcdu)) return 1; }
        printf("BENCH,mmio_write_read,8,%u,%llu\n",iterations,(unsigned long long)(cycles()-start));
        printf("PASS mmio iterations=%u\n",iterations); return 0;
    }
    if(!strcmp(mode,"mixed")) {
        if(copy(n,3,29,1,&elapsed)) return 1;
        uint64_t start=cycles(),latencies=0;
        for(unsigned i=0;i<iterations;++i) {
            uint64_t t=cycles(); w32(SD_CONTROL,SD_CLEAR); fence(); w32(SD_CONTROL,SD_START);
            if(wait_done(&elapsed,t)) return 1;
            latencies+=elapsed;
        }
        uint64_t total_cycles=cycles()-start;
        volatile uint64_t *sp=(volatile uint64_t *)SD_BUFFER_VA;
        volatile uint64_t *dp=(volatile uint64_t *)(SD_BUFFER_VA+SD_DEST_OFFSET);
        size_t total=(n+29+128+7)&~(size_t)7;
        for(size_t i=0;i<total;i+=8) {
            uint64_t sw=sp[i/8],dw=dp[i/8];
            for(unsigned b=0;b<8;++b) {
                size_t j=i+b;
                uint8_t se=j>=67 && j<67+n ? pattern(j-67,1):0x6d;
                uint8_t de=j>=93 && j<93+n ? pattern(j-93,1):0xc9;
                if(((sw>>(8*b))&255)!=se || ((dw>>(8*b))&255)!=de) {
                    printf("FAIL mixed offset=%zu\n",j); return 1;
                }
            }
        }
        printf("BENCH,mixed,%zu,%u,%llu,%llu\n",n,iterations,
               (unsigned long long)total_cycles,(unsigned long long)latencies);
        printf("PASS mixed tasks=%u\n",iterations); return 0;
    }
    if(!strcmp(mode,"regression")) {
        size_t lengths[]={1,3,31,33,63,64,65,127,129,4096,1048576};
        for(unsigned i=0;i<sizeof(lengths)/sizeof(*lengths);++i)
            if(copy(lengths[i],3,29,i+1,&elapsed)) return 1;
        return error_tests();
    }
    for(unsigned i=0;i<iterations;++i) {
        if(copy(n,3,29,i+1,&elapsed)) return 1;
        sum+=elapsed;
    }
    printf("BENCH,%s,%zu,%u,%llu\n",mode,n,iterations,(unsigned long long)sum);
    printf("PASS mode=%s timeouts=%llu\n",mode,(unsigned long long)timeout_count); return 0;
}
