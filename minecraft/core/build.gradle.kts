plugins {
    `java-library`
    alias(libs.plugins.kotlin.jvm)
}

dependencies {
    api(project(":protocol"))
    api(libs.brigadier)

    testImplementation(libs.junit.jupiter)
    testImplementation(kotlin("test"))
}
