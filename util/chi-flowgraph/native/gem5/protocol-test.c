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
static void owner(unsigned phase,unsigned line,u64 value) {
    shared[402]=line;shared[403]=value;fence();shared[400]=phase;fence();
    while(shared[401]!=phase) {wait_hint();}
    fence();
}
void _start(void) {
#if HART == 0
    unsigned n=0;
    control[128+n++]=chi(0x03,0,0,64,~0ULL,0,0); // ReadOnce: no allocation.
    control[128+n++]=chi(0x04,1,0,8,~0ULL,0,0); // ReadNoSnp: small natural access.
    control[128+n++]=chi(0x07,2,0,64,~0ULL,0,0); // ReadUnique installs UC.
    chi(0x08,2,0,64,~0ULL,0,0); // CleanShared.
    chi(0x09,2,0,64,~0ULL,0,0); // CleanInvalid.
    write_value(3,3);chi(0x0a,3,0,64,~0ULL,0,0); // MakeInvalid permits discard.
    control[144]=chi(0x04,3,0,8,~0ULL,0,0); // Old memory remains zero after MakeInvalid.
    control[128+n++]=read_value(4);chi(0x0c,4,0,64,~0ULL,0,0); // MakeUnique: the requester guarantees full overwrite.
    for(unsigned word=0;word<8;++word)shared[4*8+word]=44;fence();
    control[128+n++]=read_value(5);chi(0x0d,5,0,64,~0ULL,0,0); // Evict real clean line.
    chi(0x07,6,0,64,~0ULL,0,0);chi(0x15,6,0,64,~0ULL,0,0); // WriteEvictFull UC.
    chi(0x1d,7,0,64,~0ULL,0xaaaaaaaaaaaaaaaaULL,0);
    chi(0x18,7,8,8,0x55,0x1122334455667788ULL,0); // WriteUniquePtl, alternate BE.
    control[128+n++]=chi(0x03,7,0,64,~0ULL,0,0);
    control[128+n++]=control[65]; // Expected 0xaa22aa44aa66aa88.
    chi(0x19,8,0,64,~0ULL,0x123456789abcdef0ULL,0); // WriteUniqueFull.
    control[128+n++]=chi(0x03,8,0,64,~0ULL,0,0);
    write_value(9,0x987654321ULL);chi(0x1b,9,0,64,~0ULL,0,0); // WriteBackFull takes L1 dirty data.
    control[128+n++]=chi(0x03,9,0,64,~0ULL,0,0);
    chi(0x1d,10,0,64,~0ULL,0xaaaaaaaaaaaaaaaaULL,0);
    chi(0x1c,10,8,8,0x55,0x1122334455667788ULL,0); // WriteNoSnpPtl preserves memory BE=0 bytes.
    control[128+n++]=chi(0x04,10,8,8,~0ULL,0,0);
    chi(0x1d,11,0,64,~0ULL,42,0); // WriteNoSnpFull.
    chi(0x22,11,0,64,~0ULL,0,256); // StashOnceShared: actual target DataPull.
    owner(1,11,0); // P1 reads stashed line; no target RN REQ is expected.
    chi(0x1d,12,0,64,~0ULL,43,0);
    chi(0x23,12,0,64,~0ULL,0,256); // StashOnceUnique.
    owner(2,12,0);
    chi(0x22,13,0,64,~0ULL,0,272); // RN3 intentionally rejects stash hint.
    owner(3,14,0x1111);
    control[128+n++]=chi(0x24,14,0,64,~0ULL,0,0); // ReadOnceCleanInvalid returns latest and writes back.
    control[128+n++]=chi(0x04,14,0,8,~0ULL,0,0); // Memory must now contain 0x1111.
    owner(4,15,0x2222);
    control[128+n++]=chi(0x25,15,0,64,~0ULL,0,0); // ReadOnceMakeInvalid returns latest, discards dirty copy.
    control[128+n++]=chi(0x04,15,0,8,~0ULL,0,0); // Memory remains its old zero.
    owner(5,16,0x3333);
    control[128+n++]=chi(0x26,16,0,64,~0ULL,0,0); // ReadNotSharedDirty: requesting RN cannot become SD.
    write_value(17,0x4444);chi(0x08,17,0,64,~0ULL,0,0); // Dirty CleanShared must preserve latest data.
    control[145]=chi(0x04,17,0,8,~0ULL,0,0);control[146]=read_value(17);
    write_value(18,0x5555);chi(0x09,18,0,64,~0ULL,0,0); // Dirty CleanInvalid writes back and invalidates.
    control[147]=chi(0x04,18,0,8,~0ULL,0,0);
    fence();shared[400]=99;fence();done(1);
#elif HART == 1
    unsigned phase=0;
    for(;;) {
        unsigned next=shared[400];if(next==phase){wait_hint();continue;}if(next==99)done(1);
        const unsigned line=shared[402];const u64 value=shared[403];
        if(next<=2)control[127+next]=read_value(line);else write_value(line,value);
        fence();shared[401]=next;fence();phase=next;
    }
#else
    while(shared[400]!=99) {wait_hint();}done(1);
#endif
}
