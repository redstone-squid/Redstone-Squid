plugins {
    `java-library`
    alias(libs.plugins.kotlin.jvm)
}

dependencies {
    api(project(":core"))

    testImplementation(libs.junit.jupiter)
    testImplementation(kotlin("test"))
}
