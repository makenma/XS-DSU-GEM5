ENTRY(_start)

/*
 * Linux maps PT_LOAD segments at page granularity.  Keep the ELF and program
 * headers inside the load segment so file offset zero is congruent with the
 * 0x10000 virtual address.  Placing a tiny segment at file offset 0xb0 with
 * p_align=2 makes Linux execute the ELF header instead of .text.
 */
PHDRS
{
    text PT_LOAD FILEHDR PHDRS FLAGS(7);
    attributes 0x70000003 FLAGS(4); /* PT_RISCV_ATTRIBUTES */
}

SECTIONS
{
    . = 0x10000 + SIZEOF_HEADERS;
    .text : { *(.text) } :text
    .riscv.attributes : { *(.riscv.attributes) } :attributes
    /DISCARD/ : { *(.comment) }
}
