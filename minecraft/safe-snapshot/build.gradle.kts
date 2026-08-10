plugins {
    `java-library`
    alias(libs.plugins.kotlin.jvm)
}

dependencies {
    testImplementation(libs.junit.jupiter)
    testImplementation(kotlin("test"))
}
