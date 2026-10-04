// Freestanding CPU test. The uncached control page submits explicit CHI intents.
// Cache copybacks use real L1/L2 data. All normal accesses use the cache hierarchy.
typedef unsigned long long u64;
static volatile u64* const control=(volatile u64*)0x200000000ULL;
static volatile u64* const shared=(volatile u64*)0x100000000ULL;
static inline void fence(void) { __asm__ volatile("fence rw,rw" ::: "memory"); }
static inline void wait_hint(void) { __asm__ volatile(".rept 64\n nop\n .endr" ::: "memory"); }
static void done(u64 value) { fence();control[256]=value;for(;;)__asm__ volatile("nop"); }
static u64 read_value(unsigned line) { return shared[line*8]; }
static void write_value(unsigned line,u64 value) { shared[line*8]=value;fence(); }
__attribute__((noinline)) static u64 chi(unsigned opcode,unsigned line,unsigned offset,unsigned size,u64 be,u64 payload,unsigned stash) {
    control[6]=(u64)__builtin_return_address(0)-4; // Measured caller JAL PC; RV64 without compressed instructions.
    control[0]=opcode;
    control[1]=control[5]+line*64+offset;
    control[2]=size;control[3]=be;control[4]=stash;
    for(unsigned i=0;i<8;++i)control[8+i]=payload;
    fence();
    control[32]=1; // Actual CPU doorbell instruction is the CHI request cause.
    while(control[33]==1) {wait_hint();}
    if(control[33]!=2) {control[128]=opcode;control[129]=control[34];done(2);}
    fence();return control[64];
}
// All target requests start with an SLC miss. Prerequisite reads are preparation.
// chi() is the real uncached CPU test-page ABI defined above, not a C instruction.
void _start(void) {
#if HART == 0
    unsigned n=0;
    control[128+n++]=chi(0x03,0,0,64,~0ULL,0,0); // ReadOnce
    control[128+n++]=chi(0x04,1,0,8,~0ULL,0,0); // ReadNoSnp
    control[128+n++]=chi(0x07,2,0,64,~0ULL,0,0); // ReadUnique
    chi(0x08,3,0,64,~0ULL,0,0); // CleanShared: already clean/absent
    chi(0x09,4,0,64,~0ULL,0,0); // CleanInvalid: already absent
    chi(0x0a,5,0,64,~0ULL,0,0); // MakeInvalid: already absent
    chi(0x07,6,0,64,~0ULL,0,0); // Prepare RN UC; HNF has no SLC data
    chi(0x0c,6,0,64,~0ULL,0,0); // MakeUnique; full overwrite guaranteed
    for(unsigned word=0;word<8;++word)shared[6*8+word]=66;fence();
    chi(0x07,7,0,64,~0ULL,0,0); // Prepare a real clean RN line
    chi(0x0d,7,0,64,~0ULL,0,0); // Evict
    chi(0x07,8,0,64,~0ULL,0,0); // Prepare RN UC for copyback
    chi(0x15,8,0,64,~0ULL,0,0); // WriteEvictFull
    chi(0x1d,9,0,64,~0ULL,0xaaaaaaaaaaaaaaaaULL,0); // Initialize memory only
    chi(0x18,9,8,8,0x55,0x1122334455667788ULL,0); // WriteUniquePtl
    chi(0x03,9,0,64,~0ULL,0,0);control[144]=control[65];
    chi(0x19,10,0,64,~0ULL,0x123456789abcdef0ULL,0); // WriteUniqueFull
    write_value(11,0x987654321ULL); // Prepare actual dirty L1 data
    chi(0x1b,11,0,64,~0ULL,0,0); // WriteBackFull
    control[145]=chi(0x03,11,0,64,~0ULL,0,0);
    chi(0x1d,12,0,64,~0ULL,0xaaaaaaaaaaaaaaaaULL,0); // Initialize memory only
    chi(0x1c,12,8,8,0x55,0x1122334455667788ULL,0); // WriteNoSnpPtl
    control[146]=chi(0x04,12,8,8,~0ULL,0,0);
    chi(0x1d,13,0,64,~0ULL,42,0); // WriteNoSnpFull
    chi(0x1d,14,0,64,~0ULL,42,0);
    chi(0x22,14,0,64,~0ULL,0,256); // StashOnceShared, RN1 DataPull
    chi(0x1d,15,0,64,~0ULL,43,0);
    chi(0x23,15,0,64,~0ULL,0,256); // StashOnceUnique, RN1 DataPull
    control[128+n++]=chi(0x24,16,0,64,~0ULL,0,0); // ReadOnceCleanInvalid
    control[128+n++]=chi(0x25,17,0,64,~0ULL,0,0); // ReadOnceMakeInvalid
    control[128+n++]=chi(0x26,18,0,64,~0ULL,0,0); // ReadNotSharedDirty
    control[128+n++]=chi(0x01,19,0,64,~0ULL,0,0); // Existing ReadShared
    done(1);
#else
    // CPU is idle, but its RN/L2 still responds to snoops and stash DataPull.
    done(1);
#endif
}
