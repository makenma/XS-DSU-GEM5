// Exercise finite HN entries, same-address competition and real dirty victims.
typedef unsigned long long u64;
static volatile u64* const shared=(volatile u64*)0x100000000ULL;
static volatile u64* const control=(volatile u64*)0x200000000ULL;
static inline void fence(void){__asm__ volatile("fence rw,rw" ::: "memory");}
static inline void pause(void){__asm__ volatile(".rept 8\n nop\n .endr" ::: "memory");}
void _start(void) {
    for(unsigned i=0;i<8;++i) {shared[(i*4+HART)*8]=1+HART*1000+i;fence();}
    shared[1536+HART]=1;fence();
    for(unsigned hart=0;hart<4;++hart)while(!shared[1536+hart])pause();
    unsigned errors=0;
    for(unsigned i=0;i<8;++i)for(unsigned hart=0;hart<4;++hart) {
        if(shared[(i*4+hart)*8]!=1+hart*1000+i)++errors;
    }
    fence();control[128]=errors;control[256]=errors?2:1;
    for(;;)__asm__ volatile("nop");
}
