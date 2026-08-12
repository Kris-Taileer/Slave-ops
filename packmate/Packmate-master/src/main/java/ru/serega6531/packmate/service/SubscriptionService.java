package ru.serega6531.packmate.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.SneakyThrows;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.sockjs.SockJsTransportFailureException;
import ru.serega6531.packmate.model.pojo.SubscriptionMessage;

import java.io.IOException;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.CopyOnWriteArrayList;

@Service
@Slf4j
public class SubscriptionService {

    private final List<WebSocketSession> subscribers = new CopyOnWriteArrayList<>();

    private final ObjectMapper mapper;

    @Autowired
    public SubscriptionService(ObjectMapper mapper) {
        this.mapper = mapper;
    }

    public void addSubscriber(WebSocketSession session) {
        subscribers.add(session);
        log.info("User subscribed: {} ({} {})",
                Objects.requireNonNull(session.getRemoteAddress()).getHostName(),
                session.getClass().getSimpleName(),
                session.getId());
    }

    public void removeSubscriber(WebSocketSession session) {
        subscribers.remove(session);
        log.info("User unsubscribed: {} ({})",
                Objects.requireNonNull(session.getRemoteAddress()).getHostName(),
                session.getId());
    }

    /**
     * Вызов потокобезопасный
     */
    @SneakyThrows
    public void broadcast(SubscriptionMessage message) {
        final TextMessage messageJson = objectToTextMessage(message);
        subscribers.forEach(s -> {
            try {
                s.sendMessage(messageJson);
            } catch (IOException | SockJsTransportFailureException e) {
                log.warn("WS", e);
            } catch (IllegalStateException ignored) {
                // очередь сообщений заполнилась, сообщение не доставится
            }
        });
    }

    private TextMessage objectToTextMessage(SubscriptionMessage object) throws JsonProcessingException {
        return new TextMessage(mapper.writeValueAsString(object));
    }

}
