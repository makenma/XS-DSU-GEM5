ENTRY(_start)

PHDRS
{
    load PT_LOAD FILEHDR PHDRS FLAGS(7);
    attributes 0x70000003 FLAGS(4); /* PT_RISCV_ATTRIBUTES */
}

SECTIONS
{
    . = 0x10000 + SIZEOF_HEADERS;
    .text : { *(.text.start) *(.text*) } :load
    .rodata : { *(.rodata*) } :load
    .data : { *(.data*) *(.sdata*) } :load
    .bss : { *(.bss*) *(.sbss*) *(COMMON) } :load
    __global_pointer$ = . + 0x800;
    .riscv.attributes : { *(.riscv.attributes) } :attributes
    /DISCARD/ : { *(.comment) *(.note*) }
}
