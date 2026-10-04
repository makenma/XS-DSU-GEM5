"""Checked transformations against the pinned gem5 and observer versions."""
def replace(s, old, new, count=1):
    assert s.count(old) == count, (old[:100], s.count(old), count)
    return s.replace(old, new)

def patches(gem5, baseline):
    prefix = 'src/mem/cache/CHI/'
    result = {}
    def read(name):
        return (gem5 / prefix / name).read_text()

    s = read('base/ChiChannel.hh')
    s = replace(s, '        uint64_t address = 0;', '        uint64_t address = 0;\n        std::vector<std::string> causeInstructionIds, dataProducerInstructionIds;')
    s = replace(s, '        uint32_t    ReturnTxnId = 0;', '        uint32_t    ReturnTxnId = 0;\n        uint32_t    stashNid = 0;')
    s = replace(s, '    struct RawRsp:BaseFlit\n    {', '    struct RawRsp:BaseFlit\n    {\n        FlowGraphIdentity pullFlow;')
    result[prefix+'base/ChiChannel.hh'] = s

    s = baseline(prefix+'HomeNodeFull.py')
    s += '\n    sn_hash_masks = VectorParam.UInt64([], "XOR parity masks selecting the SN target")\n'
    result[prefix+'HomeNodeFull.py'] = s
    s = read('HnfCoherencyController.hh')
    s = replace(s, '    void setSlcsf(HnfSLCSF* slcsf) { slcsfUnit = slcsf; }', '''    void setSlcsf(HnfSLCSF* slcsf) { slcsfUnit = slcsf; }
    void setSnHashMasks(const std::vector<uint64_t>& masks) { snHashMasks = masks; }''')
    s = replace(s, '    uint32_t snInterleaveShift = 6;', '    uint32_t snInterleaveShift = 6;\n    std::vector<uint64_t> snHashMasks;')
    s = replace(s, '        return snNodeIds[(addr >> snInterleaveShift) & (snNodeIds.size() - 1)];', '''        if (snHashMasks.empty()) return snNodeIds[(addr >> snInterleaveShift) & (snNodeIds.size() - 1)];
        unsigned index = 0;
        for (unsigned bit = 0; bit < snHashMasks.size(); ++bit)
            index |= (__builtin_parityll(addr & snHashMasks[bit]) << bit);
        return snNodeIds.at(index);''')
    result[prefix+'HnfCoherencyController.hh'] = s
    s = baseline(prefix+'HomeNodeFull.cc')
    s = replace(s, '    cc.setSlcsf(&slcsf->service());', '    cc.setSlcsf(&slcsf->service());\n    cc.setSnHashMasks(p.sn_hash_masks);')
    result[prefix+'HomeNodeFull.cc'] = s

    s = baseline('src/sim/Workload.py')
    s = 'from m5.SimObject import PyBindMethod\n'+s
    s = replace(s, "    abstract = True\n\n    @classmethod\n    def _is_compatible_with", "    abstract = True\n    cxx_exports = [PyBindMethod('allocPhysPages')]\n\n    @classmethod\n    def _is_compatible_with")
    result['src/sim/Workload.py'] = s
    from extended_endpoints import extend
    extend(result, read, baseline, replace)
    from native_l2_patch import extend_native
    extend_native(result)
    return result
