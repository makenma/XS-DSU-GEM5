"""Checked patches for Zicbom CSRs and translation on the pinned gem5 tree.

All substitutions are made against the immutable baseline. The installer
separately validates the current checkout and preserves observer composition.
"""
from pathlib import Path


def replace(source, old, new, count=1):
    if source.count(old) != count:
        raise RuntimeError(f'Unexpected pinned CBO patch anchor ({count}): {old[:100]}')
    return source.replace(old, new)


def patches(baseline):
    out = {}
    root = 'src/arch/riscv/'
    out[root + 'cbo.hh'] = Path(__file__).with_name('RiscvCbo.hh').read_text(encoding='utf-8')
    p = root + 'isa/includes.isa'
    out[p] = replace(baseline(p), '#include "arch/generic/memhelpers.hh"\n#include "arch/riscv/faults.hh"',
        '#include "arch/generic/memhelpers.hh"\n#include "arch/riscv/cbo.hh"\n#include "arch/riscv/faults.hh"')
    p = root + 'regs/misc.hh'
    s = baseline(p)
    s = replace(s, '    NUM_MISCREGS',
        '    // Appended to preserve all existing checkpoint register indices.\n'
        '    MISCREG_SENVCFG,\n    NUM_MISCREGS')
    s = replace(s, '    CSR_SCOUNTEREN = 0x106,',
        '    CSR_SCOUNTEREN = 0x106,\n    CSR_SENVCFG = 0x10A,')
    s = replace(s, '    {CSR_SCOUNTEREN, {"scounteren", MISCREG_SCOUNTEREN}},',
        '    {CSR_SCOUNTEREN, {"scounteren", MISCREG_SCOUNTEREN}},\n'
        '    {CSR_SENVCFG, {"senvcfg", MISCREG_SENVCFG}},')
    out[p] = s
    p = root + 'isa.cc'
    s = baseline(p)
    s = replace(s, '#include "arch/riscv/isa.hh"',
        '#include "arch/riscv/isa.hh"\n#include "arch/riscv/cbo.hh"')
    s = replace(s, '    [MISCREG_NMIP]          = "NMIP",',
        '    [MISCREG_NMIP]          = "NMIP",\n    [MISCREG_SENVCFG]       = "SENVCFG",')
    s = replace(s, 'ISA::setMiscReg(int misc_reg, RegVal val)\n{',
        '''ISA::setMiscReg(int misc_reg, RegVal val)
{
    // Each xenvcfg has independent WARL storage; do not mask h/s values by
    // the corresponding higher-privilege CSR. Unsupported Zicboz stays zero.
    if (misc_reg == MISCREG_MENVCFG || misc_reg == MISCREG_HENVCFG ||
        misc_reg == MISCREG_SENVCFG) {
        setMiscRegNoEffect(misc_reg, Cbo::legalizeEnvcfg(val, misc_reg == MISCREG_SENVCFG));
        return;
    }''')
    out[p] = s
    p = root + 'tlb.hh'
    s = baseline(p)
    s = replace(s, 'Addr gpaddr, bool G);', 'Addr gpaddr, bool G, bool cacheMaintenance = false);')
    s = replace(s, 'bool h_inst);', 'bool h_inst, bool cacheMaintenance = false);')
    out[p] = s
    p = root + 'tlb.cc'
    s = baseline(p)
    s = replace(s, '#include "arch/riscv/tlb.hh"',
        '#include "arch/riscv/tlb.hh"\n#include "arch/riscv/cbo.hh"')
    s = replace(s, 'BaseMMU::Mode mode, PTE pte,Addr gpaddr,bool G)',
        'BaseMMU::Mode mode, PTE pte,Addr gpaddr,bool G,bool cacheMaintenance)')
    s = replace(s, 'BaseMMU::Mode mode, PTE pte,bool h_inst){',
        'BaseMMU::Mode mode, PTE pte,bool h_inst,bool cacheMaintenance){')
    s = replace(s, '    if (mode == BaseMMU::Read && !pte.r) {',
        '    if (cacheMaintenance && !pte.a) {\n'
        '        fault = createPagefault(vaddr, gpaddr, mode, G);\n'
        '    } else if (mode == BaseMMU::Read && !pte.r) {')
    s = replace(s, '    } else if (!pte.u) {',
        '    } else if (cacheMaintenance && !pte.a) {\n'
        '        return std::make_pair(continuePtw,createPagefault(vaddr, gpaddr, mode, true));\n'
        '    } else if (!pte.u) {')
    s = replace(s, 'mode == BaseMMU::Write && !pte.w',
        'mode == BaseMMU::Write && (cacheMaintenance ?\n'
        '        !Cbo::accessible(pte.r, pte.w, pte.x, status.mxr) : !pte.w)')
    s = replace(s, '(mode == BaseMMU::Write) && !(pte.r && pte.w)',
        '(mode == BaseMMU::Write) && (cacheMaintenance ?\n'
        '        !Cbo::accessible(pte.r, pte.w, pte.x, status.mxr) : !(pte.r && pte.w))')
    # All permission call sites have their original Request available. Keep
    # mode=Write for MPRV/SUM handling and store-class exception numbers.
    s = replace(s, 'checkPermissions(status, pmode, vaddr, mode, pte, 0, false)',
        'checkPermissions(status, pmode, vaddr, mode, pte, 0, false, req->isCacheMaintenance())')
    s = replace(s, 'checkPermissions(status, pmode, vaddr, mode, e[0]->pteVS, 0, false)',
        'checkPermissions(status, pmode, vaddr, mode, e[0]->pteVS, 0, false, req->isCacheMaintenance())')
    s = replace(s, 'checkPermissions(status, pmode, vaddr, mode, e[0]->pte, 0, false)',
        'checkPermissions(status, pmode, vaddr, mode, e[0]->pte, 0, false, req->isCacheMaintenance())', 2)
    s = replace(s, 'mode,e[0]->pte,req->get_h_inst())',
        'mode,e[0]->pte,req->get_h_inst(),req->isCacheMaintenance())')
    s = replace(s, 'mode, e[0]->pte, req->get_h_inst())',
        'mode, e[0]->pte, req->get_h_inst(), req->isCacheMaintenance())', 3)
    s = replace(s, '!pte.d && mode == BaseMMU::Write',
        '!pte.d && mode == BaseMMU::Write && !req->isCacheMaintenance()')
    s = replace(s, 'mode == BaseMMU::Write && !e[0]->pte.d',
        'mode == BaseMMU::Write && !e[0]->pte.d && !req->isCacheMaintenance()', 2)
    s = replace(s, 'mode == BaseMMU::Write && !e[0]->pteVS.d',
        'mode == BaseMMU::Write && !e[0]->pteVS.d && !req->isCacheMaintenance()')
    out[p] = s
    p = root + 'pagetable_walker.cc'
    s = baseline(p)
    s = replace(s, '#include "arch/riscv/pagetable_walker.hh"',
        '#include "arch/riscv/pagetable_walker.hh"\n#include "arch/riscv/cbo.hh"')
    # A normal store cannot borrow a CBO walk's relaxed W/D checks.
    s = replace(s, 'model_match = (mainReq->get_two_stage_state() == req->get_two_stage_state()) &&',
        'model_match = (mainReq->isCacheMaintenance() == req->isCacheMaintenance()) &&\n'
        '                  (mainReq->get_two_stage_state() == req->get_two_stage_state()) &&')
    s = replace(s, 'checkPermissions(vsstatus, pmode, entry.vaddr, mode, pte, 0, false)',
        'checkPermissions(vsstatus, pmode, entry.vaddr, mode, pte, 0, false, mainReq->isCacheMaintenance())')
    s = replace(s, 'checkPermissions(status, pmode, entry.vaddr, mode, pte, 0, false)',
        'checkPermissions(status, pmode, entry.vaddr, mode, pte, 0, false, mainReq->isCacheMaintenance())', 2)
    s = replace(s, '(mode == BaseMMU::Write) && !(pte.r && pte.w)',
        '(mode == BaseMMU::Write) && (mainReq->isCacheMaintenance() ?\n'
        '            !Cbo::accessible(pte.r, pte.w, pte.x, status.mxr) : !(pte.r && pte.w))')
    s = replace(s, '        } else if (!pte.u) {',
        '        } else if (mainReq->isCacheMaintenance() && !pte.a) {\n'
        '            endWalk();\n'
        '            GstageFault = true;\n'
        '            fault = pageFault(true, true);\n'
        '            return fault;\n'
        '        } else if (!pte.u) {')
    s = replace(s, '((!pte.d) && (mode == BaseMMU::Write))',
        '((!pte.d) && (mode == BaseMMU::Write) && !mainReq->isCacheMaintenance())')
    s = replace(s, '!pte.d && mode == BaseMMU::Write',
        '!pte.d && mode == BaseMMU::Write && !mainReq->isCacheMaintenance()')
    s = replace(s, 'walker->pmp->pmpCheck(r.req, mode, pmode, r.tc);',
        '''walker->pmp->pmpCheck(r.req, mode, pmode, r.tc);
                    if (mainFault != NoFault && r.req->isCacheMaintenance()) {
                        // CBO permission denial is an architectural access
                        // fault, not the old walker's physical-overflow panic.
                        // Finish every coalesced request and retire this walk.
                        r.translation->finish(mainFault, r.req, r.tc, mode);
                        mainFault = NoFault;
                        continue;
                    }''', 3)
    out[p] = s
    p = root + 'pmp.cc'
    s = baseline(p)
    s = replace(s, '(PMP_WRITE & allowed_privs))',
        '((PMP_WRITE | (req->isCacheMaintenance() ? PMP_READ : 0)) & allowed_privs))')
    # Check the actual last byte for CBO: a valid rs1 at a PMP boundary must
    # not fault because a fictitious following byte belongs to another region.
    s = replace(s, 'pmp_range.contains(req->getPaddr() + req->getSize()))',
        'pmp_range.contains(req->getPaddr() + req->getSize() -\n'
        '                    (req->isCacheMaintenance() ? 1 : 0)))')
    out[p] = s
    return out
