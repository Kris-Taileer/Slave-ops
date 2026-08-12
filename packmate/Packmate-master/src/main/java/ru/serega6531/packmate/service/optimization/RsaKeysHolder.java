package ru.serega6531.packmate.service.optimization;

import lombok.SneakyThrows;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Service;

import java.io.File;
import java.io.IOException;
import java.math.BigInteger;
import java.nio.file.Files;
import java.security.KeyFactory;
import java.security.NoSuchAlgorithmException;
import java.security.interfaces.RSAPrivateKey;
import java.security.spec.InvalidKeySpecException;
import java.security.spec.PKCS8EncodedKeySpec;
import java.util.Base64;
import java.util.HashMap;
import java.util.Map;
import java.util.Objects;

@Service
@Slf4j
public class RsaKeysHolder {

    // Key: N from RSA public key
    private final Map<BigInteger, RSAPrivateKey> keys = new HashMap<>();

    public RSAPrivateKey getKey(BigInteger modulus) {
        return keys.get(modulus);
    }

    @EventListener(ApplicationReadyEvent.class)
    public void afterStartup(ApplicationReadyEvent event) {
        log.info("Loading RSA keys...");
        File dir = new File("rsa_keys");
        if (dir.exists() && dir.isDirectory()) {
            for (File keyFile : Objects.requireNonNull(dir.listFiles())) {
                addKey(keyFile);
            }
        }
    }

    @SneakyThrows
    public void addKey(File keyFile) {
        if (!keyFile.exists()) {
            throw new IllegalArgumentException("Key file does not exist");
        }

        try {
            RSAPrivateKey privateKey = loadFromFile(keyFile);
            keys.put(privateKey.getModulus(), privateKey);
            String n = privateKey.getModulus().toString();
            log.info("Loaded RSA key with N={}...", n.substring(0, Math.min(n.length(), 8)));
        } catch (IOException | InvalidKeySpecException e) {
            log.error("Error loading rsa key", e);
        }
    }

    private RSAPrivateKey loadFromFile(File keyFile) throws IOException, InvalidKeySpecException, NoSuchAlgorithmException {
        String content = Files.readString(keyFile.toPath());

        content = content.replaceAll("-----BEGIN (RSA )?PRIVATE KEY-----", "")
                .replaceAll("-----END (RSA )?PRIVATE KEY-----", "")
                .replace("\n", "");

        byte[] keyBytes = Base64.getDecoder().decode(content);

        PKCS8EncodedKeySpec spec = new PKCS8EncodedKeySpec(keyBytes);
        KeyFactory kf = KeyFactory.getInstance("RSA");
        return (RSAPrivateKey) kf.generatePrivate(spec);
    }

}
