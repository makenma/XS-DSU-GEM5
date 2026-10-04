"""Additive native transport/profile, preserving the legacy component fixtures."""


def extend_native(result):
    prefix = 'src/mem/cache/CHI/'
    channel = prefix + 'base/ChiChannel.hh'
    source = result[channel]
    source = source.replace('        FlowGraphIdentity flow;', '''        FlowGraphIdentity flow;
        // Transport mode metadata, not a CHI field. Native endpoints construct
        // Issue H field values before handing the flit to the router.
        bool nativeChi = false;''')
    source = source.replace('uint8_t     dbid = 0;', 'uint16_t    dbid = 0;')
    source = source.replace('        uint32_t    stashNid = 0;', '''        uint32_t    stashNid = 0;
        bool likelyShared = false;
        bool cah = false;''')
    source = source.replace('    struct RawSnp:BaseFlit\n    {', '''    struct RawSnp:BaseFlit
    {
        bool retToSrc = false;
        bool doNotGoToSD = false;''')
    result[channel] = source
    sconscript = prefix + 'SConscript'
    for name in ['ChiNativeL2', 'ChiNativeMemory']:
        result[sconscript] += f"\nSimObject('{name}.py',sim_objects=['{name}'])\nSource('{name}.cc')\n"
    # gem5 trace already supplies sim/cur_tick.cc. Packet's generic integer
    # accessors are defined in sim/bufval.cc and need an explicit test source.
    result[sconscript] += "\nGTest('chi_native_request.test', 'ChiNativeRequest.test.cc', '../../packet.cc', '../../../sim/bufval.cc', with_tag('gem5 trace'))\n"
