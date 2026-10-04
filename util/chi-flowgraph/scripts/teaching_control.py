"""Credit, cancellation, ordering and CopyAtHome protocol illustrations."""
from teaching_flow import Scenario, line_data


def cached(state, value=0, **kw):
    return {'state': state, 'dirty': state in ('UD', 'SD'),
            'data': line_data(value), 'validMask': 'ff' * 64, **kw}


def early_pcredit():
    s = Scenario('early-pcredit', 'PCrdGrant 提前到 · 信用先于 RetryAck', 'retry',
                 '信用与 RetryAck 可以先后颠倒；RN 缓存提前到达的信用，等两个条件都满足才重发。',
                 'B2.10 / B2.10.2', [178, 179, 181, 182])
    s.thread(0, 'P0 · 读请求', '// C 示意；Home 资源不足，测试调度让信用先到\n'
             'uint64_t v = chi_read(ReadShared, &x);\nassert(v == 9);\n')
    r = s.requester('read', 0, 2)
    s.seed('RN0', {'state': 'I'})
    s.seed('HN0', cached('SN', 9, sfState='I', sharers=[]))
    q = s.msg(1000, 'RN0', 'HN0', 'REQ', 'ReadShared', r,
              {'TxnID': 10, 'AllowRetry': 1, 'PCrdType': 0, 'ExpCompAck': 1, 'Size': 6})
    retry = s.msg(q.end + 1000, 'HN0', 'RN0', 'RSP', 'RetryAck', r,
                  {'TxnID': 10, 'PCrdType': 2}, delay=14000,
                  desc='RetryAck 已发出，本例网络仲裁使它晚到；不能把信用当作本请求的 RetryAck。')
    credit = s.msg(q.end + 2000, 'HN0', 'RN0', 'RSP', 'PCrdGrant', r,
                   {'PCrdType': 2}, desc='按 (SrcID=HN0, PCrdType=2) 记账；信用不绑定某个 TxnID。')
    wait = s.wait(credit.end, 'RN0', r, 'retry_ack', '信用已到；仍等这次请求的 RetryAck。')
    n = s.note(credit.end, r, '待用信用 +1，但请求还不能重发。PCrdGrant 没有有效的请求 TxnID 关联。', 'RN0')
    s.unwait(retry.end, 'RN0', r, wait)
    again = s.msg(retry.end + 1000, 'RN0', 'HN0', 'REQ', 'ReadShared', r,
                  {'TxnID': 10, 'AllowRetry': 0, 'PCrdType': 2, 'ExpCompAck': 1, 'Size': 6},
                  desc='RetryAck 与正确类型信用都已到：消费一个信用，以 AllowRetry=0 重发。')
    d = s.data(again.end + 1000, 'HN0', 'RN0', 'CompData', r, 1, 'SC',
               txnid=10, dbid=50, value=9)
    s.state(d.end, 'RN0', r, cached('SC', 9))
    ack = s.msg(d.end + 1000, 'RN0', 'HN0', 'RSP', 'CompAck', r, {'TxnID': 50})
    s.state(ack.end, 'HN0', r, cached('SN', 9, sfState='SN', sharers=['RN0']))
    for node in ('RN0', 'HN0'):
        s.done(ack.end, node, r)
    for title, e in [('初次请求可重试', q), ('信用先到', s.arrival(credit)), ('只记账，暂不重发', n),
                     ('迟到的 RetryAck', retry.rxid), ('带信用重发', again), ('读数据到齐', s.arrival(d))]:
        s.point(title, e)
    s.prerequisites += ['HN 拒绝初次请求后产生 RetryAck 与 PCrdGrant，网络允许接收次序颠倒。',
                        '本例只展示一个待重试请求；多 MSHR 共享信用时同样不能按 TxnID 绑定信用。']
    s.requirements = ['P-Credit 以来源节点与 PCrdType 记账', '提前信用暂存']
    s.results = {'RN0': 'SC，x=9', '待用信用': 0, '重发': '1 次，AllowRetry=0 / PCrdType=2'}
    s.sources += [{'label': '香山 L2 · PCredit 设计', 'url': 'https://docs.xiangshan.cc/projects/design/en/kunminghu-v3/cache/l2cache/downstream/PCredit/'}]
    return s


def write_cancel():
    s = Scenario('write-cancel', 'WriteDataCancel · 取消写仍要发齐数据拍', 'data',
                 '部分写已经拿到 DBID 后决定取消；仍发送原定数量的 DAT，但 opcode 为 WriteDataCancel、全部 BE=0。',
                 'B4.5.2 / B2.7', [137, 239, 240])
    s.thread(0, 'P0 · 可取消的部分写', '// 协议测试接口示意；Normal memory，不是 Device\n'
             'auto w = chi_write_async(WriteUniquePtl, &x, 64, mask, data);\n'
             'chi_cancel_before_data(w); // RN 控制器操作，不是普通 C store\nwait_completion(w);\n')
    r = s.requester('write', 0, 2)
    s.seed('RN0', {'state': 'I'})
    s.seed('HN0', cached('SN', 7, sfState='I', sharers=[]))
    s.seed('SN0', cached('memory', 7))
    q = s.msg(1000, 'RN0', 'HN0', 'REQ', 'WriteUniquePtl', r,
              {'TxnID': 10, 'Size': 6, 'ExpCompAck': 0, 'Order': 0, 'MemAttr': 13},
              desc='本例请求一整行传输范围，原定两个 256-bit DAT，实际更新掩码为部分字节。')
    db = s.msg(q.end + 1000, 'HN0', 'RN0', 'RSP', 'DBIDResp', r, {'TxnID': 10, 'DBID': 50})
    decision = s.note(db.end, r, 'RN 尚未发送任何写数据，控制器决定取消；不能只停止发送，否则 HN 永远等不到数据。', 'RN0')
    d = s.data(db.end + 1000, 'RN0', 'HN0', 'WriteDataCancel', r, 0, 'I',
               value=0, be='00' * 64, txnid=50,
               desc='两拍都使用 WriteDataCancel，DataID=0/2；按协议载荷与 BE 均清零。')
    comp = s.msg(d.end + 1000, 'HN0', 'RN0', 'RSP', 'Comp', r, {'TxnID': 10, 'DBID': 50, 'Resp': 0},
                 desc='HN 消耗全部取消拍并返回完成；既有值 7 不被修改。')
    s.state(d.end, 'HN0', r, cached('SN', 7, sfState='I', sharers=[]))
    for node in ('RN0', 'HN0'):
        s.done(comp.end, node, r)
    for title, e in [('发起部分写', q), ('已取得 DBID', s.arrival(db)), ('取消决定', decision),
                     ('第一拍 BE=0', d), ('第二拍仍不可省', d.ids[-1]), ('正常收尾', s.arrival(comp))]:
        s.point(title, e)
    s.prerequisites += ['RN→HN 本例使用 WriteUniquePtl；不能把这个取消方式直接用于 RN 发出的 WriteUniqueFull。',
                        '非 Device 内存；尚未开始写数据；ExpCompAck=0。']
    s.requirements = ['WriteDataCancel', '即使取消也回收所有预定 DAT 拍']
    s.results = {'HN0 / SN0': 'x=7 未改变', 'DAT 拍数': 2, '全部 byte enable': 0}
    return s


def ordered_writes():
    s = Scenario('ordered-writes', 'Streaming OWO · CompAck 控制写的可见顺序', 'ordering',
                 '两个写可以同时在途；后写的 CompAck 要等前写 Comp，Home 才能向其他观察者公开后写。',
                 'B2.7.5.3', [144, 145, 146])
    s.thread(0, 'P0 · 同一有序写流', '// C 意图示意；由 RN 的 OWO 测试接口建立关联写流\n'
             'chi_owo_write(&x, 9); // 映射到 HN0\n'
             'chi_owo_write(&flag, 1); // 映射到 HN1\n'
             '// 第二次 CompAck 等待第一次 Comp，不等待第二次 Comp\n')
    a = s.requester('write-x', 0, 2)
    b = s.requester('write-flag', 0, 3, address='0x1040', tick=8000)
    for node, address in [('HN0', '0x1000'), ('HN1', '0x1040')]:
        s.seed(node, cached('SN', 0, sfState='I', sharers=[]), address)
    q1 = s.msg(1000, 'RN0', 'HN0', 'REQ', 'WriteUniqueFull', a,
               {'TxnID': 10, 'ExpCompAck': 1, 'Order': 2, 'Size': 6})
    db1 = s.msg(q1.end + 1000, 'HN0', 'RN0', 'RSP', 'DBIDResp', a, {'TxnID': 10, 'DBID': 50})
    d1 = s.data(db1.end + 1000, 'RN0', 'HN0', 'NonCopyBackWriteData', a, 0, 'I', txnid=50, value=9)
    ack1 = s.msg(db1.end + 1000, 'RN0', 'HN0', 'RSP', 'CompAck', a, {'TxnID': 50},
                 desc='当前流没有更早写；一收到 DBID 即可发 Ack，不等待本写的 Comp。')
    q2 = s.msg(max(db1.end + 3000, 9000), 'RN0', 'HN1', 'REQ', 'WriteUniqueFull', b,
               {'TxnID': 11, 'ExpCompAck': 1, 'Order': 2, 'Size': 6},
               desc='前写 DBID 已到，允许发后写；前写 Comp 尚未到达。')
    db2 = s.msg(q2.end + 1000, 'HN1', 'RN0', 'RSP', 'DBIDResp', b, {'TxnID': 11, 'DBID': 51})
    d2 = s.data(db2.end + 1000, 'RN0', 'HN1', 'NonCopyBackWriteData', b, 0, 'I', txnid=51, value=1)
    w = s.wait(db2.end, 'RN0', b, 'prior_ordered_comp', '后写可发 DAT，但其 CompAck 必须等前写 Comp。', a)
    hw = s.wait(d2.end, 'HN1', b, 'compack', '数据已收齐；还没有 CompAck，不得向其他观察者公开 flag=1。')
    s.state(max(d1.end, ack1.end), 'HN0', a, cached('SN', 9, sfState='I', sharers=[], dirty=True))
    comp1 = s.msg(d2.end + 5000, 'HN0', 'RN0', 'RSP', 'Comp', a, {'TxnID': 10, 'DBID': 50},
                  desc='前写 coherence action 已完成；现在允许后写送出 CompAck。')
    s.unwait(comp1.end, 'RN0', b, w)
    ack2 = s.msg(comp1.end + 1000, 'RN0', 'HN1', 'RSP', 'CompAck', b, {'TxnID': 51},
                 desc='不必等后写自己的 Comp；否则可能死锁。')
    s.unwait(ack2.end, 'HN1', b, hw)
    visible = s.state(ack2.end, 'HN1', b, cached('SN', 1, sfState='I', sharers=[], dirty=True))
    comp2 = s.msg(ack2.end + 1000, 'HN1', 'RN0', 'RSP', 'Comp', b, {'TxnID': 11, 'DBID': 51})
    for node in ('RN0', 'HN0'):
        s.done(comp1.end, node, a)
    for node in ('RN0', 'HN1'):
        s.done(comp2.end, node, b)
    for title, e in [('首写 Ack 无需等自身 Comp', ack1), ('前写未完成，后写已发', q2),
                     ('后写数据先到', s.arrival(d2)), ('前写 Comp 解锁', s.arrival(comp1)), ('后写 Ack', ack2), ('flag 才能公开', visible)]:
        s.point(title, e)
    s.prerequisites += ['同一 RN 的关联写流；本例固定两个不同 HN 的地址映射。',
                        'Order=2 且 ExpCompAck=1；未启用跨目标的额外发送优化。',
                        'C 表达写流意图，不能靠两个普通 store 保证产生此协议序列。']
    s.requirements = ['Streaming OWO', 'RN 跨事务完成顺序跟踪', 'HN 在 CompAck 前阻止写公开']
    s.results = {'HN0': 'x=9 已可见', 'HN1': 'flag=1 随后才可见', 'SN': '可仍持有旧值，由 HN 保留 dirty 责任'}
    return s


def copy_at_home():
    s = Scenario('copy-at-home', 'CopyAtHome · 写回可以完全没有 DAT', 'advanced',
                 'RN 自取得 CAH=1 的行后未改写，HN 仍有隐藏副本；以 Comp→CompAck 完成逐出，省掉整行数据。',
                 'B2.3.2.3 / B2.8.8 / B4.5.4', [69, 158, 159, 160, 161, 245, 246])
    s.thread(0, 'P0 · 逐出未修改的行', '// 准备：先前 CompData_UC 携带 CAH=1，x=9\n'
             '// 此后没有 store；HN 仍持有相同的隐藏副本\n'
             'chi_copyback(WriteEvictFull, &x);\n// 协议完成：Comp 后仍必须发 CompAck\n')
    r = s.requester('evict', 0, 3)
    s.seed('RN0', cached('UC', 9))
    s.seed('HN0', {'state': 'I', 'sfState': 'EN', 'owner': 'RN0', 'sharers': ['RN0']})
    s.seed('SN0', cached('memory', 9))
    n = s.note(0, r, 'HN 的隐藏副本为 9，当前不能作为有效 SLC 行公开；RN 本地缓存的 CAH=1。', 'HN0')
    q = s.msg(1000, 'RN0', 'HN0', 'REQ', 'WriteEvictFull', r,
              {'TxnID': 10, 'CAH': 1, 'ExpCompAck': 0, 'Size': 6},
              desc='CAH 只表示自 Home 保留副本以来没改写；HN 选择本次无 DAT 优化。')
    c = s.msg(q.end + 1000, 'HN0', 'RN0', 'RSP', 'Comp', r,
              {'TxnID': 10, 'DBID': 50, 'Resp': 0},
              desc='回 Comp 而非 CompDBIDResp；不要求 CopyBackWriteData。')
    wait = s.wait(c.start, 'HN0', r, 'compack', 'Comp 已发出，隐藏副本仍需保留，直到收到 CompAck。')
    ack = s.msg(c.end + 1000, 'RN0', 'HN0', 'RSP', 'CompAck', r,
                {'TxnID': 50, 'Resp': 2, 'CacheState': 'UC'},
                desc='即使原请求 ExpCompAck=0，也必须 Ack；Resp=UC 告诉 HN 副本未被失效。')
    s.state(ack.start, 'RN0', r, {'state': 'I', 'dirty': False})
    s.unwait(ack.end, 'HN0', r, wait)
    installed = s.state(ack.end, 'HN0', r, cached('SN', 9, sfState='I', owner=None, sharers=[]))
    for node in ('HN0', 'RN0'):
        s.done(ack.end, node, r)
    for title, e in [('初始隐藏副本', n), ('CAH=1 的逐出', q), ('HN 选择无数据完成', c),
                     ('ExpCompAck=0 仍要 Ack', ack), ('隐藏副本转有效', installed)]:
        s.point(title, e)
    s.prerequisites += ['HN 先前明确提供 CAH=1，RN 缓存该属性且未修改行；不能临时把普通写回的 CAH 改为 1。',
                        '本例没有交错 snoop；若 CompAck 报 I，HN 必须按相应规则处理隐藏副本。']
    s.requirements = ['CopyAtHome 属性跟踪', '隐藏副本与普通有效 SLC 行分开', '无 DAT CopyBack 完成握手']
    s.results = {'RN0': 'I', 'HN0': '干净有效值 9', 'DAT 数量': 0, 'CompAck': '必须发送，Resp=UC'}
    return s


def build_cases():
    return [early_pcredit(), write_cancel(), ordered_writes(), copy_at_home()]
