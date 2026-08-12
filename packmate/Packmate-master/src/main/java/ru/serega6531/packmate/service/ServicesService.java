package ru.serega6531.packmate.service;

import jakarta.annotation.PostConstruct;
import lombok.extern.slf4j.Slf4j;
import org.modelmapper.ModelMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import ru.serega6531.packmate.properties.PackmateProperties;
import ru.serega6531.packmate.model.CtfService;
import ru.serega6531.packmate.model.enums.SubscriptionMessageType;
import ru.serega6531.packmate.model.pojo.ServiceCreateDto;
import ru.serega6531.packmate.model.pojo.ServiceDto;
import ru.serega6531.packmate.model.pojo.ServiceUpdateDto;
import ru.serega6531.packmate.model.pojo.SubscriptionMessage;
import ru.serega6531.packmate.repository.ServiceRepository;

import java.net.InetAddress;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Service
@Slf4j
public class ServicesService {

    private final ServiceRepository repository;
    private final SubscriptionService subscriptionService;
    private final PcapService pcapService;

    private final InetAddress localIp;

    private final Map<Integer, CtfService> services = new HashMap<>();
    private final ModelMapper modelMapper;

    @Autowired
    public ServicesService(ServiceRepository repository,
                           SubscriptionService subscriptionService,
                           @Lazy PcapService pcapService,
                           ModelMapper modelMapper,
                           PackmateProperties properties) {
        this.repository = repository;
        this.subscriptionService = subscriptionService;
        this.pcapService = pcapService;
        this.modelMapper = modelMapper;
        this.localIp = properties.localIp();
    }

    @PostConstruct
    public void init() {
        repository.findAll().forEach(s -> services.put(s.getPort(), s));
        log.info("Loaded {} services", services.size());
    }

    public CtfService find(int id) {
        return services.get(id);
    }

    public Optional<CtfService> findService(InetAddress firstIp, int firstPort, InetAddress secondIp, int secondPort) {
        if (firstIp.equals(localIp)) {
            return findByPort(firstPort);
        } else if (secondIp.equals(localIp)) {
            return findByPort(secondPort);
        }

        return Optional.empty();
    }

    private Optional<CtfService> findByPort(int port) {
        return Optional.ofNullable(services.get(port));
    }

    public List<ServiceDto> findAll() {
        return services.values()
                .stream()
                .map(this::toDto)
                .toList();
    }

    public void deleteByPort(int port) {
        log.info("Removed service at port {}", port);

        services.remove(port);
        repository.deleteById(port);

        subscriptionService.broadcast(new SubscriptionMessage(SubscriptionMessageType.DELETE_SERVICE, port));

        updateFilter();
    }

    @Transactional
    public ServiceDto create(ServiceCreateDto dto) {
        if (repository.existsById(dto.getPort())) {
            throw new IllegalArgumentException("Service already exists");
        }

        CtfService service = fromDto(dto);

        log.info("Added service '{}' at port {}", service.getName(), service.getPort());

        return save(service);
    }

    @Transactional
    public ServiceDto update(int port, ServiceUpdateDto dto) {
        CtfService service = repository.findById(port).orElseThrow();

        log.info("Edited service '{}' at port {}", service.getName(), service.getPort());

        modelMapper.map(dto, service);
        service.setPort(port);
        return save(service);
    }

    private ServiceDto save(CtfService service) {
        final CtfService saved = repository.save(service);
        services.put(saved.getPort(), saved);

        subscriptionService.broadcast(new SubscriptionMessage(SubscriptionMessageType.SAVE_SERVICE, toDto(saved)));

        updateFilter();

        return toDto(saved);
    }

    public void updateFilter() {
        pcapService.updateFilter(findAll());
    }

    private ServiceDto toDto(CtfService service) {
        return modelMapper.map(service, ServiceDto.class);
    }

    private CtfService fromDto(ServiceCreateDto dto) {
        return modelMapper.map(dto, CtfService.class);
    }

}
