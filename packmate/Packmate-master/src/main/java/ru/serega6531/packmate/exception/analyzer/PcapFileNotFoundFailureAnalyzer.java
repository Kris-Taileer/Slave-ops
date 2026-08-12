package ru.serega6531.packmate.exception.analyzer;

import org.springframework.boot.diagnostics.AbstractFailureAnalyzer;
import org.springframework.boot.diagnostics.FailureAnalysis;
import ru.serega6531.packmate.exception.PcapFileNotFoundException;

import java.io.File;
import java.util.Arrays;
import java.util.List;

public class PcapFileNotFoundFailureAnalyzer extends AbstractFailureAnalyzer<PcapFileNotFoundException> {
    @Override
    protected FailureAnalysis analyze(Throwable rootFailure, PcapFileNotFoundException cause) {
        String description = "The file " + cause.getFile().getAbsolutePath() + " was not found";
        String existingFilesMessage;

        File[] existingFiles = cause.getDirectory().listFiles();

        if (existingFiles == null) {
            return new FailureAnalysis(
                    description,
                    "Make sure you've put the pcap file to the ./pcaps directory, not the root directory. " +
                            "The directory currently does not exist",
                    cause
            );
        }

        if (existingFiles.length == 0) {
            existingFilesMessage = "The pcaps directory is currently empty";
        } else {
            List<String> existingFilesNames = Arrays.stream(existingFiles).map(File::getName).toList();
            existingFilesMessage = "The files present in " + cause.getDirectory().getAbsolutePath() + " are: " + existingFilesNames;
        }

        return new FailureAnalysis(
                description,
                "Please verify the file name. Make sure you've put the pcap file to the ./pcaps directory, not the root directory.\n" +
                        existingFilesMessage,
                cause
        );
    }
}
