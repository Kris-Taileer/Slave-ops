plugins {
    id("org.springframework.boot") version "3.0.6"
    id("java")
    id("io.spring.dependency-management") version "1.1.0"
}

group = "ru.serega6531"
version = "1.0-SNAPSHOT"

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

configurations {
    get("compileOnly").apply {
        extendsFrom(configurations.annotationProcessor.get())
    }
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.springframework.boot:spring-boot-starter-data-jpa")
    implementation("org.springframework.boot:spring-boot-starter-web")
    implementation("org.springframework.boot:spring-boot-starter-security")
    implementation("org.springframework.boot:spring-boot-starter-websocket")
    implementation("org.springframework.session:spring-session-core")
    implementation(group = "org.apache.commons", name = "commons-lang3", version = "3.12.0")
    implementation(group = "commons-io", name = "commons-io", version = "2.11.0")

    implementation("org.pcap4j:pcap4j-core:1.8.2")
    implementation("org.pcap4j:pcap4j-packetfactory-static:1.8.2")

    constraints {
        implementation("net.java.dev.jna:jna:5.13.0") {
            because("upgraded version required to run on MacOS")
            // https://stackoverflow.com/questions/70368863/unsatisfiedlinkerror-for-m1-macs-while-running-play-server-locally
        }
    }

    implementation(group = "com.google.guava", name = "guava", version = "31.1-jre")
    implementation(group = "org.java-websocket", name = "Java-WebSocket", version = "1.5.3")
    implementation(group = "org.bouncycastle", name = "bcprov-jdk15on", version = "1.70")
    implementation(group = "org.bouncycastle", name = "bctls-jdk15on", version = "1.70")
    implementation(group = "org.modelmapper", name = "modelmapper", version = "3.1.1")
    implementation("com.athaydes.rawhttp:rawhttp-core:2.5.2")

    compileOnly("org.jetbrains:annotations:24.0.1")
    compileOnly("org.projectlombok:lombok")
    runtimeOnly("org.springframework.boot:spring-boot-devtools")
    runtimeOnly("org.postgresql:postgresql")
    annotationProcessor("org.projectlombok:lombok")
    testImplementation("org.junit.jupiter:junit-jupiter:5.9.2")
}

tasks.getByName<Test>("test") {
    useJUnitPlatform()
}