package ru.serega6531.packmate.pcap;

import ru.serega6531.packmate.model.enums.Protocol;

public class NoOpPcapWorker implements PcapWorker {
    @Override
    public void start() {
    }

    @Override
    public void stop() {
    }

    @Override
    public void closeAllStreams(Protocol protocol) {
    }

    @Override
    public int closeTimeoutStreams(Protocol protocol, long timeoutMillis) {
        return 0;
    }

    @Override
    public void setFilter(String filter) {
    }

    @Override
    public String getExecutorState() {
        return "none";
    }
}
