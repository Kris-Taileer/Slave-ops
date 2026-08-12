package ru.serega6531.packmate;

import org.junit.jupiter.api.Assertions;
import org.junit.jupiter.api.Test;
import ru.serega6531.packmate.model.FoundPattern;
import ru.serega6531.packmate.model.Pattern;
import ru.serega6531.packmate.model.enums.PatternSearchType;
import ru.serega6531.packmate.service.PatternMatcher;

import java.util.List;
import java.util.Set;

public class PatternMatcherTest {

    @Test
    public void testRegex() {
        String content = "ahkfkyafceffek";

        Set<FoundPattern> correctMatches = Set.of(
                FoundPattern.builder()
                        .startPosition(6)
                        .endPosition(8)
                        .build(),
                FoundPattern.builder()
                        .startPosition(9)
                        .endPosition(11)
                        .build());

        final Pattern pattern = new Pattern();
        pattern.setId(1);
        pattern.setValue("[a-f]{3}");
        pattern.setSearchType(PatternSearchType.REGEX);

        final PatternMatcher matcher = new PatternMatcher(content.getBytes(), List.of(pattern));
        final Set<FoundPattern> matches = matcher.findMatches();

        assertMatchesAreCorrect(correctMatches, matches);
    }

    @Test
    public void testSubstring() {
        String content = "abaabbaaabaabbbbbbbaaabaaa";
        Set<FoundPattern> correctMatches = Set.of(
                FoundPattern.builder()
                        .startPosition(12)
                        .endPosition(14)
                        .build(),
                FoundPattern.builder()
                        .startPosition(15)
                        .endPosition(17)
                        .build());

        final Pattern pattern = new Pattern();
        pattern.setId(1);
        pattern.setValue("bbb");
        pattern.setSearchType(PatternSearchType.SUBSTRING);

        final PatternMatcher matcher = new PatternMatcher(content.getBytes(), List.of(pattern));
        final Set<FoundPattern> matches = matcher.findMatches();

        assertMatchesAreCorrect(correctMatches, matches);
    }

    @Test
    public void testSubbytes() {
        byte[] content = new byte[]{0x11, (byte) 0xAA, (byte) 0xAA, (byte) 0xAA, (byte) 0xAA, 0x22};
        Set<FoundPattern> correctMatches = Set.of(
                FoundPattern.builder()
                        .startPosition(1)
                        .endPosition(2)
                        .build(),
                FoundPattern.builder()
                        .startPosition(3)
                        .endPosition(4)
                        .build());

        final Pattern pattern = new Pattern();
        pattern.setId(1);
        pattern.setValue("AAaa");
        pattern.setSearchType(PatternSearchType.SUBBYTES);

        final PatternMatcher matcher = new PatternMatcher(content, List.of(pattern));
        final Set<FoundPattern> matches = matcher.findMatches();

        assertMatchesAreCorrect(correctMatches, matches);
    }

    private void assertMatchesAreCorrect(Set<FoundPattern> correctMatches, Set<FoundPattern> foundMatches) {
        Assertions.assertEquals(correctMatches.size(), foundMatches.size());

        Assertions.assertTrue(correctMatches.stream().allMatch(correct ->
                foundMatches.stream().anyMatch(found -> matchesEqual(correct, found))
        ));
    }

    private boolean matchesEqual(FoundPattern one, FoundPattern two) {
        return one.getStartPosition() == two.getStartPosition() &&
                one.getEndPosition() == two.getEndPosition();
    }

}
