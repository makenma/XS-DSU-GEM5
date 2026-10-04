"""Two host-only router changes; stage order, credit and arbitration unchanged."""

COMMIT = '65f3241a6cd486dffddaca251536f5c691af9a1f'
CC = 'src/mem/cache/CHI/ChiRouterRefModel.cc'
HH = 'src/mem/cache/CHI/ChiRouterRefModel.hh'
BASELINE_SHA256 = {
    CC: 'c0e1e98175c7f6fdb3872a5900f60102bfd72caea4b2f723297d69eeeac90d75',
    HH: 'fc4e3b5d4b1fe4eaf51586867d3b193286e40d9e61adc2ea27e99c02fa098944',
}
OBSERVED_CC_SHA256 = 'bf22228d8b31d2a6787dfd5b1e13a96ff26cccd7d89965ca5a7bcc12760457b7'


def replace_exact(text, before, after, count=1):
    if text.count(before) != count:
        raise ValueError('unexpected router patch context: ' + before[:90])
    return text.replace(before, after)


def observer_source(baseline_cc):
    """Exactly the existing extended_endpoints observer, before optimization."""
    text = '#include "mem/cache/CHI/FlowGraphTrace.hh"\n' + baseline_cc
    return replace_exact(text,
        '    stats.internalSentFlits[chIdx(ch)][direction]++;',
        '    FlowGraphTrace::get().route(flit.flit,name(),direction);\n'
        '    stats.internalSentFlits[chIdx(ch)][direction]++;')


def patches(observed):
    cc = observed[CC]
    hh = observed[HH]
    cc = replace_exact(cc, '''                for (int o = 0; o < OutPortNum; ++o) {
                    if (!inbufDataByOut[c][r][ip][o].empty() ||
                        !internalReqByOutQ[c][r][ip][o].empty()) {
                        return true;
                    }
                }
''', '''                // These queues are populated only when this immutable
                // mode is enabled. Keep hasWork's value and event timing.
                if (multiFlitOut) {
                    for (int o = 0; o < OutPortNum; ++o) {
                        if (!inbufDataByOut[c][r][ip][o].empty() ||
                            !internalReqByOutQ[c][r][ip][o].empty()) {
                            return true;
                        }
                    }
                }
''')
    cc = replace_exact(cc, '''                RoutedFlit head;
                if (!peekInbufFlit(ch, r, win_ip, out, head)) {''',
        '''                QueueDir dir;
                if (!peekInbufDirection(ch, r, win_ip, out, dir)) {''', 2)
    cc = replace_exact(cc,
        '                if (!canSendOut(ch, r, out, head.dir)) {',
        '                if (!canSendOut(ch, r, out, dir)) {', 2)
    before = '''bool
ChiRouterRefModel::peekInbufFlit(ChannelType ch, int router, InPort ip,
                                 OutPort out, RoutedFlit &flit) const
{
    const int c = chIdx(ch);
    const int out_idx = static_cast<int>(out);

    if (!multiFlitOut) {
        const auto &q = inbufData[c][router][ip];
        if (q.empty() || q.front().out != out) {
            return false;
        }
        flit = q.front();
        return true;
    }

    const auto &q = inbufDataByOut[c][router][ip][out_idx];
    if (q.empty()) {
        return false;
    }
    flit = q.front();
    return true;
}
'''
    after = before.replace(
        'peekInbufFlit(ChannelType ch, int router, InPort ip,\n'
        '                                 OutPort out, RoutedFlit &flit) const',
        'peekInbufDirection(ChannelType ch, int router, InPort ip,\n'
        '                                      OutPort out, QueueDir &dir) const')
    after = after.replace('flit = q.front();', 'dir = q.front().dir;')
    cc = replace_exact(cc, before, after)
    hh = replace_exact(hh,
        '    bool peekInbufFlit(ChannelType ch, int router, InPort ip, OutPort out,\n'
        '                       RoutedFlit &flit) const;',
        '    bool peekInbufDirection(ChannelType ch, int router, InPort ip,\n'
        '                            OutPort out, QueueDir &dir) const;')
    return {CC: cc, HH: hh}
