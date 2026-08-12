package ru.serega6531.packmate.pcap;

import lombok.SneakyThrows;
import lombok.extern.slf4j.Slf4j;
import org.apache.commons.lang3.concurrent.BasicThreadFactory;
import org.pcap4j.core.PcapNativeException;
import org.pcap4j.core.PcapNetworkInterface;
import org.pcap4j.core.Pcaps;
import ru.serega6531.packmate.exception.PcapInterfaceNotFoundException;
import ru.serega6531.packmate.service.ServicesService;
import ru.serega6531.packmate.service.StreamService;

import java.net.InetAddress;
import java.net.UnknownHostException;
import java.util.List;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

@Slf4j
public class LivePcapWorker extends AbstractPcapWorker {

    private final PcapNetworkInterface device;

    public LivePcapWorker(ServicesService servicesService,
                          StreamService streamService,
                          InetAddress localIp,
                          String interfaceName) throws PcapNativeException, UnknownHostException {
        super(servicesService, streamService, localIp);
        device = Pcaps.getDevByName(interfaceName);

        if (device == null) {
            List<String> existingInterfaces = Pcaps.findAllDevs().stream().map(PcapNetworkInterface::getName).toList();
            throw new PcapInterfaceNotFoundException(interfaceName, existingInterfaces);
        }

        BasicThreadFactory factory = new BasicThreadFactory.Builder()
                .namingPattern("pcap-processor").build();
        processorExecutorService = new ThreadPoolExecutor(1, 1, 0L, TimeUnit.MILLISECONDS, new LinkedBlockingQueue<>(), factory);
    }

    public void start() throws PcapNativeException {
        log.info("Using interface " + device.getName());
        pcap = device.openLive(65536, PcapNetworkInterface.PromiscuousMode.PROMISCUOUS, 100);

        applyFilter();

        loopExecutorService.execute(() -> {
            try {
                log.info("Intercept started");
                pcap.loop(-1, this);
            } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
                // выходим
            } catch (Exception e) {
                log.error("Error while capturing packet", e);
                stop();
            }
        });
    }

    @SneakyThrows
    public void stop() {
        if (pcap != null && pcap.isOpen()) {
            pcap.breakLoop();
            pcap.close();
        }

        log.info("Intercept stopped");
    }

    @Override
    public String getExecutorState() {
        return processorExecutorService.toString();
    }
}
