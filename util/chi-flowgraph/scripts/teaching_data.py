"""Data-path CHI lessons. Every timing, field, and instruction is modeled.

These are protocol illustrations, not CPU tests or gem5 capture evidence.
The C-like programs describe intent; the scenario supplies legal arbitration.
"""

from teaching_flow import Scenario


def line_value(value):
    return value.to_bytes(8, 'little').hex() + '00' * 56


def cached(state, value=9, **extra):
    return {'state': state, 'dirty': state in ('UD', 'SD', 'UDP'),
            'data': line_value(value), 'validMask': 'ff' * 64, **extra}


def home(state='I', owner=None, sharers=(), value=9, dirty=False):
    result = {'state': state, 'sfState': 'EN' if owner else 'SN' if sharers else 'I',
              'owner': owner, 'sharers': list(sharers), 'dirty': dirty}
    if state != 'I':
        result.update(data=line_value(value), validMask='ff' * 64)
    return result


def memory_write(s, tick, root, value, desc):
    """Complete both 256-bit beats before retiring this illustrative SN write."""
    req = s.msg(tick, 'HN0', 'SN0', 'REQ', 'WriteNoSnpFull', root,
                fields={'TxnID': 40, 'ExpCompAck': 0, 'Size': 6},
                desc=desc, leg=root + '/memory-write')
    rsp = s.msg(req.end + 1000, 'SN0', 'HN0', 'RSP', 'CompDBIDResp', root,
                fields={'TxnID': 40, 'DBID': 80, 'Resp': 0},
                desc='SN 分配 DBID；完成承诺不代表两拍写数据已经到达。',
                leg=root + '/memory-write')
    data = s.data(rsp.end + 1000, 'HN0', 'SN0', 'NonCopyBackWriteData', root,
                  0, 'I', value=value, txnid=80, desc='两拍 DataID=0/2 到齐后更新内存。',
                  leg=root + '/memory-write')
    memory = {'state': 'memory', 'dirty': False, 'data': value if isinstance(value, str) else line_value(value),
              'validMask': 'ff' * 64}
    s.state(data.end, 'SN0', root, memory)
    return data


def dct_double():
    s = Scenario('dct-double', 'DCT 双路返回 · 数据同时飞向 RN 与 HN', 'forwarding',
                 '一个 SnpSharedFwd 产生两份数据：读方拿 SC，HN 接过脏责任再写内存。',
                 'B5.1.3.2 / B4.8.3.3 / B4.9', [277, 281, 289])
    s.thread(0, 'P0 · 共享读取', '// C 示意；准备阶段 RN1 持有 UD，内存 x=0\n'
             '// HN 本例选择 DCT，RetToSrc=1\nuint64_t v = chi_read(ReadShared, &x);\nassert(v == 9);\n')
    s.thread(1, 'P1 · 脏 owner', '// 已完成的准备操作，不是本片段 CPU 实测\n'
             'WRITE_ONCE(x, 9);\n// 同时向 RN0 发 CompData_SC，向 HN 发带 PD 的数据\n')
    root = s.requester('shared', 0, 3)
    s.seed('RN0', {'state': 'I'})
    s.seed('RN1', cached('UD'))
    s.seed('HN0', home(owner='RN1', sharers=['RN1']))
    s.seed('SN0', cached('memory', 0))
    req = s.msg(1000, 'RN0', 'HN0', 'REQ', 'ReadShared', root,
                fields={'TxnID': 10, 'ExpCompAck': 1, 'Size': 6})
    snp = s.msg(req.end + 1000, 'HN0', 'RN1', 'SNP', 'SnpSharedFwd', root,
                fields={'TxnID': 30, 'FwdNID': s.node_ids['RN0'], 'FwdTxnID': 10,
                        'RetToSrc': 1, 'DoNotGoToSD': 1},
                desc='HN 指定直接供数给 RN0，并要求一份返回 HN；RN1 不保留 SD。')
    wait = s.wait(snp.start, 'HN0', root, 'forwarded_response_and_ack',
                  'HN 分别等待 SnpRespDataFwded 和 RN0 的 CompAck，不能把两条路径混为一条。')
    depart = snp.end + 1000
    forward = s.data(depart, 'RN1', 'RN0', 'CompData', root, 1, 'SC', value=9,
                     txnid=10, dbid=30, fields={'HomeNID': s.node_ids['HN0']},
                     desc='DCT：数据直接去 RN0；TxnID=原请求 10，DBID=HN 的 snoop 30。',
                     leg=root + '/direct')
    returned = s.data(depart, 'RN1', 'HN0', 'SnpRespDataFwded', root, 5, 'SC', value=9,
                      txnid=30, fields={'FwdState': 1},
                      desc='第二份数据给 HN：Resp=SC_PD，FwdState=SC；脏责任交给 HN。',
                      leg=root + '/snoop-return')
    s.state(depart + 1000, 'RN1', root, cached('SC'))
    s.state(forward.end, 'RN0', root, cached('SC'))
    s.state(returned.end, 'HN0', root, home('SN', sharers=['RN0', 'RN1'], dirty=True))
    ack = s.msg(forward.end + 1000, 'RN0', 'HN0', 'RSP', 'CompAck', root,
                fields={'TxnID': 30}, desc='CompAck 回 HomeNID=HN0，不回供数的 RN1。')
    written = memory_write(s, returned.end + 1000, root, 9,
                           'HN 接到 dirty 数据后把最新值 9 写入 SN；可与直传/CompAck 并行。')
    finish = max(ack.end, returned.end, written.end)
    s.state(finish, 'HN0', root, home('SN', sharers=['RN0', 'RN1']))
    s.unwait(finish, 'HN0', root, wait)
    s.done(finish, 'HN0', root)
    s.done(finish, 'RN0', root)
    for title, event in [('共享读请求', req), ('指定两个数据去向', snp),
                         ('直达 RN0 的第一拍', forward), ('返回 HN 的第一拍', returned),
                         ('CompAck 回 Home', ack), ('内存更新完成', s.arrival(written))]:
        s.point(title, event)
    s.prerequisites = ['协议推演；C 为意图示意，mesh 时延不是实测。',
                       'RN1=UD、x=9；内存 x=0；本例不用 MTE。',
                       '只向 RN1 发一份 forwarding snoop；该例选择 UD→SC、dirty 转给 HN。']
    s.results = {'RN0': 'SC，x=9', 'RN1': 'SC，x=9', 'HN0': '持有干净值 9',
                 'SN0': 'x=9', '数据路径': 'RN1→RN0 与 RN1→HN0 同时在途'}
    s.outcome = '读方与 HN 收到不同作用的数据副本；HN 等待 snoop 响应、CompAck 和内存写数据到齐。'
    s.requirements = ['DCT / SnpSharedFwd', 'SnpRespDataFwded + FwdState', '两份 DAT 独立完成']
    return s


def dct_fallback():
    s = Scenario('dct-fallback', 'DCT 未采用 · Fwd snoop 转普通返回', 'forwarding',
                 '发出 forwarding snoop 不保证成功直传；HN 必须识别普通 snoop 响应并完成后备路径。',
                 'B2.3.1.1 Alt 5d / B4.8.3', [58, 272, 273])
    s.thread(0, 'P0 · 读方', '// C 示意；RN1=UC，x=9，内存也是 9\n'
             '// HN 请求直传，但 RN1 选择合法降级\nuint64_t v = chi_read(ReadShared, &x);\nassert(v == 9);\n')
    s.thread(1, 'P1 · forwarding 目标', '// 准备阶段已有干净行\n'
             'uint64_t cached_value = READ_ONCE(x);\n// 本例控制器选择普通 SnpShared 处理路径\n')
    root = s.requester('fallback-read', 0, 3)
    s.seed('RN0', {'state': 'I'})
    s.seed('RN1', cached('UC'))
    s.seed('HN0', home(owner='RN1', sharers=['RN1']))
    s.seed('SN0', cached('memory'))
    req = s.msg(1000, 'RN0', 'HN0', 'REQ', 'ReadShared', root,
                fields={'TxnID': 10, 'ExpCompAck': 1, 'Size': 6})
    snp = s.msg(req.end + 1000, 'HN0', 'RN1', 'SNP', 'SnpSharedFwd', root,
                fields={'TxnID': 30, 'FwdNID': s.node_ids['RN0'], 'FwdTxnID': 10, 'RetToSrc': 1},
                desc='这是 forwarding 请求，不是“直传成功”的证据。')
    waiting = s.wait(snp.start, 'HN0', root, 'snoop_result', '等待目标实际选择 forwarding 或普通返回。')
    decision = s.note(snp.end, root, 'RN1 合法地按非 forwarding SnpShared 处理；不发送 RN1→RN0 CompData。', 'RN1')
    returned = s.data(snp.end + 1000, 'RN1', 'HN0', 'SnpRespData', root, 1, 'SC',
                      value=9, txnid=30, desc='普通 SnpRespData_SC，不是 SnpRespDataFwded。')
    s.state(snp.end + 2000, 'RN1', root, cached('SC'))
    s.unwait(returned.end, 'HN0', root, waiting)
    s.state(returned.end, 'HN0', root, home('SN', sharers=['RN1']))
    response = s.data(returned.end + 1000, 'HN0', 'RN0', 'CompData', root, 1, 'SC',
                      value=9, txnid=10, dbid=50,
                      desc='HN 已收齐普通 snoop 数据，改由 HN 向原请求者供数。')
    s.state(response.end, 'RN0', root, cached('SC'))
    ack = s.msg(response.end + 1000, 'RN0', 'HN0', 'RSP', 'CompAck', root,
                fields={'TxnID': 50})
    s.state(ack.end, 'HN0', root, home('SN', sharers=['RN0', 'RN1']))
    s.done(ack.end, 'RN0', root)
    s.done(ack.end, 'HN0', root)
    for title, event in [('HN 请求直传', snp), ('RN 选择降级', decision),
                         ('普通响应返回 HN', returned), ('HN 补完读数据', response), ('完成确认', ack)]:
        s.point(title, event)
    s.prerequisites = ['协议推演，不是 gem5 / RTL 捕获。',
                       '协议允许但不期待降级；本例由控制器测试策略明确选择，普通 C 不能保证触发。',
                       'RN1 持有完整 UC 行；未使用 Atomic 或 Exclusive。']
    s.results = {'RN0': 'SC，x=9', 'RN1': 'SC，x=9', '直接 RN1→RN0 数据': '无',
                 '实际路径': 'RN1→HN0→RN0'}
    s.outcome = 'Fwd opcode 表达请求方式；实际 SnpRespData 表明未直传，HN 后备供数后正常完成。'
    s.requirements = ['识别 forwarding 失败响应', '普通 SnpRespData 后备供数']
    return s


def partial_snoop_merge():
    s = Scenario('partial-snoop-merge', 'UDP 部分脏数据 · 与内存按 BE 合并', 'data',
                 '脏 owner 仅持有部分有效字节；HN 必须同时读取内存并以 snoop 的有效字节覆盖旧值。',
                 'B5.1.5 / B4.5.3', [241, 291, 292])
    s.thread(0, 'P0 · 获取完整独占行', '// C 示意；内存整行为 0xaa\n'
             '// RN1 仅有 [8,16) 与 [40,48) 的脏数据\nline64 v = chi_read_line(ReadUnique, &x);\n'
             'assert(v[8] == 0xbb && v[0] == 0xaa);\n')
    s.thread(1, 'P1 · 部分有效的脏 owner', '// 测试模型预置 UDP，普通 C 不保证得到该状态\n'
             'chi_seed_udp(&x, 8, 8, 0xbb);\nchi_seed_udp(&x, 40, 8, 0xcc);\n')
    root = s.requester('partial-read', 0, 3)
    memory = bytes([0xaa] * 64)
    partial = bytearray(64)
    partial[8:16] = bytes([0xbb] * 8)
    partial[40:48] = bytes([0xcc] * 8)
    mask = bytearray(64)
    mask[8:16] = mask[40:48] = bytes([0xff] * 8)
    merged = bytes(partial[i] if mask[i] else memory[i] for i in range(64)).hex()
    s.seed('RN0', {'state': 'I'})
    s.seed('RN1', {'state': 'UDP', 'dirty': True, 'data': partial.hex(), 'validMask': mask.hex()})
    s.seed('HN0', home(owner='RN1', sharers=['RN1']))
    s.seed('SN0', {'state': 'memory', 'data': memory.hex(), 'validMask': 'ff' * 64})
    req = s.msg(1000, 'RN0', 'HN0', 'REQ', 'ReadUnique', root,
                fields={'TxnID': 10, 'ExpCompAck': 1, 'Size': 6})
    begin = req.end + 1000
    snp = s.msg(begin, 'HN0', 'RN1', 'SNP', 'SnpUnique', root,
                fields={'TxnID': 30, 'RetToSrc': 1}, desc='失效 UDP owner，取回其部分有效字节。')
    mem = s.msg(begin, 'HN0', 'SN0', 'REQ', 'ReadNoSnp', root,
                fields={'TxnID': 40, 'ExpCompAck': 0, 'Size': 6},
                desc='与 snoop 同时读取旧内存，用于补齐 owner 无效字节。', leg=root + '/memory-read')
    wait = s.wait(begin, 'HN0', root, 'partial_data_merge', '等待 snoop 部分数据和内存完整行都到齐。')
    fresh = s.data(snp.end + 1000, 'RN1', 'HN0', 'SnpRespDataPtl', root, 4, 'I',
                   value=partial.hex(), be=mask.hex(), txnid=30,
                   desc='每拍仅偏移 8..15 有效；未选中的零只是无效载荷，不能覆盖内存。')
    s.state(snp.end + 2000, 'RN1', root, {'state': 'I', 'dirty': False, 'validMask': '00' * 64})
    old = s.data(mem.end + 3000, 'SN0', 'HN0', 'CompData', root, 0, 'I',
                 value=memory.hex(), txnid=40, desc='内存仍全部为 0xaa。', leg=root + '/memory-read')
    ready = max(fresh.end, old.end)
    s.unwait(ready, 'HN0', root, wait)
    combined = s.note(ready, root,
                      '合并规则：BE=1 取 snoop，BE=0 取内存；得到 aa×8 / bb×8 / aa×24 / cc×8 / aa×16。', 'HN0')
    response = s.data(ready + 1000, 'HN0', 'RN0', 'CompData', root, 6, 'UD',
                      value=merged, txnid=10, dbid=50,
                      desc='完整有效两拍交给新 owner，并把 dirty 责任传给 RN0。')
    s.state(response.end, 'RN0', root, {'state': 'UD', 'dirty': True, 'data': merged, 'validMask': 'ff' * 64})
    ack = s.msg(response.end + 1000, 'RN0', 'HN0', 'RSP', 'CompAck', root, fields={'TxnID': 50})
    s.state(ack.end, 'HN0', root, home(owner='RN0', sharers=['RN0']))
    s.done(ack.end, 'RN0', root)
    s.done(ack.end, 'HN0', root)
    for title, event in [('两个来源并行', snp), ('部分 BE 数据', fresh), ('旧内存数据', old),
                         ('按字节合并', combined), ('完整行交给新 owner', response), ('完成', ack)]:
        s.point(title, event)
    s.prerequisites = ['协议推演；不计入真实 CPU opcode 覆盖。',
                       '显式支持 UDP 和 SnpRespDataPtl；与普通 WriteUniquePtl 字节写不是同一场景。',
                       '256-bit DAT，每行 DataID=0/2 两拍；本例两拍分别有 8 字节有效。']
    s.results = {'RN0': 'UD；完整行保留 bb/cc 新字节及 aa 旧字节', 'RN1': 'I',
                 'SN0': '仍为 aa×64，dirty 责任已到 RN0', '合并行': merged}
    s.outcome = '部分 snoop 返回不能单独构成完整行；先按 BE 合并，再传递完整行及 dirty 责任。'
    s.requirements = ['UDP', 'SnpRespDataPtl', '多来源 BE 合并']
    return s


def writeclean_old():
    s = Scenario('writeclean-old', 'WriteCleanFull × SnpOnce · 晚到的数据更旧', 'advanced',
                 '清理旧快照与之后的本地写重叠；snoop 可看见较新值，稍后 copyback 仍回写旧快照。',
                 'B4.11.1 / B4.7.3', [262, 263, 283, 284])
    s.thread(0, 'P0 · 一次性读取', '// C 示意；HN 先处理这次读\n'
             'uint64_t v = chi_read(ReadOnce, &x);\nassert(v == 9);\n')
    s.thread(1, 'P1 · 清理快照与后续写', '// 初始 RN1=UD，x=7，内存 x=0\n'
             'chi_copyback_async(WriteCleanFull, &x); // 冻结旧快照 7\n'
             'WRITE_ONCE(x, 9); // 后续 dirty 世代，SnpOnce 可观察到 9\n'
             'wait_copyback(); // 只清理快照 7，不抹去新世代的 dirty\n')
    read = s.requester('once', 0, 2, tick=0)
    write = s.requester('clean-snapshot', 1, 2, tick=0)
    update = s.requester('new-store-generation', 1, 3, tick=3000)
    # These messages are caused by P0's read, but their current payload was
    # produced by P1's later store. Do not replace one identity with the other.
    new_value_producer = s.roots[update]['instruction']
    s.seed('RN0', {'state': 'I'})
    s.seed('RN1', cached('UD', 7))
    s.seed('HN0', home(owner='RN1', sharers=['RN1']))
    s.seed('SN0', cached('memory', 0))
    req = s.msg(1000, 'RN0', 'HN0', 'REQ', 'ReadOnce', read,
                fields={'TxnID': 10, 'ExpCompAck': 1, 'Size': 6})
    wc = s.msg(1000, 'RN1', 'HN0', 'REQ', 'WriteCleanFull', write,
               fields={'TxnID': 20, 'ExpCompAck': 0, 'Size': 6},
               desc='旧清理世代冻结 x=7；HN 同址读先被接纳。')
    wait = s.wait(wc.end, 'HN0', write, 'same_address', '等前序 ReadOnce 完成后，才发 CopyBack 的 CompDBIDResp。', read)
    s.state(3000, 'RN1', update, cached('UD', 9))
    newer = s.note(3000, update, 'CPU 后续写产生新世代 x=9；旧 WriteClean 快照仍为 7。', 'RN1')
    s.done(4000, 'RN1', update)
    snp = s.msg(max(req.end, wc.end) + 1000, 'HN0', 'RN1', 'SNP', 'SnpOnce', read,
                fields={'TxnID': 30, 'RetToSrc': 1},
                desc='SnpOnce 允许 RN1 保留可写 UD；这正是旧快照例外的限定条件。')
    fresh = s.data(snp.end + 1000, 'RN1', 'HN0', 'SnpRespData', read, 2, 'UD',
                   value=9, txnid=30, producers=[new_value_producer],
                   desc='snoop 取当前值 9；Resp=UD，不转移 dirty 责任。')
    response = s.data(fresh.end + 1000, 'HN0', 'RN0', 'CompData', read, 0, 'I',
                      value=9, txnid=10, dbid=50, producers=[new_value_producer],
                      desc='ReadOnce 返回 9，不安装缓存。')
    ack = s.msg(response.end + 1000, 'RN0', 'HN0', 'RSP', 'CompAck', read,
                fields={'TxnID': 50})
    s.done(ack.end, 'RN0', read)
    s.done(ack.end, 'HN0', read)
    s.unwait(ack.end, 'HN0', write, wait)
    dbid = s.msg(ack.end + 1000, 'HN0', 'RN1', 'RSP', 'CompDBIDResp', write,
                 fields={'TxnID': 20, 'DBID': 60, 'Resp': 0})
    older = s.data(dbid.end + 1000, 'RN1', 'HN0', 'CopyBackWriteData', write, 6, 'UD',
                   value=7, txnid=60, producers=[],  # The seed's historical writer is unknown.
                   desc='限定 WriteCleanFull+SnpOnce*：稍后 copyback 允许带比 snoop 的 9 更旧的快照 7。')
    retained = s.state(older.end, 'RN1', write, cached('UD', 9))
    s.note(older.end, write,
           '旧清理世代完成后可立即重新 Dirty；保留后续 store 的 x=9。不得把 x=9 标成 Clean 而让内存仍是 7。', 'RN1')
    memory = memory_write(s, older.end + 1000, write, 7,
                          '内存只提交本次清理快照 7；RN1 仍持有更新值 9 和其 dirty 责任。')
    s.state(memory.end, 'HN0', write, home(owner='RN1', sharers=['RN1']))
    s.done(memory.end, 'RN1', write)
    s.done(memory.end, 'HN0', write)
    for title, event in [('清理旧快照 7', wc), ('之后写成 9', newer), ('snoop 返回 9', fresh),
                         ('copyback 却返回 7', older), ('新世代继续 UD', retained), ('内存只更新到 7', s.arrival(memory))]:
        s.point(title, event)
    s.prerequisites = ['协议例外推演；不是对现有 classic cache 的能力声明。',
                       '只适用于 WriteCleanFull + SnpOnce*，且 snoop 响应允许继续修改。',
                       '控制器需保存清理快照与后续 store 的世代；不能将此例泛化到 WriteBackFull。']
    s.results = {'RN0 一次性读结果': 9, 'RN1': 'UD，最新值 9；后续世代仍 dirty',
                 'SN0': '旧快照 7', '归因': 'copyback 属于清理指令；新值属于之后的 store'}
    s.outcome = 'snoop 的值 9 可以比晚发的 copyback 值 7 新；清理旧世代不能清掉新世代的 dirty 责任。'
    s.requirements = ['WriteCleanFull', 'SnpOnce 保留可写权限', '清理快照与 store 世代独立跟踪']
    return s


def make_read_unique():
    s = Scenario('make-read-unique', 'MakeReadUnique · 升级途中丢行，完成时补数据', 'advanced',
                 '请求发出时有 SC 副本，但被排在前面的竞争者失效；Home 必须按完成前状态决定返回数据。',
                 'B4.7.1.1 / B4.11', [255, 256, 257, 258, 283, 284])
    s.thread(0, 'P0 · 原共享持有者', '// 初始 RN0=SC，x=9；C 为示意\n'
             'chi_upgrade(MakeReadUnique, &x);\n'
             '// 等待期间收到 SnpUnique，原副本丢失\nassert(READ_ONCE(x) == 9);\n')
    s.thread(1, 'P1 · 较早到达 Home 的竞争者', '// 初始 RN1=I\n'
             'uint64_t v = chi_read(ReadUnique, &x);\n'
             '// 本例得到 UC 后尚未执行 store，随后被 P0 再失效\n')
    upgrade = s.requester('upgrade', 0, 2, tick=0)
    rival = s.requester('rival', 1, 2, tick=0)
    s.seed('RN0', cached('SC'))
    s.seed('RN1', {'state': 'I'})
    s.seed('HN0', home('SN', sharers=['RN0']))
    s.seed('SN0', cached('memory'))
    first = s.msg(1000, 'RN1', 'HN0', 'REQ', 'ReadUnique', rival,
                  fields={'TxnID': 20, 'ExpCompAck': 1, 'Size': 6},
                  desc='示意调度确保 RN1 先在 HN 排序。')
    pending = s.msg(first.end + 1000, 'RN0', 'HN0', 'REQ', 'MakeReadUnique', upgrade,
                    fields={'TxnID': 10, 'ExpCompAck': 1, 'Excl': 0, 'Size': 6},
                    desc='发出时 RN0 仍持有 SC；不是可丢弃旧值的 MakeUnique。')
    waiting = s.wait(pending.end, 'HN0', upgrade, 'same_address', '先完成竞争者 ReadUnique；MakeReadUnique 暂时等待。', rival)
    invalid = s.msg(max(first.end, pending.end) + 1000, 'HN0', 'RN0', 'SNP', 'SnpUnique', rival,
                    fields={'TxnID': 31, 'RetToSrc': 0}, desc='HN 的较早事务使 RN0 在升级等待期间丢失 SC 副本。')
    invrsp = s.msg(invalid.end + 1000, 'RN0', 'HN0', 'RSP', 'SnpResp', rival,
                   fields={'TxnID': 31, 'Resp': 0}, desc='SC→I；HN 已有干净完整行，本 snoop 不携带 DAT。')
    lost = s.state(invalid.end + 1000, 'RN0', rival, {'state': 'I', 'dirty': False, 'validMask': '00' * 64})
    firstdata = s.data(invrsp.end + 1000, 'HN0', 'RN1', 'CompData', rival, 2, 'UC',
                       value=9, txnid=20, dbid=51, desc='竞争者得到 UC、值 9。')
    s.state(firstdata.end, 'RN1', rival, cached('UC'))
    firstack = s.msg(firstdata.end + 1000, 'RN1', 'HN0', 'RSP', 'CompAck', rival,
                     fields={'TxnID': 51})
    s.state(firstack.end, 'HN0', rival, home('SN', owner='RN1', sharers=['RN1']))
    s.done(firstack.end, 'RN1', rival)
    s.done(firstack.end, 'HN0', rival)
    s.unwait(firstack.end, 'HN0', upgrade, waiting)
    snp = s.msg(firstack.end + 1000, 'HN0', 'RN1', 'SNP', 'SnpCleanInvalid', upgrade,
                fields={'TxnID': 32, 'RetToSrc': 0},
                desc='现在处理 MakeReadUnique；原请求者已失效，不能用 SnpMakeInvalid 盲目丢弃其他副本。')
    rsp = s.msg(snp.end + 1000, 'RN1', 'HN0', 'RSP', 'SnpResp', upgrade,
                fields={'TxnID': 32, 'Resp': 0}, desc='RN1 尚未写脏，UC→I，数据仍由 HN 的有效副本提供。')
    s.state(snp.end + 1000, 'RN1', upgrade, {'state': 'I', 'dirty': False, 'validMask': '00' * 64})
    decision = s.note(rsp.end, upgrade,
                      '精确 SF 记录 RN0 已被失效；不能只回 Comp_UC，必须返回 CompData_UC 补行。', 'HN0')
    refill = s.data(rsp.end + 1000, 'HN0', 'RN0', 'CompData', upgrade, 2, 'UC',
                    value=9, txnid=10, dbid=50, desc='MakeReadUnique 按完成前状态补上两拍数据。')
    s.state(refill.end, 'RN0', upgrade, cached('UC'))
    ack = s.msg(refill.end + 1000, 'RN0', 'HN0', 'RSP', 'CompAck', upgrade,
                fields={'TxnID': 50})
    s.state(ack.end, 'HN0', upgrade, home('SN', owner='RN0', sharers=['RN0']))
    s.done(ack.end, 'RN0', upgrade)
    s.done(ack.end, 'HN0', upgrade)
    for title, event in [('发出时还有 SC', pending), ('升级途中副本丢失', lost),
                         ('先完成竞争者', firstack), ('判断必须补数据', decision),
                         ('CompData 补行', refill), ('升级完成', ack)]:
        s.point(title, event)
    s.prerequisites = ['协议推演，包含 Issue H 的 MakeReadUnique（超出原 19 REQ）。',
                       '非 Exclusive；精确 SF；HN 初始有与 SC 相同的干净数据 9。',
                       'RN1 的 ReadUnique 在 Home 排序较早；收到其 CompAck 后才发下一次同址 snoop。']
    s.results = {'RN0': 'UC，完整值 9', 'RN1': 'I', '响应选择': 'CompData_UC，而不是只有 Comp_UC',
                 'SN0': '值 9 未改变'}
    s.outcome = '响应由完成前的缓存状态决定；MakeReadUnique 在途中丢行时可在同一事务内补回数据。'
    s.requirements = ['MakeReadUnique (0x41)', '请求期间接受 invalidating snoop', '按最终持有状态选择有数据完成']
    return s


def build_cases():
    return [dct_double(), dct_fallback(), partial_snoop_merge(), writeclean_old(), make_read_unique()]
