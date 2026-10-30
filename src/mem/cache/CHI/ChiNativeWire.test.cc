// Standalone C++17 test: no gem5 runtime, Python, CHIron, or refiner involved.
#include "ChiNativeWire.hh"
#include <iostream>
#include <limits>

using namespace gem5::Chi;
using namespace gem5::Chi::NativeWire;

namespace {
unsigned checks = 0;
void check(bool result, const char* description)
{
    ++checks;
    if (!result) throw std::runtime_error(description);
}
template<class Fn> void invalid(Fn fn, const char* description)
{
    bool rejected = false;
    try { fn(); } catch (const std::invalid_argument&) { rejected = true; }
    check(rejected,description);
}
NativeDat dataPrototype(unsigned opcode = 4)
{
    NativeDat d;
    d.nativeChi = true; d.srcid=4; d.tgtid=0; d.txnid=0xdef;
    d.opcode=opcode; d.HomeNID=opcode==4?4:0; d.dbid=opcode==4?0xabc:0;
    d.resp=opcode==4?1:opcode==2?6:0;
    return d;
}
std::vector<uint8_t> bytes()
{
    std::vector<uint8_t> result(64);
    for (unsigned i=0;i<result.size();++i) result[i]=uint8_t(i+1);
    return result;
}
void scalarFields()
{
    for (unsigned code=0;code<=6;++code) {
        check(sizeBytes(code)==(1u<<code),"Size decoding");
        check(sizeCode(1u<<code)==code,"Size encoding");
    }
    invalid([]{sizeCode(0);},"Zero-sized transaction rejected");
    invalid([]{sizeCode(24);},"Non-power-of-two size rejected");
    invalid([]{sizeCode(128);},"Oversized transaction rejected");
    invalid([]{sizeBytes(7);},"Reserved Size rejected");
    const uint64_t pa=0x1234567880;
    check(snoopAddress(pa)==(pa>>3),"SNP Addr is PA>>3");
    check(physicalSnoopAddress(snoopAddress(pa))==pa,"SNP Addr round trip");
    invalid([]{snoopAddress(0x81);},"SNP low address bits rejected");
    invalid([]{physicalSnoopAddress(UINT64_MAX);},"SNP decode overflow rejected");
    check(dataId(0)==0&&dataId(31)==0&&dataId(32)==2&&dataId(63)==2,"256-bit DataID allocation");
    check(dataOffset(0)==0&&dataOffset(2)==32,"DataID natural line offset");
    invalid([]{dataOffset(1);},"DataID1 rejected");
    invalid([]{dataOffset(3);},"DataID3 rejected");
    invalid([]{dataId(64);},"Offset outside line rejected");
}
void validation()
{
    NativeReq q;q.nativeChi=true;q.opcode=0x26;q.size=6;q.AllowRetry=1;q.txnid=4095;q.srcid=2047;
    check(validate(q).empty(),"Full 12-bit TxnID accepted");
    auto bad=q;bad.txnid=4096;check(!validate(bad).empty(),"TxnID overflow rejected");
    bad=q;bad.size=64;check(!validate(bad).empty(),"Coarse byte-count Size rejected");
    bad=q;bad.opcode=0x5b;check(!validate(bad).empty(),"Coarse write opcode rejected");
    bad=q;bad.pcrdtype=3;check(!validate(bad).empty(),"First request credit type rejected");
    bad=q;bad.srcid=2048;check(!validate(bad).empty(),"NodeID width enforced");
    bad=q;bad.ReturnTxnId=4096;check(!validate(bad).empty(),"ReturnTxnID width enforced");
    bad=q;bad.addr=uint64_t(1)<<48;check(!validate(bad).empty(),"PA width enforced");
    bad=q;bad.nativeChi=false;bad.size=64;bad.opcode=0x5b;
    check(validate(bad).empty(),"Coarse messages left for old mode");
    check(!validate(DAT,NativeFlitVariant{q}).empty(),"Channel/type mismatch rejected");
    NativeRsp r;r.nativeChi=true;r.opcode=6;r.dbid=0xabc;
    check(validate(r).empty()&&r.dbid==0xabc,"DBID values above255 survive storage");
    r.dbid=0x1000;check(!validate(r).empty(),"DBID overflow rejected");
    NativeSnp s;s.nativeChi=true;s.opcode=7;s.addr=snoopAddress(0x1234000);s.size=255;s.doNotGoToSD=1;
    check(validate(s).empty(),"SNP legacy Size is not a protocol field");
    s.addr=uint64_t(1)<<45;check(!validate(s).empty(),"SNP encoded address width enforced");
    s.addr=0;s.doNotGoToSD=0;check(!validate(s).empty(),"SnpUnique requires DoNotGoToSD=1");
    s.doNotGoToSD=1;s.opcode=9;s.retToSrc=1;check(!validate(s).empty(),"SnpCleanInvalid requires RetToSrc=0");
}
void fullLineRead()
{
    const auto line=bytes();auto beats=packData(dataPrototype(),line,{},0x1040,64);
    check(beats.size()==2,"Full read produces two physical flits");
    check(beats[0].dataid==0&&beats[1].dataid==2,"Full read native DataIDs");
    check(beats[0].data.size()==32&&beats[1].data.size()==32,"Fixed physical DAT width");
    check(beats[0].dbid==0xabc&&beats[1].dbid==0xabc,"12-bit completion DBID preserved");
    check(std::all_of(beats[0].byteEnable.begin(),beats[0].byteEnable.end(),[](auto b){return b==0;}),"Read BE intentionally inapplicable");
    DataAssembler assembly(0x1040,64);
    beats[1].beatOffset=999;beats[1].last=0;beats[0].beatOffset=888;beats[0].last=0;
    check(assembly.accept(beats[1]).empty(),"Upper beat can arrive first");
    check(!assembly.complete(),"Upper beat alone is not completion");
    check(assembly.accept(beats[0]).empty(),"Lower beat accepted despite irrelevant metadata");
    check(assembly.complete()&&assembly.allKnown(),"Completion from full DataID coverage");
    check(assembly.data()==line,"Read data reconstructed byte-for-byte");
    check(!assembly.accept(beats[0]).empty(),"Duplicate read rejected after completion");
    for (auto& d:beats) std::fill(d.byteEnable.begin(),d.byteEnable.end(),1);
    DataAssembler ones(0x1040,64);ones.accept(beats[0]);ones.accept(beats[1]);
    check(ones.data()==assembly.data(),"Read BE does not affect data validity");
}
void partialNaturalLanes()
{
    const auto line=bytes();auto reads=packData(dataPrototype(),line,{},0x1028,8);
    check(reads.size()==1&&reads[0].dataid==2,"8B at PA+40 uses upper beat");
    check(reads[0].data[8]==line[40]&&reads[0].data[15]==line[47],"8B occupies natural lanes8..15");
    check(reads[0].data[0]==0&&reads[0].data[16]==0,"Outside read window padding");
    DataAssembler assembly(0x1028,8);check(assembly.accept(reads[0]).empty()&&assembly.complete(),"Partial read completion");
    check(assembly.data()[40]==line[40]&&assembly.validBytes()[40]&&!assembly.validBytes()[39],"Partial read validity window");
    std::vector<uint8_t> be(64,0);be[40]=be[43]=be[47]=1;
    auto writes=packData(dataPrototype(3),line,be,0x1028,8);
    check(writes.size()==1&&writes[0].data.size()==32,"Partial write fixed DAT width");
    check(writes[0].byteEnable[8]&&writes[0].byteEnable[11]&&writes[0].byteEnable[15],"Partial BE natural lanes");
    check(writes[0].data[9]==0&&!writes[0].byteEnable[9],"BE0 write byte is zeroed at production");
    DataAssembler accepted(0x1028,8);check(accepted.accept(writes[0]).empty(),"Partial write accepted");
    check(accepted.byteEnable()[40]&&!accepted.byteEnable()[41],"Partial write mask reconstructed");
    auto malformed=writes[0];malformed.data[1]=1;malformed.byteEnable[1]=1;
    DataAssembler rejected(0x1028,8);check(!rejected.accept(malformed).empty(),"BE outside Addr/Size rejected");
    check(!rejected.complete(),"Rejected beat does not change assembly");
    auto wrong=reads[0];wrong.dataid=0;DataAssembler wrongId(0x1028,8);
    check(!wrongId.accept(wrong).empty(),"DataID outside partial window rejected");
}
void malformedAndUnknown()
{
    const auto line=bytes();const auto original=packData(dataPrototype(),line,{},0,64);
    auto bad=original[0];bad.dataid=1;check(!validate(bad).empty(),"Native DAT DataID1 rejected");
    bad=original[0];bad.data.resize(8);check(!validate(bad).empty(),"Variable-width DAT rejected");
    bad=original[0];bad.byteEnable[0]=2;check(!validate(bad).empty(),"Non-bit BE rejected");
    bad=original[0];bad.chunkValid.resize(3);check(!validate(bad).empty(),"Malformed validity metadata rejected");
    auto mismatch=original[1];mismatch.HomeNID=8;
    DataAssembler assembly;assembly.accept(original[0]);check(!assembly.accept(mismatch).empty(),"Cross-beat HomeNID change rejected");
    mismatch=original[1];++mismatch.dbid;check(!assembly.accept(mismatch).empty(),"Cross-beat completion DBID change rejected");
    mismatch=original[1];++mismatch.resp;check(!assembly.accept(mismatch).empty(),"Cross-beat Resp change rejected");
    check(!assembly.complete(),"Rejected fields never complete transfer");
    auto unknown=original[1];unknown.chunkValid[1]=0;
    check(assembly.accept(unknown).empty()&&assembly.complete(),"Unknown chunk preserves geometry completion");
    check(!assembly.allKnown()&&!assembly.validBytes()[40],"Unknown chunk is not fabricated as known");
    auto coarse=original[0];coarse.nativeChi=false;DataAssembler native;
    check(!native.accept(coarse).empty(),"Native reassembly rejects coarse flit");
}
void writeAndSnoopFields()
{
    const auto line=bytes();auto d=dataPrototype(2);d.txnid=0xabc;
    auto writes=packData(d,line,{},0,64);
    check(writes[0].txnid==0xabc&&writes[1].txnid==0xabc,"Write DAT preserves caller DBID in TxnID");
    check(writes[0].HomeNID==0,"Write HomeNID zero");
    auto bad=writes[0];bad.HomeNID=4;check(!validate(bad).empty(),"Write HomeNID nonzero rejected");
    bad=writes[0];bad.byteEnable[0]=0;check(!validate(bad).empty(),"BE0 nonzero known data rejected");
    auto invalidated=dataPrototype(2);invalidated.resp=0;
    auto empty=packData(invalidated,line,std::vector<uint8_t>(64,0),0,64);
    check(validate(empty[0]).empty(),"CopyBackWriteData_I zeroBE allowed");
    bad=empty[0];bad.byteEnable[0]=1;check(!validate(bad).empty(),"CopyBackWriteData_I nonzeroBE rejected");
    auto snoops=packData(dataPrototype(1),line,{},0,64);
    check(snoops.size()==2&&validate(snoops[0]).empty(),"Full snoop data native encoding");
    bad=snoops[0];bad.dbid=1;check(!validate(bad).empty(),"Snoop noDataPull DBID must zero");
    bad=snoops[0];bad.byteEnable[0]=0;bad.data[0]=0;check(!validate(bad).empty(),"SnpRespData requires fullBE");
    // B2.9.4 permits variation in inapplicable WriteData DBID, unlike CompData.
    writes[1].dbid=0x777;DataAssembler writeAssembly;
    check(writeAssembly.accept(writes[0]).empty()&&writeAssembly.accept(writes[1]).empty(),"Inapplicable write DBID not used as transaction identity");
}
}

int main()
{
    try {
        scalarFields(); validation(); fullLineRead(); partialNaturalLanes();
        malformedAndUnknown(); writeAndSnoopFields();
        std::cout << "ChiNativeWire: " << checks << " checks passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "ChiNativeWire check " << checks << " failed: " << error.what() << '\n';
        return 1;
    }
}
