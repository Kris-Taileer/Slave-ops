package ru.serega6531.packmate.configuration;

import lombok.extern.slf4j.Slf4j;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.event.EventListener;
import org.springframework.security.authentication.event.AuthenticationFailureBadCredentialsEvent;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetails;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.security.web.SecurityFilterChain;
import ru.serega6531.packmate.properties.PackmateProperties;

@Configuration
@EnableWebSecurity
@Slf4j
public class SecurityConfiguration {

    @Bean
    public InMemoryUserDetailsManager userDetailsService(PackmateProperties properties, PasswordEncoder passwordEncoder) {
        UserDetails user = User.builder()
                .username(properties.web().accountLogin())
                .password(passwordEncoder.encode(properties.web().accountPassword()))
                .roles("USER")
                .build();

        return new InMemoryUserDetailsManager(user);
    }

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        return http.csrf()
                .disable()
                .authorizeHttpRequests((auth) ->
                        auth.requestMatchers("/site.webmanifest")
                                .permitAll()
                                .anyRequest()
                                .authenticated()
                )
                .httpBasic()
                .and()
                .headers()
                .frameOptions()
                .sameOrigin()
                .and()
                .build();
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @EventListener
    public void authenticationFailed(AuthenticationFailureBadCredentialsEvent e) {
        log.info("Login failed for user {}, password {}",
                e.getAuthentication().getPrincipal(), e.getAuthentication().getCredentials());
    }

}
