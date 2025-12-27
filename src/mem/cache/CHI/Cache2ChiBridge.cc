#ifndef __CACHE2CHIBRIDGE_CC__
#define __CACHE2CHIBRIDGE_CC__

#include "mem/cache/CHI/Cache2ChiBridge.hh"

#include <cassert>

#include "base/logging.hh"
#include "mem/packet.hh"
#include "sim/eventq.hh"
#include "sim/sim_object.hh"

namespace gem5
{

Cache2ChiBridge::Cache2ChiBridge(const Cache2ChiBridgeParams& p)
    : ClockedObject(p)
    , cachePort(csprintf("%s.cache_side", name()), *this)
    // 下面这个 chiPort 构造参数，按你 ChiCommonPort 的构造函数签名对齐
    // 你之前 Port(name,id) 的版本第二参是 PortID，所以这里给一个 id（例如 0）
    , chiPort(csprintf("%s.chi_side", name()),  /*id*/ 0)
    , pumpEvent([this]{ pump(); }, name() + ".pumpEvent")
{
}

Cache2ChiBridge::CacheSidePort::CacheSidePort(const std::string& name, Cache2ChiBridge& owner)
    : ResponsePort(name,&owner), owner(owner)
    {
        // Constructor implementation
    }



Port&
Cache2ChiBridge::getPort(const std::string& if_name, PortID idx)
{
    // Python 里用 system.bridge.cache_side / chi_side 连接时会进这里
    if (if_name == "cache_side") {
        return cachePort;
    }
    if (if_name == "chi_side") {
        return chiPort;
    }

    return ClockedObject::getPort(if_name, idx);
}

void
Cache2ChiBridge::schedulePump()
{
    if (!pumpEvent.scheduled()) {
        // nextCycle() 是 ClockedObject 的方法：返回下一个时钟边沿 tick
        schedule(pumpEvent, nextCycle());
    }
}

/* =========================
 * Packet -> RawReq (最小可用版)
 * =========================
 * 目前做：Read -> ReadShared (REQ opcode base=0x01, variant0)
 * 其他暂不支持，后面你再逐步扩展映射表
 */
RawReq
Cache2ChiBridge::packetToRawReq(PacketPtr pkt) const
{
    RawReq r{};

    // 这些接口是 gem5 Packet 常用接口，如果你那份有差异就改这里
    r.addr = pkt->getAddr();
    r.size = static_cast<uint8_t>(pkt->getSize());
    r.hdr.qos  = pkt->qosValue();

    r.hdr.srcid = static_cast<int>(pkt->requestorId());
    r.hdr.tgtid = 0;           // TODO：后续你可以根据地址映射到 HNF/SNF
    r.AllowRetry = 0;      // TODO：如果你要实现 retry 机制再开启

    // 最小实现：只支持 read
    if (pkt->isRead()) {
        // ReadShared (Opcode[5:0]=0x01, Opcode[6]=0)
        r.hdr.opcode = 0x01;
        return r;
    }

    if (pkt->isWrite()) {
        // TODO: 把 write 映射到 WriteUnique* / WriteBack* 等
        panic("Cache2ChiBridge: write packet -> CHI req not implemented yet\n");
    }

    panic("Cache2ChiBridge: unsupported packet type -> CHI req\n");
}

/* =========================
 * Cache side callbacks
 * ========================= */

bool
Cache2ChiBridge::cacheRecvTimingReq(PacketPtr pkt)
{
    // 最简单：先收下来，放 pending，然后触发 pump 再发到 CHI
    pendingReqPkts.push(pkt);
    schedulePump();
    return true;
}

Tick
Cache2ChiBridge::cacheRecvAtomic(PacketPtr pkt)
{
    // 最小版本：不支持 atomic（你后面做 atomic opcode 再补）
    panic("Cache2ChiBridge: atomic access not supported yet\n");
}

void
Cache2ChiBridge::cacheRecvFunctional(PacketPtr pkt)
{
    // 最小版本：直接走 timing 路径或者忽略（按你需求）
    // 这里可以选择：立刻转成 RawReq 并 enqueue（不受 timing 约束）
    // 我先给一个保守实现：当成 timing req
    (void)cacheRecvTimingReq(pkt);
}

bool
Cache2ChiBridge::cacheRecvTimingSnoopResp(PacketPtr pkt)
{
    // 如果 L2 会回 snoop resp，这里未来要接起来
    // 最小版本先不做
    warn("Cache2ChiBridge: snoop resp not handled yet\n");
    return(false);
}

/* =========================
 * pump(): 从 pending 队列推进到 CHI；从 CHI 收到的 rsp/dat 推回 cache
 * ========================= */
void
Cache2ChiBridge::pump()
{
    // 1) cache -> CHI (REQ)
    while (!pendingReqPkts.empty()) {
        PacketPtr pkt = pendingReqPkts.front();

        // 先翻译 packet -> RawReq
        RawReq r = packetToRawReq(pkt);

        // credit gating：建议你在 ChiCommonPort 里提供 canSendReq()/enqueueReq()
        // enqueueReq() 内部做：CreditValue++ + assert(CreditValue<=CreditLimit)
        //
        // 如果你还没写这两个接口，请在 ChiCommonPort 加：
        //   bool canSendReq() const;
        //   void enqueueReq(const RawReq&);
        //
        if (!chiPort.canSendReq()) {
            // credit 不够：等 credit return 后再 pump
            break;
        }

        chiPort.enqueueReq(r);
        pendingReqPkts.pop();

        // TODO：真实建模里你要保存 pkt 用于将来响应匹配（比如 MSHR / txnid）
        // 最小版本先不做匹配，仅演示推进链路
    }

    // 2) CHI -> cache (RSP/DAT)
    //
    // 这块你需要 ChiCommonPort 提供：是否有 rsp/dat、pop 接口
    // 例如：
    //   bool hasRsp() const; RawRsp popRsp();
    //   bool hasDat() const; RawDat popDat();
    //
    // 然后把 RawRsp/RawDat 组装成 PacketPtr，再 sendTimingResp 给 cache。
    //
    // 最小版本先留 TODO（否则你还没实现 SNF/HNF 返回，也没法组包）
    //
    // while (chiPort.hasRsp()) { ... cachePort.sendTimingResp(pkt); }
    // while (chiPort.hasDat()) { ... cachePort.sendTimingResp(pkt); }

    // 如果还有工作没做完（比如 pending 队列还没空、或者 chi 有东西），再 schedule 一次
    if (!pendingReqPkts.empty() /*|| chiPort.hasRsp() || chiPort.hasDat()*/) {
        schedulePump();
    }
}



}

#endif // __CACHE2CHIBRIDGE_CC__
