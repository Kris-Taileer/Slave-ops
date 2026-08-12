package ru.serega6531.packmate.service;

import jakarta.annotation.PostConstruct;
import lombok.extern.slf4j.Slf4j;
import org.modelmapper.ModelMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.annotation.Lazy;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import ru.serega6531.packmate.model.CtfService;
import ru.serega6531.packmate.model.FoundPattern;
import ru.serega6531.packmate.model.Pattern;
import ru.serega6531.packmate.model.enums.PatternActionType;
import ru.serega6531.packmate.model.enums.PatternDirectionType;
import ru.serega6531.packmate.model.enums.SubscriptionMessageType;
import ru.serega6531.packmate.model.pojo.PatternCreateDto;
import ru.serega6531.packmate.model.pojo.PatternDto;
import ru.serega6531.packmate.model.pojo.PatternUpdateDto;
import ru.serega6531.packmate.model.pojo.SubscriptionMessage;
import ru.serega6531.packmate.repository.PatternRepository;

import java.time.Instant;
import java.util.Collection;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;

@Service
@Slf4j
public class PatternService {

    private final PatternRepository repository;
    private final StreamService streamService;
    private final SubscriptionService subscriptionService;
    private final ModelMapper modelMapper;

    private final Map<Integer, Pattern> patterns = new HashMap<>();

    @Autowired
    public PatternService(PatternRepository repository,
                          @Lazy StreamService streamService,
                          SubscriptionService subscriptionService,
                          ModelMapper modelMapper) {
        this.repository = repository;
        this.streamService = streamService;
        this.subscriptionService = subscriptionService;
        this.modelMapper = modelMapper;
    }

    @PostConstruct
    public void init() {
        repository.findAll().forEach(p -> patterns.put(p.getId(), p));
        log.info("Loaded {} patterns", patterns.size());
    }

    public Pattern find(int id) {
        return patterns.get(id);
    }

    public Collection<Pattern> findAll() {
        return patterns.values();
    }

    public Set<FoundPattern> findMatches(byte[] bytes, CtfService service, PatternDirectionType directionType, PatternActionType actionType) {
        final List<Pattern> list = patterns.values().stream()
                .filter(pattern -> pattern.isEnabled() && !pattern.isDeleted())
                .filter(p -> p.getServiceId() == null || p.getServiceId().equals(service.getPort()))
                .filter(p -> p.getActionType() == actionType)
                .filter(p -> p.getDirectionType() == directionType || p.getDirectionType() == PatternDirectionType.BOTH)
                .toList();
        return new PatternMatcher(bytes, list).findMatches();
    }

    public Set<FoundPattern> matchOne(byte[] bytes, Pattern pattern) {
        return new PatternMatcher(bytes, List.of(pattern)).findMatches();
    }

    public void enable(int id, boolean enabled) {
        final Pattern pattern = find(id);
        if (pattern != null) {
            pattern.setEnabled(enabled);
            final Pattern saved = repository.save(pattern);
            patterns.put(id, saved);

            if (enabled) {
                log.info("Enabled pattern '{}' with value '{}'", pattern.getName(), pattern.getValue());
                subscriptionService.broadcast(new SubscriptionMessage(SubscriptionMessageType.ENABLE_PATTERN, id));
            } else {
                log.info("Disabled pattern '{}' with value '{}'", pattern.getName(), pattern.getValue());
                subscriptionService.broadcast(new SubscriptionMessage(SubscriptionMessageType.DISABLE_PATTERN, id));
            }
        }
    }

    public void delete(int id) {
        final Pattern pattern = find(id);
        if (pattern != null) {
            pattern.setDeleted(true);
            final Pattern saved = repository.save(pattern);
            patterns.put(id, saved);

            log.info("Deleted pattern '{}' with value '{}'", pattern.getName(), pattern.getValue());
            subscriptionService.broadcast(new SubscriptionMessage(SubscriptionMessageType.SAVE_PATTERN, toDto(saved)));
        }
    }

    @Transactional
    public PatternDto create(PatternCreateDto dto) {
        Pattern pattern = fromDto(dto);

        pattern.setEnabled(true);
        pattern.setDeleted(false);
        pattern.setSearchStartTimestamp(System.currentTimeMillis());

        Pattern saved = save(pattern);
        return toDto(saved);
    }

    @Transactional
    public PatternDto update(int id, PatternUpdateDto dto) {
        Pattern pattern = repository.findById(id).orElseThrow();

        modelMapper.map(dto, pattern);

        Pattern saved = save(pattern);
        return toDto(saved);
    }

    private Pattern save(Pattern pattern) {
        try {
            PatternMatcher.compilePattern(pattern);
        } catch (Exception e) {
            throw new IllegalArgumentException(e.getMessage());
        }

        final Pattern saved = repository.save(pattern);
        patterns.put(saved.getId(), saved);

        log.info("Added new pattern '{}' with value '{}'", pattern.getName(), pattern.getValue());
        subscriptionService.broadcast(new SubscriptionMessage(SubscriptionMessageType.SAVE_PATTERN, toDto(saved)));

        return saved;
    }

    public void lookBack(int id, int minutes) {
        final Pattern pattern = find(id);
        if (pattern != null && pattern.getActionType() == PatternActionType.FIND) {
            long end = pattern.getSearchStartTimestamp();
            long start = end - TimeUnit.MINUTES.toMillis(minutes);

            pattern.setSearchStartTimestamp(start);
            repository.save(pattern);

            log.info("Scanning for pattern '{}' between {} and {}", pattern.getName(),
                    Instant.ofEpochMilli(start), Instant.ofEpochMilli(end));
            streamService.processLookbackPattern(pattern, start, end);
        }
    }

    public Pattern fromDto(PatternCreateDto dto) {
        return modelMapper.map(dto, Pattern.class);
    }

    public PatternDto toDto(Pattern pattern) {
        return modelMapper.map(pattern, PatternDto.class);
    }
}
