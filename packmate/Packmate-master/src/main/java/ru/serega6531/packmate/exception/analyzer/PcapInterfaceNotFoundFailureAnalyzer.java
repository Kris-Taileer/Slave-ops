package ru.serega6531.packmate.exception.analyzer;

import org.springframework.boot.diagnostics.AbstractFailureAnalyzer;
import org.springframework.boot.diagnostics.FailureAnalysis;
import ru.serega6531.packmate.exception.PcapInterfaceNotFoundException;

public class PcapInterfaceNotFoundFailureAnalyzer extends AbstractFailureAnalyzer<PcapInterfaceNotFoundException> {
    @Override
    protected FailureAnalysis analyze(Throwable rootFailure, PcapInterfaceNotFoundException cause) {
        return new FailureAnalysis(
                "The interface \"" + cause.getRequestedInterface() + "\" was not found",
                "Check the interface name in the config. Existing interfaces are: " + cause.getExistingInterfaces(),
                cause
        );
    }
}
