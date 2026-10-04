"""Issue H race examples: illustrative protocol models, never CPU captures.

The C fragments express intent. Delays and resource scheduling belong to the
teaching model; the fragments are not executable tests that guarantee a race.
"""

from teaching_flow import Scenario


LINE9 = '0900000000000000' + '00' * 56
LINE0 = '00' * 64
VALID = 'ff' * 64


def _cache(state, value=LINE9, **extra):
    return {'state': state, 'dirty': state in ('UD', 'SD'),
            **({'data': value, 'validMask': VALID} if state != 'I' else {'validMask': LINE0}), **extra}


def _home(s, owner=None, sharers=None, dirty=False, value=LINE9):
    result = _cache(s, value, sfState='EN' if owner else 'SN' if sharers else 'I',
                    owner=owner, sharers=sharers or ([owner] if owner else []))
    result['dirty'] = dirty
    return result


def _read(s, tick, rn, root, txn=10, opcode='ReadShared', delay=0):
    return s.msg(tick, rn, 'HN0', 'REQ', opcode, root,
                 fields={'TxnID': txn, 'Size': 6, 'Order': 0, 'ExpCompAck': 1, 'AllowRetry': 1},
                 desc='同址分配型读；Order=0，完整 cache line 为 64 字节。', delay=delay)


def _ack(s, tick, rn, root, dbid=50, delay=0, desc=''):
    return s.msg(tick, rn, 'HN0', 'RSP', 'CompAck', root,
                 fields={'TxnID': dbid}, delay=delay,
                 desc=desc or 'CompAck 的 TxnID 使用完成响应提供的 DBID。')


def _wait_event(s, wait_id):
    return next(e['id'] for e in s.observed
                if e['kind'] == 'wait_start' and e['waitId'] == wait_id)


def _split_early_snoop():
    s = Scenario('split-early-snoop', '提前 CompAck 后，snoop 追上读数据', 'ordering',
                 'RespSepData 允许提前确认完成，但 RN 必须等整行 DAT 到齐才能回答追来的 snoop。',
                 'B2.3.1.1; B2.7.3; B4.11.1–B4.11.2', [57, 58, 137, 283, 284])
    s.thread(0, 'P0 · 分离响应读方',
             '// C 伪代码；通道延迟由教学调度设置\n'
             'uint64_t v = chi_read(ReadShared, &x);\n'
             'assert(v == 9);\n')
    s.thread(1, 'P1 · 随后的独占读',
             '// 同址访问；并不由 C 代码控制网络到达次序\n'
             'uint64_t v = chi_read(ReadUnique, &x);\n'
             'assert(v == 9);\n')
    first = s.requester('split-read', 0, 2)
    later = s.requester('later-unique', 1, 2, tick=8000)
    s.seed('RN0', _cache('I'))
    s.seed('RN1', _cache('I'))
    s.seed('HN0', _home('SC'))
    s.seed('SN0', _cache('SC'))
    req = _read(s, 1000, 'RN0', first)
    rsp = s.msg(req.end + 1000, 'HN0', 'RN0', 'RSP', 'RespSepData', first,
                fields={'TxnID': 10, 'DBID': 50},
                desc='响应先到。Order=0 允许 RN0 不等 DataSepResp 就发送 CompAck。')
    # Both beats leave Home before Home acts on the following request. Only
    # the final beat's delivery is delayed (B4.11.2); no unsent beat is bypassed.
    data = s.data(req.end + 2000, 'HN0', 'RN0', 'DataSepResp', first, 1, 'SC',
                  txnid=10, dbid=50, beat_delays={2: 40000},
                  desc='两拍连续发出：DataID=0 正常先到，DataID=2 在 mesh 中延迟，晚于 SnpUnique 抵达 RN0。')
    early_ack = _ack(s, rsp.end + 1000, 'RN0', first,
                     desc='收到 RespSepData 后不等全行 DAT 就提前 CompAck；这不代表第二拍已经到达 RN0。')
    s.done(early_ack.end, 'HN0', first)
    unique = _read(s, early_ack.end + 1000, 'RN1', later, txn=11, opcode='ReadUnique')
    snp = s.msg(unique.end + 1000, 'HN0', 'RN0', 'SNP', 'SnpUnique', later,
                fields={'TxnID': 30, 'RetToSrc': 1},
                desc='HN 已收到前一个 CompAck，允许发同址 snoop；DAT 仍在飞行。')
    assert snp.end < data.end
    wait = s.wait(snp.end, 'RN0', later, 'data_pending',
                  '已经收到 RespSepData：等先前读的全部 DataSepResp 到齐后再回答 snoop。', blocker=first)
    s.state(data.end, 'RN0', first, _cache('SC'))
    s.done(data.end, 'RN0', first)
    s.unwait(data.end, 'RN0', later, wait)
    returned = s.data(data.end + 1000, 'RN0', 'HN0', 'SnpRespData', later, 0, 'I',
                      txnid=30,
                      desc='整行到齐后失效；RetToSrc=1，本例选择返回完整 clean 数据。')
    s.state(data.end + 1000, 'RN0', later, _cache('I'))
    answer = s.data(returned.end + 1000, 'HN0', 'RN1', 'CompData', later, 2, 'UC',
                    txnid=11, dbid=51, desc='前一持有者已失效，RN1 获得独占干净副本。')
    s.state(answer.end, 'RN1', later, _cache('UC'))
    ack = _ack(s, answer.end + 1000, 'RN1', later, dbid=51)
    s.state(ack.end, 'HN0', later, _home('I', owner='RN1'))
    s.done(ack.start, 'RN1', later)
    s.done(ack.end, 'HN0', later)
    s.point('响应先到', s.arrival(rsp))
    s.point('第一拍 DataID=0 已到', s.arrival(data.ids[0]))
    s.point('提前确认', early_ack)
    s.point('snoop 抵达时数据未齐', _wait_event(s, wait))
    s.point('最后一拍 DataID=2 到齐', s.arrival(data))
    s.point('全行到齐才回应 snoop', returned)
    s.prerequisites = ['协议推演，非 gem5 运行；C 是伪代码，延迟由教学调度设置。',
                       'ReadShared 为 Order=0；响应与数据采用 RespSepData/DataSepResp 分离。',
                       'HN 已发出全部数据拍后才处理后续请求；第一拍先到，只有第二拍在网络中晚于 snoop 到达。']
    s.requirements = ['Separate response/data', 'Early CompAck', 'DAT channel delay']
    s.results = {'RN0': 'I，先收到完整 x=9 再回应 snoop', 'RN1': 'UC，x=9',
                 '关键约束': 'SnpRespData 不得早于 RN0 收齐两拍 DataSepResp'}
    s.outcome = 'CompAck 保证完成响应的次序；收到 RespSepData 后，snoop 还必须等待数据全部到齐。'
    return s


def _compack_overtake():
    s = Scenario('compack-overtake', '新的 REQ 超过上一次 CompAck', 'ordering',
                 'RN 已经读完并静默逐出，但 HN 必须等旧 CompAck 才能处理同址新请求。',
                 'B5.6.4; B2.7.3', [318, 137])
    s.thread(0, 'P0 · 连续两次访问',
             '// C 伪代码；缓存压力使 clean 行静默逐出\n'
             'uint64_t first = chi_read(ReadShared, &x);\n'
             'cache_pressure_until_silent_evict(&x);\n'
             'uint64_t second = chi_read(ReadShared, &x);\n'
             'assert(first == 9 && second == 9);\n')
    old = s.requester('first-read', 0, 2)
    s.seed('RN0', _cache('I'))
    s.seed('HN0', _home('SC'))
    s.seed('SN0', _cache('SC'))
    first_req = _read(s, 1000, 'RN0', old)
    first_data = s.data(first_req.end + 1000, 'HN0', 'RN0', 'CompData', old, 1, 'SC',
                        txnid=10, dbid=50, desc='第一次读取已获得 clean shared 副本。')
    s.state(first_data.end, 'RN0', old, _cache('SC'))
    old_ack = _ack(s, first_data.end + 1000, 'RN0', old, delay=40000,
                   desc='旧 CompAck 先发出，但 RSP 通道延迟使它最后到达 HN。')
    s.done(old_ack.start, 'RN0', old)
    new = s.requester('second-read', 0, 4, tick=old_ack.start + 1000)
    evicted = s.state(old_ack.start + 1000, 'RN0', new, _cache('I'))
    s.note(old_ack.start + 1000, new, 'RN0 的 SC 副本静默逐出；这里没有发 Evict。', node='RN0')
    second_req = _read(s, old_ack.start + 2000, 'RN0', new, txn=11)
    assert second_req.end < old_ack.end
    wait = s.wait(second_req.end, 'HN0', new, 'same_address',
                  '新 REQ 已到；旧事务 DBID=50 的 CompAck 仍在途中。', blocker=old)
    s.done(old_ack.end, 'HN0', old)
    s.unwait(old_ack.end, 'HN0', new, wait)
    second_data = s.data(old_ack.end + 1000, 'HN0', 'RN0', 'CompData', new, 2, 'UC',
                         txnid=11, dbid=51, desc='旧 CompAck 到达并释放前一事务后，处理新请求。')
    s.state(second_data.end, 'RN0', new, _cache('UC'))
    new_ack = _ack(s, second_data.end + 1000, 'RN0', new, dbid=51)
    s.state(new_ack.end, 'HN0', new, _home('I', owner='RN0'))
    s.done(new_ack.start, 'RN0', new)
    s.done(new_ack.end, 'HN0', new)
    s.point('旧数据已返回', s.arrival(first_data))
    s.point('旧 CompAck 先发后到', old_ack)
    s.point('clean 行静默逐出', evicted)
    s.point('新请求先到 HN 并等待', _wait_event(s, wait))
    s.point('旧 Ack 后新数据才能发出', second_data)
    s.prerequisites = ['协议推演，非 gem5 运行；C 中的缓存压力操作只表示构造意图。',
                       '同一 RN 两次 ReadShared，两个不同的 REQ TxnID。',
                       'REQ 与 RSP 的不同网络延迟使新请求先于旧 CompAck 到达 HN。']
    s.requirements = ['Independent REQ/RSP ordering', 'Silent clean eviction']
    s.results = {'第一次读取': 9, '第二次读取': 9, 'RN0': 'UC',
                 'HN 释放顺序': 'DBID=50 的 CompAck → 第二请求处理'}
    s.outcome = 'RN 读完不等于 HN 已释放事务；新同址请求必须等待旧 CompAck。'
    return s


def _retry_invalidated():
    s = Scenario('retry-invalidated', '写回 Retry 后被失效：退还信用', 'retry',
                 'WBFull 等待 P-Credit 时仍要回答 snoop；失去副本后可取消写回并 PCrdReturn。',
                 'B2.10; B4.11.1', [178, 179, 180, 283])
    s.thread(0, 'P0 · 等待重试的写回',
             '// 初始 RN0=UD，x=9；内存仍为 0\n'
             'chi_copyback_async(WriteBackFull, &x);\n'
             'wait_completion_or_cancel();\n')
    s.thread(1, 'P1 · 接管脏行',
             '// C 伪代码；HN 的信用资源由教学调度控制\n'
             'uint64_t v = chi_read(ReadUnique, &x);\n'
             'assert(v == 9);\n')
    wb = s.requester('retried-writeback', 0, 2)
    read = s.requester('take-dirty-line', 1, 2, tick=7000)
    s.seed('RN0', _cache('UD'))
    s.seed('RN1', _cache('I'))
    s.seed('HN0', _home('I', owner='RN0'))
    s.seed('SN0', _cache('SC', LINE0))
    req = s.msg(1000, 'RN0', 'HN0', 'REQ', 'WriteBackFull', wb,
                fields={'TxnID': 20, 'AllowRetry': 1, 'PCrdType': 0, 'ExpCompAck': 0},
                desc='写回尚未获接受，RN0 仍持有 UD 数据。')
    retry = s.msg(req.end + 1000, 'HN0', 'RN0', 'RSP', 'RetryAck', wb,
                  fields={'TxnID': 20, 'PCrdType': 1}, desc='HN 缺少写回资源，要求等待类型 1 的 P-Credit。')
    wait = s.wait(retry.end, 'RN0', wb, 'pcredit',
                  '等待 PCrdType=1；等待信用不能阻止回答 snoop。')
    unique = _read(s, retry.end + 1000, 'RN1', read, txn=10, opcode='ReadUnique')
    snp = s.msg(unique.end + 1000, 'HN0', 'RN0', 'SNP', 'SnpUnique', read,
                fields={'TxnID': 30, 'RetToSrc': 0}, desc='写回仍在等信用，同址 snoop 正常处理。')
    dirty = s.data(snp.end + 1000, 'RN0', 'HN0', 'SnpRespData', read, 4, 'I',
                   txnid=30, desc='先通过 snoop 转移最新脏数据，RN0 变成 I。')
    s.state(snp.end + 1000, 'RN0', read, _cache('I'))
    result = s.data(dirty.end + 1000, 'HN0', 'RN1', 'CompData', read, 6, 'UD',
                    txnid=10, dbid=50, desc='RN1 接管 x=9 及脏责任；内存仍可为旧值 0。')
    s.state(result.end, 'RN1', read, _cache('UD'))
    ack = _ack(s, result.end + 1000, 'RN1', read)
    s.state(ack.end, 'HN0', read, _home('I', owner='RN1'))
    s.done(ack.start, 'RN1', read)
    s.done(ack.end, 'HN0', read)
    grant = s.msg(ack.end + 1000, 'HN0', 'RN0', 'RSP', 'PCrdGrant', wb,
                  fields={'TxnID': 0, 'PCrdType': 1},
                  desc='信用终于到达，但 RN0 已被失效，不再需要原 WriteBackFull。')
    s.unwait(grant.end, 'RN0', wb, wait)
    returned = s.msg(grant.end + 1000, 'RN0', 'HN0', 'REQ', 'PCrdReturn', wb,
                     fields={'TxnID': 0, 'PCrdType': 1, 'AllowRetry': 0},
                     desc='合法分支：取消待重试写回，归还类型 1 的信用；PCrdReturn 不要求响应。')
    s.done(returned.start, 'RN0', wb)
    s.done(returned.end, 'HN0', wb)
    s.point('写回被 RetryAck', retry)
    s.point('等信用时响应失效 snoop', snp)
    s.point('脏数据转到 RN1', result)
    s.point('信用晚到', s.arrival(grant))
    s.point('取消写回并退信用', returned)
    s.prerequisites = ['协议推演，非 gem5 运行；RN0=UD，x=9，SN 的旧值为 0。',
                       'WriteBackFull 收到 RetryAck 后没有被 HN 接受。',
                       '展示 PCrdReturn 取消分支；另一合法分支是带信用重发并回 CopyBackWriteData_I。']
    s.requirements = ['Request Retry', 'PCrdReturn', 'Snoop while waiting for credit']
    s.results = {'RN0': 'I；原 WB 已取消', 'RN1': 'UD，x=9', 'memory x': 0,
                 'PCrdType=1': '已通过 PCrdReturn 归还；无重发写回'}
    s.outcome = 'Retry 不是冻结缓存状态；被失效后取消写回时仍必须正确归还 P-Credit。'
    return s


def _multiple_snoops():
    s = Scenario('multiple-snoops', '一个写回连续遭遇两次 snoop', 'snoop',
                 'WriteBackFull 最终采用最后一次 snoop 后的状态；不能锁死在初始 UD 或中间 SC。',
                 'B4.11.1–B4.11.2; B5.6.1', [283, 284, 313, 314])
    s.thread(0, 'P0 · 被阻塞的写回',
             '// 初始 RN0=UD，x=9，内存=0\n'
             'chi_copyback_async(WriteBackFull, &x);\n'
             'wait_completion();\n')
    s.thread(1, 'P1 · 第一个共享读',
             '// C 伪代码；先由 HN 处理这个读\n'
             'uint64_t a = chi_read(ReadShared, &x);\n')
    s.thread(2, 'P2 · 随后的独占读',
             '// C 伪代码；此读排在写回之前\n'
             'uint64_t b = chi_read(ReadUnique, &x);\n')
    wb = s.requester('pending-writeback', 0, 2)
    shared = s.requester('first-shared', 1, 2)
    unique = s.requester('second-unique', 2, 2, tick=8000)
    for node, state in [('RN0', 'UD'), ('RN1', 'I'), ('RN2', 'I')]:
        s.seed(node, _cache(state))
    s.seed('HN0', _home('I', owner='RN0'))
    s.seed('SN0', _cache('SC', LINE0))
    req1 = _read(s, 1000, 'RN1', shared)
    req2 = _read(s, 8000, 'RN2', unique, txn=11, opcode='ReadUnique')
    wbreq = s.msg(1000, 'RN0', 'HN0', 'REQ', 'WriteBackFull', wb,
                  fields={'TxnID': 20, 'ExpCompAck': 0, 'CAH': 0, 'AllowRetry': 1}, delay=13000,
                  desc='写回进入网络，HN 把它排在两次读之后处理。')
    wait2 = s.wait(req2.end, 'HN0', unique, 'same_address', '先完成 RN1 的 ReadShared。', blocker=shared)
    waitwb = s.wait(wbreq.end, 'HN0', wb, 'same_address', '等待两个更早获调度的读事务。', blocker=shared)
    snp1 = s.msg(req1.end + 1000, 'HN0', 'RN0', 'SNP', 'SnpShared', shared,
                 fields={'TxnID': 30, 'RetToSrc': 0}, desc='第一个 snoop：RN0 的 UD 降为 SC。')
    dirty = s.data(snp1.end + 1000, 'RN0', 'HN0', 'SnpRespData', shared, 5, 'SC',
                   txnid=30, desc='返回 x=9 并把脏责任交给 HN；RN0 保留 SC。')
    s.state(snp1.end + 1000, 'RN0', shared, _cache('SC'))
    s.state(dirty.end, 'HN0', shared, _home('SD', sharers=['RN0'], dirty=True))
    first = s.data(dirty.end + 1000, 'HN0', 'RN1', 'CompData', shared, 1, 'SC',
                   txnid=10, dbid=50, desc='HN 保留脏责任，RN1 得到 clean shared 数据。')
    s.state(first.end, 'RN1', shared, _cache('SC'))
    ack1 = _ack(s, first.end + 1000, 'RN1', shared)
    s.state(ack1.end, 'HN0', shared, _home('SD', sharers=['RN0', 'RN1'], dirty=True))
    s.done(ack1.start, 'RN1', shared)
    s.done(ack1.end, 'HN0', shared)
    s.unwait(ack1.end, 'HN0', unique, wait2)
    snp2 = s.msg(ack1.end + 1000, 'HN0', 'RN0', 'SNP', 'SnpUnique', unique,
                 fields={'TxnID': 31, 'RetToSrc': 0},
                 desc='第二个 snoop 晚于第一 snoop 的完整响应；RN0 由 SC 变 I。')
    snp_other = s.msg(ack1.end + 1000, 'HN0', 'RN1', 'SNP', 'SnpUnique', unique,
                      fields={'TxnID': 32, 'RetToSrc': 0}, desc='同时失效另一个 sharer RN1。')
    rsp0 = s.msg(snp2.end + 1000, 'RN0', 'HN0', 'RSP', 'SnpResp', unique,
                 fields={'TxnID': 31, 'Resp': 0, 'CacheState': 'I'},
                 desc='SC 且 RetToSrc=0，只返回失效响应。')
    rsp1 = s.msg(snp_other.end + 1000, 'RN1', 'HN0', 'RSP', 'SnpResp', unique,
                 fields={'TxnID': 32, 'Resp': 0, 'CacheState': 'I'})
    s.state(rsp0.start, 'RN0', unique, _cache('I'))
    s.state(rsp1.start, 'RN1', unique, _cache('I'))
    second = s.data(max(rsp0.end, rsp1.end) + 1000, 'HN0', 'RN2', 'CompData', unique, 6, 'UD',
                    txnid=11, dbid=51, desc='所有其他副本已失效；HN 把数据和脏责任交给 RN2。')
    s.state(second.end, 'RN2', unique, _cache('UD'))
    ack2 = _ack(s, second.end + 1000, 'RN2', unique, dbid=51)
    s.state(ack2.end, 'HN0', unique, _home('I', owner='RN2'))
    s.done(ack2.start, 'RN2', unique)
    s.done(ack2.end, 'HN0', unique)
    s.unwait(ack2.end, 'HN0', wb, waitwb)
    dbid = s.msg(ack2.end + 1000, 'HN0', 'RN0', 'RSP', 'CompDBIDResp', wb,
                 fields={'TxnID': 20, 'DBID': 60}, desc='两个读都结束后才允许原写回继续。')
    empty = s.data(dbid.end + 1000, 'RN0', 'HN0', 'CopyBackWriteData', wb, 0, 'I',
                   value=LINE0, be=LINE0, txnid=60,
                   desc='最后一次 snoop 后为 I：两拍 BE 全零、data 全零，不能再次交付旧值。')
    s.done(empty.end, 'HN0', wb)
    s.done(empty.end, 'RN0', wb)
    s.point('第一次 snoop：UD → SC', snp1)
    s.point('第二次 snoop：SC → I', snp2)
    s.point('脏责任最终给 RN2', second)
    s.point('原写回最后获得 DBID', s.arrival(dbid))
    s.point('写回采用最后的 I 状态', empty)
    s.prerequisites = ['协议推演，非 gem5 运行；初始 RN0=UD，x=9，内存=0。',
                       'HN 把同址 WB 排在两个读之后；先 SnpShared，再 SnpUnique。',
                       '同 RN 同址的后一个 snoop 必须等待前一个 snoop 的响应；本例还等待相应读完成。']
    s.requirements = ['Multiple same-address snoops', 'HN retains dirty responsibility']
    s.results = {'RN0': 'I；WB 发送两拍零 BE/零数据', 'RN1': 'I', 'RN2': 'UD，x=9', 'memory x': 0}
    s.outcome = 'pending CopyBack 的状态必须随每个 snoop 更新；最后一次 snoop 决定最终 CopyBack 响应。'
    return s


def _data_before_response():
    s = Scenario('data-before-response', 'DAT 先到，完成响应后到', 'ordering',
                 'DataSepResp 可以先到；仅收到数据不能提前发送要求等待 RespSepData 的 CompAck。',
                 'B2.3.1.1; B2.4.3; B2.7.3', [57, 58, 103, 104, 137])
    s.thread(0, 'P0 · 分离响应读方',
             '// C 伪代码；RSP 通道延迟由教学调度设置\n'
             'uint64_t v = chi_read(ReadShared, &x);\n'
             'assert(v == 9);\n')
    root = s.requester('data-first-read', 0, 2)
    s.seed('RN0', _cache('I'))
    s.seed('HN0', _home('SC'))
    s.seed('SN0', _cache('SC'))
    req = _read(s, 1000, 'RN0', root)
    data = s.data(req.end + 1000, 'HN0', 'RN0', 'DataSepResp', root, 2, 'UC',
                  txnid=10, dbid=50, desc='两个 256-bit 数据拍先抵达；DBID 与稍后的 RespSepData 一致。')
    rsp = s.msg(req.end + 1000, 'HN0', 'RN0', 'RSP', 'RespSepData', root,
                fields={'TxnID': 10, 'DBID': 50}, delay=24000,
                desc='RespSepData 与数据同时发出，但独立 RSP 通道使它最后抵达。')
    assert data.end < rsp.end
    wait = s.wait(data.end, 'RN0', root, 'completion_pending',
                  '数据已收齐，仍等待 RespSepData，尚不能发送 CompAck。')
    s.unwait(rsp.end, 'RN0', root, wait)
    s.state(rsp.end, 'RN0', root, _cache('UC'))
    ack = _ack(s, rsp.end + 1000, 'RN0', root,
               desc='两部分均已收到；CompAck 使用 RespSepData/DataSepResp 的一致 DBID=50。')
    s.state(ack.end, 'HN0', root, _home('I', owner='RN0'))
    s.done(ack.start, 'RN0', root)
    s.done(ack.end, 'HN0', root)
    s.point('数据拍先返回', s.arrival(data))
    s.point('全行到齐仍等待响应', _wait_event(s, wait))
    s.point('延迟的完成响应到达', s.arrival(rsp))
    s.point('收到 RespSepData 才 CompAck', ack)
    s.prerequisites = ['协议推演，非 gem5 运行；数据与响应走独立通道。',
                       'ReadShared 使用 RespSepData + DataSepResp，Order=0。',
                       '早确认只允许在收到 RespSepData 后；只有 DAT 不满足此条件。']
    s.requirements = ['Separate response/data', 'Independent DAT/RSP ordering']
    s.results = {'RN0': 'UC，x=9', '数据与响应 DBID': 50,
                 '完成条件': '两拍 DataSepResp 和 RespSepData 全部接收'}
    s.outcome = '分离响应允许两种到达次序；数据完整和完成响应都要单独跟踪。'
    return s


def _evict_snoop():
    s = Scenario('evict-snoop', 'Evict 在途，旧目录仍触发 snoop', 'snoop',
                 'RN 已逐出 clean 行而 HN 尚未收到 Evict；随后 snoop 必须得到 SnpResp_I。',
                 'B5.6.1; B4.7.2', [314, 260])
    s.thread(0, 'P0 · clean 行逐出',
             '// 初始 RN0=UC，x=9，内存也是 9\n'
             'chi_evict_async(&x);\n'
             'wait_completion();\n')
    s.thread(1, 'P1 · 读取同址',
             '// C 伪代码；Evict 在 REQ 通道上延迟\n'
             'uint64_t v = chi_read(ReadShared, &x);\n'
             'assert(v == 9);\n')
    evict = s.requester('pending-evict', 0, 2)
    read = s.requester('read-after-eviction', 1, 2, tick=3000)
    s.seed('RN0', _cache('UC'))
    s.seed('RN1', _cache('I'))
    s.seed('HN0', _home('I', owner='RN0'))
    s.seed('SN0', _cache('SC'))
    lost = s.state(1000, 'RN0', evict, _cache('I'))
    ereq = s.msg(1000, 'RN0', 'HN0', 'REQ', 'Evict', evict,
                 fields={'TxnID': 20, 'ExpCompAck': 0, 'AllowRetry': 1}, delay=22000,
                 desc='clean 行已经逐出，Evict 通知仍在 mesh 中。')
    req = _read(s, 3000, 'RN1', read)
    snp = s.msg(req.end + 1000, 'HN0', 'RN0', 'SNP', 'SnpShared', read,
                fields={'TxnID': 30, 'RetToSrc': 0},
                desc='HN 的旧 SF 仍记录 RN0 持有副本，向 RN0 发 snoop。')
    absent = s.msg(snp.end + 1000, 'RN0', 'HN0', 'RSP', 'SnpResp', read,
                   fields={'TxnID': 30, 'Resp': 0, 'CacheState': 'I'},
                   desc='outstanding Evict 的明确规则：只能回答 SnpResp_I，不能返回旧 clean 数据。')
    mem = s.msg(absent.end + 1000, 'HN0', 'SN0', 'REQ', 'ReadNoSnp', read,
                fields={'TxnID': 40, 'ExpCompAck': 0, 'AllowRetry': 1, 'SnpAttr': 0}, leg=read + '/memory',
                desc='确认 RN0 无数据后，从 SN 读取干净的最新值。')
    memdata = s.data(mem.end + 1000, 'SN0', 'HN0', 'CompData', read, 0, 'I',
                     txnid=40, leg=read + '/memory', desc='内存返回 x=9。')
    answer = s.data(memdata.end + 1000, 'HN0', 'RN1', 'CompData', read, 2, 'UC',
                    txnid=10, dbid=50, desc='系统没有其他有效副本，ReadShared 可取得 UC。')
    s.state(answer.end, 'RN1', read, _cache('UC'))
    ack = _ack(s, answer.end + 1000, 'RN1', read)
    assert ereq.end < ack.end
    wait = s.wait(ereq.end, 'HN0', evict, 'same_address',
                  'Evict 通知后来到达；先完成已经开始的 ReadShared。', blocker=read)
    s.state(ack.end, 'HN0', read, _home('I', owner='RN1'))
    s.done(ack.start, 'RN1', read)
    s.done(ack.end, 'HN0', read)
    s.unwait(ack.end, 'HN0', evict, wait)
    comp = s.msg(ack.end + 1000, 'HN0', 'RN0', 'RSP', 'Comp', evict,
                 fields={'TxnID': 20, 'Resp': 0, 'CacheState': 'I'},
                 desc='完成 RN0 的 Evict；不会删掉现在属于 RN1 的目录状态，也不需要 CompAck。')
    s.done(comp.start, 'HN0', evict)
    s.done(comp.end, 'RN0', evict)
    s.point('RN0 已经丢弃 clean 行', lost)
    s.point('Evict 仍在飞行', ereq)
    s.point('旧 SF 触发 snoop', snp)
    s.point('必须回答 SnpResp_I', absent)
    s.point('SN 提供正确数据', memdata)
    s.point('后来完成 Evict', s.arrival(comp))
    s.prerequisites = ['协议推演，非 gem5 运行；初始 RN0=UC，x=9，内存也是 9。',
                       '发送 Evict 时 RN0 已为 I；HN 还未收到这一通知。',
                       'Evict 无 DAT；它不能套用 WriteEvictFull 或 WriteBackFull 的规则。']
    s.requirements = ['Evict', 'Stale directory during network transit']
    s.results = {'RN0': 'I', 'RN1': 'UC，x=9', 'memory x': 9,
                 'snoop 响应': 'SnpResp_I；没有 RN0 → HN0 DAT'}
    s.outcome = '目录命中不保证 RN 此刻仍有行；Evict 在途时的 snoop 必须返回 I。'
    return s


def build_cases() -> list[Scenario]:
    return [_split_early_snoop(), _compack_overtake(), _retry_invalidated(),
            _multiple_snoops(), _data_before_response(), _evict_snoop()]
