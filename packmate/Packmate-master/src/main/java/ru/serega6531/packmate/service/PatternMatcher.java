package ru.serega6531.packmate.service;

import lombok.SneakyThrows;
import org.springframework.security.crypto.codec.Hex;
import ru.serega6531.packmate.model.FoundPattern;
import ru.serega6531.packmate.model.Pattern;
import ru.serega6531.packmate.utils.KMPByteSearcher;
import ru.serega6531.packmate.utils.KMPStringSearcher;

import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.io.StringReader;
import java.util.*;
import java.util.regex.Matcher;

public class PatternMatcher {

    private static final Map<String, java.util.regex.Pattern> compiledRegexes = new HashMap<>();
    private static final Map<String, KMPStringSearcher> compiledStringKmps = new HashMap<>();
    private static final Map<String, KMPByteSearcher> compiledByteKmps = new HashMap<>();

    private final byte[] contentBytes;
    private final String content;
    private final List<Pattern> patterns;

    private final Set<FoundPattern> result = new HashSet<>();

    public PatternMatcher(byte[] contentBytes, List<Pattern> patterns) {
        this.contentBytes = contentBytes;
        this.content = new String(contentBytes);
        this.patterns = patterns;
    }

    public Set<FoundPattern> findMatches() {
        patterns.forEach(this::match);
        return result;
    }

    private void match(Pattern pattern) {
        switch (pattern.getSearchType()) {
            case REGEX -> matchRegex(pattern);
            case SUBSTRING -> matchSubstring(pattern);
            case SUBBYTES -> matchSubbytes(pattern);
        }
    }

    private void matchRegex(Pattern pattern) {
        final var regex = compileRegex(pattern);
        final Matcher matcher = regex.matcher(content);
        int startPos = 0;

        while (matcher.find(startPos)) {
            addIfPossible(FoundPattern.builder()
                    .patternId(pattern.getId())
                    .startPosition(matcher.start())
                    .endPosition(matcher.end() - 1)
                    .build());
            startPos = matcher.end();
        }
    }

    private void matchSubstring(Pattern pattern) {
        final String value = pattern.getValue();
        KMPStringSearcher searcher = compileStringKMP(pattern);
        StringReader reader = new StringReader(content);

        while (true) {
            int end = searcher.search(reader) - 1;

            if (end < 0) {
                searcher.reset();
                return;
            }

            int start = end - value.length() + 1;
            addIfPossible(FoundPattern.builder()
                    .patternId(pattern.getId())
                    .startPosition(start)
                    .endPosition(end)
                    .build());
        }
    }

    @SneakyThrows
    private void matchSubbytes(Pattern pattern) {
        final byte[] value = Hex.decode(pattern.getValue());
        KMPByteSearcher searcher = compileByteKMP(pattern);
        InputStream is = new ByteArrayInputStream(contentBytes);

        while (true) {
            int end = searcher.search(is) - 1;

            if (end < 0) {
                searcher.reset();
                return;
            }

            int start = end - value.length + 1;
            addIfPossible(FoundPattern.builder()
                    .patternId(pattern.getId())
                    .startPosition(start)
                    .endPosition(end)
                    .build());
        }
    }

    private void addIfPossible(FoundPattern found) {
        if (result.stream().noneMatch(match ->
                between(match.getStartPosition(), match.getEndPosition(), found.getStartPosition()) ||
                        between(match.getStartPosition(), match.getEndPosition(), found.getEndPosition()))) {
            result.add(found);
        }
    }

    private boolean between(int a, int b, int x) {
        return a <= x && x <= b;
    }

    static void compilePattern(Pattern pattern) {
        switch (pattern.getSearchType()) {
            case REGEX -> compileRegex(pattern);
            case SUBSTRING -> compileStringKMP(pattern);
            case SUBBYTES -> compileByteKMP(pattern);
        }
    }

    private static java.util.regex.Pattern compileRegex(Pattern pattern) {
        return compiledRegexes.computeIfAbsent(pattern.getValue(), java.util.regex.Pattern::compile);
    }

    private static KMPStringSearcher compileStringKMP(Pattern pattern) {
        return compiledStringKmps.computeIfAbsent(pattern.getValue(), val -> new KMPStringSearcher(val.toCharArray()));
    }

    private static KMPByteSearcher compileByteKMP(Pattern pattern) {
        return compiledByteKmps.computeIfAbsent(pattern.getValue(), val -> new KMPByteSearcher(Hex.decode(val)));
    }

}
