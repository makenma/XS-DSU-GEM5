#include "mem/packet_access.hh"
#include "uartlite.hh"

namespace gem5
{

namespace
{

constexpr uint8_t StatusRxValid = 0x01;
constexpr uint8_t StatusTxEmpty = 0x04;

void
setRegister(PacketPtr pkt, uint8_t value)
{
    if (pkt->getSize() == sizeof(uint8_t)) {
        pkt->setLE<uint8_t>(value);
    } else if (pkt->getSize() == sizeof(uint32_t)) {
        pkt->setLE<uint32_t>(value);
    } else {
        panic("Unsupported uartlite read size %u\n", pkt->getSize());
    }
}

uint8_t
getRegister(PacketPtr pkt)
{
    if (pkt->getSize() == sizeof(uint8_t))
        return pkt->getLE<uint8_t>();
    if (pkt->getSize() == sizeof(uint32_t))
        return pkt->getLE<uint32_t>();

    panic("Unsupported uartlite write size %u\n", pkt->getSize());
}

} // anonymous namespace

Tick UartLite::read(PacketPtr pkt)
{
    assert(pkt->getAddr() >= pioAddr && pkt->getAddr() < pioAddr + pioSize);
    auto offset = pkt->getAddr() - pioAddr;

    switch (offset) {
        case UARTLITE_RX_FIFO:
            setRegister(pkt,
                    device->dataAvailable() ? device->readData() : 0);
            break;
        case UARTLITE_STAT_REG:
            setRegister(pkt, StatusTxEmpty |
                    (device->dataAvailable() ? StatusRxValid : 0));
            break;
        case UARTLITE_CTRL_REG:
            setRegister(pkt, 0);
            break;
        default:
            warn("Read to other uartlite addr %i is not implemented\n",
                 offset);
            setRegister(pkt, 0);
    }
    pkt->makeAtomicResponse();
    return pioDelay;
}

Tick UartLite::write(PacketPtr pkt)
{
    assert(pkt->getAddr() >= pioAddr && pkt->getAddr() < pioAddr + pioSize);
    auto offset = pkt->getAddr() - pioAddr;

    switch (offset) {
        case UARTLITE_TX_FIFO:
            device->writeData(getRegister(pkt));
            break;
        case UARTLITE_CTRL_REG:
            // FIFO reset and interrupt-enable writes are harmless for the
            // unbuffered, polling model used by the XiangShan platform.
            break;
        default:
            warn("Write to other uartlite addr %i is not implemented\n",
                 offset);
    }

    pkt->makeAtomicResponse();
    return pioDelay;
}

UartLite::UartLite(const UartLiteParams *params)
    : BasicPioDevice(*params, params->pio_size), device(params->device)
{
}

gem5::UartLite *UartLiteParams::create() const { return new UartLite(this); }

}  // namespace gem5
